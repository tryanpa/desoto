import json

import pytest
from fastapi.testclient import TestClient

from desoto.app import create_app
from desoto.db import TripLog
from desoto.state import PassState

ROSTER = {"1": [{"name": "Ava Johnson", "id": "1234567"}, {"name": "Ben Carter", "id": "7654321"}]}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.delenv("DESOTO_ADMIN_PIN", raising=False)

    def make(pin=None):
        if pin:
            monkeypatch.setenv("DESOTO_ADMIN_PIN", pin)
        state = PassState({})
        state.set_roster(ROSTER)
        paths = dict(settings_path=tmp_path / "s.json", log=TripLog(), roster_path=tmp_path / "r.json")
        return TestClient(create_app(state, **paths)), state, paths
    return make


def test_settings_save_and_reload(env):
    c, _, p = env()
    assert c.put("/api/admin/settings", json={"bar_style": "pill"}).status_code == 200
    assert json.loads(p["settings_path"].read_text())["bar_style"] == "pill"
    assert c.put("/api/admin/settings", json={"bar_style": "x"}).status_code == 422
    c2 = TestClient(create_app(PassState({}), **p))
    assert c2.get("/api/state").json()["settings"]["bar_style"] == "pill"


def test_pin_protects_every_admin_route(env):
    c, _, _ = env("1234")
    assert c.get("/api/auth").json() == {"pin_required": True}
    for method, url in [("get", "/api/admin/roster"), ("get", "/api/admin/log"), ("get", "/api/admin/stats"),
                        ("get", "/api/admin/log.csv"), ("post", "/api/admin/queue/clear")]:
        assert getattr(c, method)(url).status_code == 401, url
        assert getattr(c, method)(url, headers={"X-Admin-Pin": "nope"}).status_code == 401
    assert c.get("/api/admin/roster", headers={"X-Admin-Pin": "1234"}).status_code == 200
    assert c.get("/api/state").status_code == 200  # kiosk/display stay open


def test_ids_never_reach_kiosk_or_display(env):
    c, state, _ = env()
    c.post("/api/queue", json={"period": "1", "student": "Ava Johnson"})
    c.post("/api/sim/docked", json={"docked": False})
    c.post("/api/queue", json={"period": "1", "student": "Ben Carter"})
    with c.websocket_connect("/ws") as ws:
        blobs = [json.dumps(ws.receive_json()), c.get("/api/state").text, json.dumps(state.snapshot())]
    for b in blobs:
        assert "1234567" not in b and "7654321" not in b
    assert "1234567" in c.get("/api/admin/roster").text  # admins do see them
    for f in ("kiosk.html", "display.html", "common.js"):
        text = (__import__("pathlib").Path("desoto/static") / f).read_text()
        assert "student_id" not in text and ".id" not in text.replace("hidden", "")


def test_trip_is_logged_with_id_and_exported(env):
    c, _, _ = env()
    c.post("/api/queue", json={"period": "1", "student": "Ava Johnson"})
    c.post("/api/sim/docked", json={"docked": False})
    row = c.get("/api/admin/log").json()["rows"][0]
    assert row["name"] == "Ava Johnson" and row["student_id"] == "1234567" and row["status"] == "out"
    c.post("/api/sim/docked", json={"docked": True})
    row = c.get("/api/admin/log").json()["rows"][0]
    assert row["status"] == "done" and row["in_at"] >= row["out_at"]
    text = c.get("/api/admin/log.csv").text
    assert "Ava Johnson" in text and "1234567" in text and text.splitlines()[0].lstrip("﻿").startswith("Date,Period")
    assert c.get("/api/admin/stats").json()["overall"]["trips"] == 1
    assert "Ava Johnson" in c.get("/api/admin/stats.csv").text


def test_clear_queue_and_manual_return(env):
    c, state, _ = env()
    for n in ("Ava Johnson", "Ben Carter"):
        c.post("/api/queue", json={"period": "1", "student": n})
    assert c.post("/api/admin/queue/clear").json() == {"cleared": 2} and state.queue == []
    assert c.post("/api/admin/pass/return").status_code == 409
    c.post("/api/sim/docked", json={"docked": False})
    assert c.post("/api/admin/pass/return").status_code == 200
    assert c.get("/api/admin/log").json()["rows"][0]["source"] == "manual"


CSV = "Name,ID,Period\nZoe Park,9,1\nYan Wu,8,2\n"


def test_import_preview_then_apply_and_persist(env):
    c, state, p = env()
    body = {"csv": CSV, "mode": "replace", "preview": True}
    r = c.post("/api/admin/roster/import", json=body).json()
    assert r["ok"] and not r["applied"] and r["total"] == 2 and r["removed"] == 2
    assert state.roster == {"1": ["Ava Johnson", "Ben Carter"]}  # preview changes nothing
    c.post("/api/admin/roster/import", json=dict(body, preview=False))
    assert state.roster == {"1": ["Zoe Park"], "2": ["Yan Wu"]}
    assert json.loads(p["roster_path"].read_text())["2"] == [{"name": "Yan Wu", "id": "8"}]
    assert c.get("/api/state").json()["roster"] == {"1": ["Zoe Park"], "2": ["Yan Wu"]}


def test_import_merge_and_errors_and_queue_cleanup(env):
    c, state, _ = env()
    c.post("/api/queue", json={"period": "1", "student": "Ava Johnson"})
    c.post("/api/admin/roster/import", json={"csv": CSV, "mode": "merge", "preview": False})
    assert state.roster["1"] == ["Ava Johnson", "Ben Carter", "Zoe Park"] and state.queue
    c.post("/api/admin/roster/import", json={"csv": CSV, "mode": "replace", "preview": False})
    assert state.queue == []  # Ava is gone from the roster, so she leaves the queue
    bad = c.post("/api/admin/roster/import", json={"csv": "Foo,Bar\n1,2\n"})
    assert bad.status_code == 422 and bad.json()["errors"]


def test_add_and_remove_student(env):
    c, state, _ = env()
    assert c.post("/api/admin/roster/student", json={"period": "Period 2", "name": " New  Kid ", "id": "5"}).status_code == 200
    assert state.roster["2"] == ["New Kid"] and state.ids[("2", "New Kid")] == "5"
    assert c.post("/api/admin/roster/student", json={"period": "2", "name": "New Kid"}).status_code == 409
    assert c.delete("/api/admin/roster/student", params={"period": "2", "name": "New Kid"}).status_code == 200
    assert "2" not in state.roster
    assert c.delete("/api/admin/roster/student", params={"period": "2", "name": "Nope"}).status_code == 404


def test_csv_formula_injection_is_defused(env):
    c, state, _ = env()
    state.set_roster({"1": [{"name": "=HYPERLINK(\"x\")", "id": "1"}]})
    c.post("/api/queue", json={"period": "1", "student": "=HYPERLINK(\"x\")"})
    c.post("/api/sim/docked", json={"docked": False})
    assert "'=HYPERLINK" in c.get("/api/admin/log.csv").text


def test_simulator_setting_gates_the_sim_endpoint(env):
    c, state, p = env()
    state.sensor_configured = True
    assert c.post("/api/sim/docked", json={"docked": False}).status_code == 200
    assert c.put("/api/admin/settings", json={"simulator": "off"}).status_code == 200
    assert c.post("/api/sim/docked", json={"docked": True}).status_code == 403
    assert c.get("/api/state").json()["settings"]["simulator"] == "off"
    assert json.loads(p["settings_path"].read_text())["simulator"] == "off"
    assert c.put("/api/admin/settings", json={}).status_code == 422
    assert c.put("/api/admin/settings", json={"simulator": "x"}).status_code == 422
    c.put("/api/admin/settings", json={"simulator": "on"})
    assert c.post("/api/sim/docked", json={"docked": True}).status_code == 200


def test_cannot_turn_simulator_off_without_a_sensor(env):
    c, state, _ = env()
    assert not state.sensor_configured
    assert c.put("/api/admin/settings", json={"simulator": "off"}).status_code == 409
    assert state.simulator_on


def test_switching_to_sensor_catches_up_with_it(env):
    c, state, _ = env()
    state.sensor_configured = True

    class Fake:  # the pass is physically sitting in its dock
        docked = True
    c.app.state.sensor = Fake()
    c.post("/api/sim/docked", json={"docked": False})  # simulator says it is out
    assert state.out
    c.put("/api/admin/settings", json={"simulator": "off"})
    assert state.out is None and state.docked


def test_sensor_test_endpoints(env):
    c, state, _ = env()
    r = c.get("/api/admin/sensor").json()
    assert r["configured"] is False and r["docked"] is None and r["events"] == []
    state.sensor_configured = True
    mon = c.app.state.monitor
    mon.record(True); mon.record(False)
    r = c.get("/api/admin/sensor").json()
    assert r["configured"] and r["simulator"] and r["docked"] is False and r["changes"] == 1 and len(r["events"]) == 2
    assert c.post("/api/admin/sensor/reset").status_code == 200
    assert c.get("/api/admin/sensor").json()["events"] == []


def test_sensor_test_needs_the_pin(env):
    c, _, _ = env("1234")
    assert c.get("/api/admin/sensor").status_code == 401
    assert c.post("/api/admin/sensor/reset").status_code == 401


# ------------------------------------------------------------------ scan mode
def scan_client(env):
    c, state, p = env()
    assert c.put("/api/admin/settings", json={"identify": "scan"}).status_code == 200
    return c, state, p


def test_scan_endpoint_gating_and_messages(env):
    c, state, _ = env()
    assert c.post("/api/scan", json={"id": "1234567"}).status_code == 403       # tap mode
    c, state, _ = scan_client(env)
    r = c.post("/api/scan", json={"id": "1234567"})
    body = r.json()
    assert r.status_code == 200 and body["result"] == "started" and body["student"] == "Ava Johnson"
    assert "1234567" not in r.text and "Ava Johnson" in body["message"]
    assert c.post("/api/scan", json={"id": "7654321"}).json()["result"] == "queued"
    nf = c.post("/api/scan", json={"id": "0000000"}).json()
    assert nf["result"] == "not_found" and "0000000" not in nf["message"]
    assert c.post("/api/scan", json={"id": "x" * 500}).status_code == 422


def test_tap_endpoints_locked_in_scan_mode(env):
    c, state, _ = scan_client(env)
    assert c.post("/api/queue", json={"period": "1", "student": "Ava Johnson"}).status_code == 403
    assert c.delete("/api/queue/1/Ava Johnson").status_code == 403
    assert c.post("/api/sim/docked", json={"docked": False}).status_code == 403
    assert state.queue == [] and state.out is None


def test_scan_rate_limit_returns_429(env):
    c, _, _ = scan_client(env)
    codes = [c.post("/api/scan", json={"id": f"000{i}"}).status_code for i in range(25)]
    assert codes[:20] == [200] * 20 and codes[20:] == [429] * 5
    assert c.post("/api/scan", json={"id": "1234567"}).status_code == 429


def test_cannot_change_how_students_identify_mid_trip(env):
    c, state, _ = scan_client(env)
    c.post("/api/scan", json={"id": "1234567"})
    assert c.put("/api/admin/settings", json={"identify": "tap"}).status_code == 409
    assert c.put("/api/admin/settings", json={"identify": "scan"}).status_code == 200   # unchanged value is fine
    c.post("/api/scan", json={"id": "1234567"})                                       # scan back in
    assert c.put("/api/admin/settings", json={"identify": "tap"}).status_code == 200
    assert c.put("/api/admin/settings", json={"call_timeout": "7"}).status_code == 422


def test_teacher_can_add_start_and_remove(env):
    c, state, _ = scan_client(env)
    assert c.post("/api/admin/queue/add", json={"period": "1", "student": "Ben Carter"}).status_code == 200
    assert state.queue == [("1", "Ben Carter")] and state.queue_method[("1", "Ben Carter")] == "teacher"
    assert c.post("/api/admin/queue/add", json={"period": "1", "student": "Ben Carter"}).status_code == 409
    assert c.delete("/api/admin/queue/1/Ben Carter").status_code == 200 and state.queue == []
    assert c.post("/api/admin/queue/add", json={"period": "1", "student": "Ava Johnson", "start": True}).status_code == 200
    assert state.out.student == ("1", "Ava Johnson") and state.out.method == "teacher"
    assert c.post("/api/admin/queue/start", json={"period": "1", "student": "Ben Carter"}).status_code == 409   # pass busy
    row = c.get("/api/admin/log").json()["rows"][0]
    assert row["method"] == "teacher" and row["student_id"] == "1234567"
    assert "Identified by" in c.get("/api/admin/log.csv").text and "teacher" in c.get("/api/admin/log.csv").text


def test_new_admin_endpoints_need_the_pin(env):
    c, _, _ = env("1234")
    for method, url, body in [("post", "/api/admin/queue/add", {"period": "1", "student": "Ava Johnson"}),
                              ("post", "/api/admin/queue/start", {"period": "1", "student": "Ava Johnson"}),
                              ("delete", "/api/admin/queue/1/Ava Johnson", None)]:
        r = getattr(c, method)(url, **({"json": body} if body else {}))
        assert r.status_code == 401, url

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
    assert json.loads(p["settings_path"].read_text()) == {"bar_style": "pill"}
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

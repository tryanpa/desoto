from datetime import date, datetime

from desoto.db import TripLog
from desoto.stats import compute


def ts(day, hh, mm=0):
    return datetime(2026, 3, day, hh, mm).timestamp()


def make_log():
    log = TripLog()
    spec = [("1", "Ava", "11", ts(2, 9), 300), ("1", "Ava", "11", ts(2, 13), 600), ("2", "Ben", "22", ts(3, 9), 120),
            ("", "Unqueued", "", ts(3, 10), 60)]
    for p, n, i, t, secs in spec:
        tid = log.open_trip(p, n, i, t, 420)
        log.close_trip(tid, t + secs)
    log.open_trip("1", "Ava", "11", ts(4, 9), 420)  # still out
    return log


def test_log_query_and_filters():
    log = make_log()
    rows, total = log.query()
    assert total == 5 and rows[0]["status"] == "out"
    rows, total = log.query(start="2026-03-02", end="2026-03-02")
    assert total == 2
    assert log.query(period="2")[1] == 1
    assert log.query(q="22")[1] == 1  # search by ID
    assert [r["over"] for r in log.query(start="2026-03-02", end="2026-03-02")[0]] == [True, False]


def test_startup_marks_open_trips_incomplete(tmp_path):
    p = tmp_path / "t.db"
    a = TripLog(p)
    a.open_trip("1", "A", "", 1.0, 420)
    b = TripLog(p)
    assert b.query()[0][0]["status"] == "incomplete"


def test_compute():
    log = make_log()
    rows, _ = log.query()
    roster = {"1": [{"name": "Ava", "id": "11"}, {"name": "Zed", "id": "99"}], "2": [{"name": "Ben", "id": "22"}]}
    s = compute(rows, roster, today=date(2026, 3, 6))
    o = s["overall"]
    assert (o["trips"], o["over"], o["unique_students"], o["unqueued"], o["longest_seconds"]) == (4, 1, 2, 1, 600)
    assert o["incomplete"] == 0 and o["over_pct"] == 25.0
    assert [d["date"] for d in s["daily"]] == ["2026-03-02", "2026-03-03"]
    assert s["hourly"][9]["trips"] == 2 and s["hourly"][13]["over"] == 1
    assert [(p["period"], p["trips"]) for p in s["by_period"]] == [("1", 2), ("2", 1)]
    by_name = {x["name"]: x for x in s["students"]}
    assert by_name["Ava"]["trips"] == 2 and by_name["Ava"]["over_seconds"] == 180
    assert by_name["Zed"]["trips"] == 0  # roster members with no trips are listed
    assert sum(by_name["Ava"]["weekly"]) == 2 and len(s["week_starts"]) == 12
    assert "Unqueued" not in by_name


def test_sensor_monitor():
    from desoto.sensor import SensorMonitor
    t = [100.0]
    m = SensorMonitor(maxlen=3, clock=lambda: t[0])
    assert m.snapshot()["docked"] is None and m.snapshot()["events"] == []
    m.record(True)                                   # starting state, not a change
    t[0] += 2.0; m.record(False)
    t[0] += 0.25; m.record(True)                     # a quick flicker
    t[0] += 5.0; m.record(False)
    s = m.snapshot()
    assert s["docked"] is False and s["changes"] == 3 and abs(s["min_gap"] - 0.25) < 1e-9
    assert len(s["events"]) == 3 and s["events"][0]["docked"] is False  # newest first, capped at maxlen
    m.reset()
    assert m.snapshot() == {"docked": None, "changes": 0, "min_gap": None, "events": []}

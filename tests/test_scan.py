import json
import sqlite3

from desoto.db import TripLog
from desoto.state import MAX_UNKNOWN_SCANS, PassState

ROSTER = {"1": [{"name": "Ava", "id": "1001"}, {"name": "Ben", "id": "1002"}, {"name": "Cy", "id": "1003"}],
          "2": [{"name": "Dee", "id": "2001"}]}


def make(scan=True, log=None):
    t = [1000.0]
    s = PassState({}, 420, clock=lambda: t[0], log=log)
    s.set_roster(ROSTER)
    if scan:
        s.set_setting("identify", "scan")
    return s, t


def test_find_student():
    s, _ = make()
    assert s.find_student("1001") == ("1", "Ava") and s.find_student("  1002 \n") == ("1", "Ben")
    assert s.find_student("https://card.example/?id=1003&x=1") == ("1", "Cy")   # QR with a URL around the ID
    assert s.find_student("1001 1002") is None       # two different IDs in one code: refuse to guess
    assert s.find_student("9999") is None and s.find_student("") is None and s.find_student(None) is None


def test_scan_off_in_tap_mode():
    s, _ = make(scan=False)
    assert s.scan("1001") == {"result": "scan_off"}


def test_full_flow():
    s, t = make()
    r = s.scan("1001")
    assert r["result"] == "started" and s.out.student == ("1", "Ava") and s.out.method == "scan"
    assert s.scan("1002")["result"] == "queued" and s.scan("2001") == {"student": "Dee", "period": "2", "result": "queued", "position": 2}
    w = s.scan("1002")                                # already queued, pass busy
    assert w["result"] == "waiting" and w["position"] == 1 and w["busy"] is True
    assert s.scan("2001")["next"] == "Ben"
    t[0] += 100
    r = s.scan("1001")                                # Ava comes back
    assert r["result"] == "returned" and r["seconds"] == 100 and r["over"] is False and s.out is None
    snap = s.snapshot()
    assert snap["called"]["student"] == "Ben" and snap["called"]["expires_at"] == t[0] + 60   # Ben must scan to start
    assert s.scan("2001")["result"] == "waiting" and s.out is None   # Dee is not next: cannot jump the line
    assert s.scan("1002")["result"] == "started" and s.queue == [("2", "Dee")]


def test_scan_when_pass_free_but_someone_is_called():
    s, _ = make()
    s.join("1", "Ava", "teacher")                     # teacher queued Ava, pass is free
    r = s.scan("1002")                                # Ben scans: must queue behind Ava, not take the pass
    assert r["result"] == "queued" and r["position"] == 2 and s.out is None


def test_over_time_return():
    s, t = make()
    s.scan("1001"); t[0] += 421
    assert s.scan("1001")["over"] is True


def test_call_timeout_skips_and_rolls_on():
    s, t = make()
    s.scan("1001"); s.scan("1002"); s.scan("1003")
    t[0] += 50; s.scan("1001")                        # Ava back; Ben is called
    assert s.tick() is False
    t[0] += 59; s.tick(); assert s.queue[0] == ("1", "Ben")
    t[0] += 2
    assert s.tick() is True
    assert s.queue == [("1", "Cy")] and "Ben" in s.snapshot()["notice"] and s.snapshot()["called"]["student"] == "Cy"
    assert s.scan("1002")["result"] == "queued"       # Ben can rejoin at the back
    t[0] += 11; s.tick(); assert s.snapshot()["notice"] is None or "Cy" in s.snapshot()["notice"]


def test_call_timeout_zero_never_skips():
    s, t = make()
    s.set_setting("call_timeout", "0")
    s.scan("1001"); s.scan("1002"); s.scan("1001")
    t[0] += 10_000
    s.tick()
    assert s.queue == [("1", "Ben")] and s.snapshot()["called"]["expires_at"] is None


def test_runaway_trip_is_abandoned(tmp_path):
    log = TripLog()
    s, t = make(log=log)
    s.scan("1001")
    t[0] += 30 * 60 + 1
    assert s.tick() is True and s.out is None
    row = log.query()[0][0]
    assert row["status"] == "incomplete" and row["method"] == "scan" and row["student_id"] == "1001"


def test_unknown_scans_are_rate_limited():
    s, t = make()
    for _ in range(MAX_UNKNOWN_SCANS):
        assert s.scan("0000")["result"] == "not_found"
    assert s.scan("1001")["result"] == "rate_limited" and s.out is None   # even a real ID waits
    t[0] += 61
    assert s.scan("1001")["result"] == "started"


def test_pass_hardware_ignored_in_scan_mode():
    s, _ = make()
    assert not s.from_sensor(False) and not s.from_simulator(False) and s.out is None


def test_teacher_start_and_methods_are_logged():
    log = TripLog()
    s, t = make(log=log)
    assert s.join("1", "Ben", "teacher") and s.start_now("1", "Ben") and s.out.method == "teacher"
    assert not s.start_now("1", "Cy")                 # pass is busy
    t[0] += 5; s.return_pass()
    s.scan("1001"); t[0] += 5; s.scan("1001")
    s.set_setting("identify", "tap"); s.set_setting("simulator", "on")
    s.join("1", "Cy"); s.from_simulator(False); t[0] += 5; s.from_simulator(True)
    methods = [(r["name"], r["method"]) for r in reversed(log.query()[0])]
    assert methods == [("Ben", "teacher"), ("Ava", "scan"), ("Cy", "tap")]


def test_queue_entries_keep_how_they_joined():
    s, _ = make(scan=False)
    s.join("1", "Ava")                                # tapped
    s.set_setting("identify", "scan")
    s.scan("1002")                                    # scanned
    assert s.queue_method == {("1", "Ava"): "tap", ("1", "Ben"): "scan"}
    s.set_roster({"1": [{"name": "Ben", "id": "1002"}]})
    assert s.queue == [("1", "Ben")] and list(s.queue_method) == [("1", "Ben")]


def test_snapshot_never_has_ids():
    s, _ = make()
    s.scan("1001"); s.scan("1002")
    blob = json.dumps(s.snapshot())
    for sid in ("1001", "1002", "1003", "2001"):
        assert sid not in blob


def test_old_log_gets_the_method_column(tmp_path):
    p = tmp_path / "old.db"
    db = sqlite3.connect(p)
    db.execute("CREATE TABLE trips(id INTEGER PRIMARY KEY AUTOINCREMENT, period TEXT NOT NULL, name TEXT NOT NULL,"
               " student_id TEXT NOT NULL DEFAULT '', out_at REAL NOT NULL, in_at REAL, allowed INTEGER NOT NULL,"
               " source TEXT NOT NULL DEFAULT 'sensor', incomplete INTEGER NOT NULL DEFAULT 0)")
    db.execute("INSERT INTO trips(period,name,out_at,in_at,allowed) VALUES('1','Old',1,60,420)")
    db.commit(); db.close()
    row = TripLog(p).query()[0][0]
    assert row["name"] == "Old" and row["method"] == "tap"


def test_hiding_test_controls_disables_the_simulator():
    s, _ = make(scan=False)
    assert s.from_simulator(False) and s.out is not None          # controls shown by default
    s.return_pass()
    assert s.set_setting("test_controls", "hide") and not s.set_setting("test_controls", "maybe")
    assert not s.from_simulator(False) and s.out is None           # hidden = refused, even with the simulator on
    assert s.snapshot()["settings"]["test_controls"] == "hide"


def test_pass_length_applies_from_the_next_trip():
    log = TripLog()
    s, t = make(log=log)
    assert s.settings["pass_seconds"] == "420" and s.duration == 420
    s.scan("1001")                                     # Ava starts on 7 minutes
    assert s.set_setting("pass_seconds", "300") and s.duration == 300
    assert s.snapshot()["duration"] == 420             # her trip keeps its own length
    t[0] += 350                                        # 5:50: over the NEW limit, but not her 7 minutes
    r = s.scan("1001")
    assert r["over"] is False and log.query()[0][0]["allowed"] == 420 and log.query()[0][0]["over"] is False
    assert s.snapshot()["duration"] == 300             # nobody out: the new length shows
    s.scan("1002"); t[0] += 350
    assert s.scan("1002")["over"] is True and log.query()[0][0]["allowed"] == 300


def test_pass_length_validation():
    s, _ = make()
    for bad in ("59", "3601", "abc", "", "7.5", "-60"):
        assert not s.set_setting("pass_seconds", bad), bad
    assert s.set_setting("pass_seconds", "60") and s.set_setting("pass_seconds", "3600") and s.set_setting("pass_seconds", "450")
    assert s.duration == 450

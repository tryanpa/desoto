from datetime import date, datetime

from desoto.bank import Bank, skey
from desoto.db import TripLog
from desoto.state import PassState

ROSTER = {"1": [{"name": "Ava", "id": "1001"}, {"name": "Ben", "id": "1002"}, {"name": "Cy", "id": "1003"}]}
NOON = datetime(2027, 1, 15, 12, 0, 0).timestamp()


def make(bank=True, scan=True):
    t = [NOON]
    log = TripLog()
    s = PassState({}, 420, clock=lambda: t[0], log=log)
    s.bank = Bank(log)
    s.set_roster(ROSTER)
    if scan:
        s.set_setting("identify", "scan")
    if bank:
        s.set_setting("bank_enabled", "on")
        s.set_setting("bank_size", "5")
    return s, t, log


def trip(s, t, sid, minutes):
    """Scan out, wait, scan back in. Returns the return-scan result."""
    assert s.scan(sid)["result"] == "started"
    t[0] += minutes * 60
    return s.scan(sid)


def remaining(s, who=("1", "Ava")):
    return s.bank_row(who)["remaining"]


def reasons(s, name="Ava", sid="1001"):
    return [(e["reason"], e["delta"], e["balance"]) for e in reversed(s.bank.ledger("1", name, sid))]


def test_bank_is_off_by_default():
    s, t, _ = make(bank=False)
    assert s.settings["bank_enabled"] == "off"
    trip(s, t, "1001", 30)                                   # very late, but nothing is counted
    assert s.blocked_reason(("1", "Ava")) is None and s.snapshot(kiosk=True)["late_notice"] is None
    assert s.bank.ledger("1", "Ava", "1001") == []


def test_every_trip_costs_one_pass():
    s, t, _ = make()
    assert remaining(s) == 5
    r = s.scan("1001")
    assert r["result"] == "started" and r["remaining"] == 4
    t[0] += 120
    assert s.scan("1001")["late"] is None and remaining(s) == 4
    assert reasons(s) == [("trip", -1, 4)]


def test_no_passes_left_blocks_until_a_teacher_adds_more():
    s, t, _ = make()
    s.bank.adjust("1", "Ava", "1001", -5, "adjust", t[0], 5, "used them up")
    assert s.scan("1001")["result"] == "no_passes" and s.out is None and s.queue == []
    assert s.blocked_reason(("1", "Ava")) == "no_passes"
    s.bank.adjust("1", "Ava", "1001", +2, "adjust", t[0], 5)
    assert s.scan("1001")["result"] == "started" and remaining(s) == 1


def test_teacher_override_starts_a_trip_even_at_zero_and_never_goes_negative():
    s, t, _ = make()
    s.bank.adjust("1", "Ava", "1001", -5, "adjust", t[0], 5)
    assert s.start_now("1", "Ava") and remaining(s) == 0 and s.out.method == "teacher"


def test_hold_blocks_and_is_kept_through_a_refill():
    s, t, _ = make()
    s.scan("1002"); s.scan("1001")                           # Ben out, Ava queued
    s.bank.set_hold("1", "Ava", "1001", True, "skipping class", t[0], 5)
    assert s.scan("1001")["result"] == "paused" and ("1", "Ava") not in s.queue   # removed from the line too
    assert s.blocked_reason(("1", "Ava")) == "paused" and s.bank_row(("1", "Ava"))["note"] == "skipping class"
    s.refill_banks()
    assert s.bank_row(("1", "Ava"))["hold"] is True          # a refill does not lift a hold
    s.bank.set_hold("1", "Ava", "1001", False, "", t[0], 5)
    assert s.blocked_reason(("1", "Ava")) is None


def test_tap_mode_blocking_reason_for_the_join_button():
    s, t, _ = make(scan=False)
    s.bank.set_hold("1", "Ben", "1002", True, "", t[0], 5)
    assert s.blocked_reason(("1", "Ben")) == "paused" and s.blocked_reason(("1", "Ava")) is None


def test_late_rule_warn_once_then_debit_each_time():
    s, t, _ = make()                                         # allowed 7:00, grace 30s, 1 free warning, debit 1
    assert trip(s, t, "1001", 7.4)["late"] is None           # 24 s over: inside the grace
    late = trip(s, t, "1001", 9)["late"]                     # 2:00 over: first late return -> warning only
    assert late["seconds"] == 120 and late["lates"] == 1 and late["debited"] == 0 and remaining(s) == 3
    notice = s.snapshot(kiosk=True)["late_notice"]
    assert notice["student"] == "Ava" and notice["debited"] == 0 and notice["seconds"] == 120 and "until" not in notice
    assert s.snapshot()["late_notice"] is None               # the hallway display never gets it
    late = trip(s, t, "1001", 8)["late"]                     # second late return -> a pass is taken
    assert late["lates"] == 2 and late["debited"] == 1 and late["remaining"] == remaining(s) == 1   # 3 - 1 (trip) - 1 (late)
    assert [r for r in reasons(s) if r[0].startswith("late")] == [("late", 0, 3), ("late", 0, 2), ("late debit", -1, 1)]


def test_late_settings_are_adjustable():
    s, t, _ = make()
    s.set_setting("late_warnings", "0"); s.set_setting("late_debit", "2"); s.set_setting("late_grace", "0")
    assert trip(s, t, "1001", 7.1)["late"]["debited"] == 2   # no free warning, no grace, debit 2
    s.set_setting("late_debit", "0")
    assert trip(s, t, "1001", 9)["late"]["debited"] == 0     # warn-only mode


def test_manual_return_is_never_a_penalty():
    s, t, _ = make()
    s.scan("1001"); t[0] += 3600
    assert s.return_pass() and s.snapshot(kiosk=True)["late_notice"] is None and s.bank_row(("1", "Ava"))["lates"] == 0


def test_sensor_return_counts_too():
    s, t, _ = make(scan=False)
    s.set_setting("simulator", "on")
    s.join("1", "Ava"); s.from_simulator(False)
    assert remaining(s) == 4                                 # the trip was charged when the pass left
    t[0] += 600; s.from_simulator(True)
    assert s.snapshot(kiosk=True)["late_notice"]["seconds"] == 180 and s.bank_row(("1", "Ava"))["lates"] == 1


def test_late_popup_expires():
    s, t, _ = make()
    trip(s, t, "1001", 9)
    assert s.snapshot(kiosk=True)["late_notice"] and s.tick() is False
    t[0] += 21
    assert s.tick() is True and s.snapshot(kiosk=True)["late_notice"] is None


def test_balance_never_goes_negative():
    s, t, _ = make()
    s.set_setting("bank_size", "1")
    s.bank.adjust("1", "Ava", "1001", 0, "adjust", t[0], 1)
    s.start_now("1", "Ava")                                  # uses the last pass
    assert remaining(s) == 0
    assert s.bank.adjust("1", "Ava", "1001", -3, "adjust", t[0], 1)["delta"] == 0


def test_grading_period_refills_once_and_again_for_the_next_period():
    s, t, _ = make()
    s.bank.adjust("1", "Ava", "1001", -3, "adjust", t[0], 5)                   # Ava is down to 2
    s.bank.add_late("1", "Ava", "1001", t[0], 5)
    assert s.tick() is False and remaining(s) == 2                             # no grading period defined: nothing happens
    pid = s.bank.add_period("Q2", "2027-01-10", "2027-03-20")
    s.recheck_periods()
    assert s.tick() is True and remaining(s) == 5 and s.bank_row(("1", "Ava"))["lates"] == 0
    s.bank.adjust("1", "Ava", "1001", -1, "adjust", t[0], 5)
    t[0] += 3600; s.tick()
    assert remaining(s) == 4                                                   # same period: no second refill
    s.bank.add_period("Q3", "2027-03-21", "2027-06-01")
    t[0] = datetime(2027, 3, 21, 8, 0).timestamp()                             # the next period begins
    assert s.tick() is True and remaining(s) == 5
    assert ("refill", 1, 5) in reasons(s)
    t[0] = datetime(2027, 8, 1, 8, 0).timestamp()                              # summer: no period covers the date
    s.bank.adjust("1", "Ava", "1001", -2, "adjust", t[0], 5)
    s.tick(); assert remaining(s) == 3


def test_manual_refill_and_ledger_notes():
    s, t, _ = make()
    s.bank.adjust("1", "Ben", "1002", -4, "adjust", t[0], 5, "lost his pass privileges for a day")
    assert s.refill_banks("Start of semester") == 3 and remaining(s, ("1", "Ben")) == 5
    led = s.bank.ledger("1", "Ben", "1002")
    assert led[0]["reason"] == "refill" and led[0]["note"] == "Start of semester" and led[1]["note"].startswith("lost")


def test_bank_follows_the_student_id_through_a_roster_reimport():
    s, t, _ = make()
    s.bank.adjust("1", "Ava", "1001", -2, "adjust", t[0], 5)
    s.set_roster({"3": [{"name": "Ava J.", "id": "1001"}]})                    # renamed and moved to another period
    assert s.bank_row(("3", "Ava J."))["remaining"] == 3 and skey("3", "x", "1001") == "1001"
    assert skey("1", "No Id", "") == "1|No Id"                                  # no ID: keyed by period and name


def test_new_numeric_settings_are_validated():
    s, _, _ = make()
    for key, good, bad in [("bank_size", "12", "0"), ("late_grace", "0", "601"), ("late_warnings", "3", "11"), ("late_debit", "2", "-1")]:
        assert s.set_setting(key, good) and not s.set_setting(key, bad) and not s.set_setting(key, "x"), key
    assert not s.set_setting("bank_enabled", "maybe")


def test_period_validation():
    s, _, _ = make()
    import pytest
    with pytest.raises(ValueError):
        s.bank.add_period("Bad", "2027-02-01", "2027-01-01")
    with pytest.raises(ValueError):
        s.bank.add_period("Bad", "not-a-date", "2027-01-01")

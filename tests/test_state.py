from desoto.state import PassState


def make():
    t = [1000.0]
    s = PassState({"1": ["A", "B"], "2": ["A", "C"]}, 420, clock=lambda: t[0])
    return s, t


def test_queue_rules():
    s, _ = make()
    assert s.join("1", "A") and not s.join("1", "A") and not s.join("1", "Z")
    assert not s.join("1", "C")  # C is in period 2, not 1
    assert s.join("2", "A")  # same name, different period
    assert s.leave("1", "A") and not s.leave("1", "A")


def test_take_and_return():
    s, t = make()
    s.join("1", "A"); s.join("1", "B")
    s.set_docked(False)
    assert s.out.student == ("1", "A") and s.queue == [("1", "B")]
    assert not s.join("1", "A")
    t[0] += 90
    s.set_docked(True)
    assert s.out is None and s.history[0]["seconds"] == 90 and s.history[0]["period"] == "1"


def test_unqueued_trip():
    s, _ = make()
    s.set_docked(False)
    assert s.out.student == ("", "Unqueued")


def test_settings():
    s, _ = make()
    assert s.settings["bar_style"] == "thick"
    assert s.set_setting("bar_style", "pill") and s.snapshot()["settings"]["bar_style"] == "pill"
    assert not s.set_setting("bar_style", "neon") and not s.set_setting("nope", "x")


def test_simulator_and_sensor_are_exclusive():
    s, _ = make()
    s.join("1", "A")
    assert s.simulator_on  # tests and laptops default to the simulator
    assert not s.from_sensor(False) and s.docked  # sensor ignored while simulating
    assert s.from_simulator(False) and s.out
    assert s.set_setting("simulator", "off") and not s.set_setting("simulator", "maybe")
    assert not s.from_simulator(True) and s.out  # simulator ignored while on the sensor
    assert s.from_sensor(True) and s.out is None

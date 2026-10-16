from desoto.state import PassState


def make():
    t = [1000.0]
    s = PassState(["A", "B", "C"], 420, clock=lambda: t[0])
    return s, t


def test_queue_rules():
    s, _ = make()
    assert s.join("A") and not s.join("A") and not s.join("Z")
    assert s.leave("A") and not s.leave("A")


def test_take_and_return():
    s, t = make()
    s.join("A"); s.join("B")
    s.set_docked(False)
    assert s.out.student == "A" and s.queue == ["B"]
    assert not s.join("A")
    t[0] += 90
    s.set_docked(True)
    assert s.out is None and s.history[0]["seconds"] == 90


def test_unqueued_trip():
    s, _ = make()
    s.set_docked(False)
    assert s.out.student == "Unqueued"

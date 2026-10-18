"""Pass sensors. A sensor reports docked (True) / undocked (False)."""
import asyncio
import threading
import time
from collections import deque
from typing import Callable, Deque, Optional

OnChange = Callable[[bool], None]


class GpioHallSensor:
    """Hall effect sensor on a GPIO pin. Magnet in the pass holds the pin low
    while the pass sits in the dock (typical open-collector sensor, pull-up)."""

    def __init__(self, pin: int, on_change: OnChange):
        from gpiozero import Button  # only available on the Pi
        self.button = Button(pin, pull_up=True, bounce_time=0.1)
        loop = asyncio.get_event_loop()
        self.button.when_pressed = lambda: loop.call_soon_threadsafe(on_change, True)
        self.button.when_released = lambda: loop.call_soon_threadsafe(on_change, False)
        on_change(self.button.is_pressed)

    @property
    def docked(self) -> bool:
        return bool(self.button.is_pressed)


class SensorMonitor:
    """Remembers what the sensor reports (whatever the simulator setting) for the admin Sensor test.

    The first reading is the sensor's starting state; every reading after that is a change. `gap` is the
    time since the previous reading, so a flickery sensor shows up as very short gaps.
    """

    def __init__(self, maxlen: int = 40, clock: Callable[[], float] = time.time):
        self.clock = clock
        self.lock = threading.Lock()
        self.events: Deque[dict] = deque(maxlen=maxlen)
        self.reset()

    def reset(self) -> None:
        with self.lock:
            self.events.clear()
            self.docked: Optional[bool] = None
            self.changes = 0
            self.min_gap: Optional[float] = None
            self._last: Optional[float] = None

    def record(self, docked: bool) -> None:
        now = self.clock()
        with self.lock:
            gap = None if self._last is None else now - self._last
            if gap is not None:
                self.changes += 1
                self.min_gap = gap if self.min_gap is None else min(self.min_gap, gap)
            self.events.append({"at": now, "docked": bool(docked), "gap": gap})
            self.docked, self._last = bool(docked), now

    def snapshot(self) -> dict:
        with self.lock:
            return {"docked": self.docked, "changes": self.changes, "min_gap": self.min_gap,
                    "events": list(reversed(self.events))}

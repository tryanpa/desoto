"""Pass sensors. A sensor reports docked (True) / undocked (False)."""
import asyncio
from typing import Callable

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

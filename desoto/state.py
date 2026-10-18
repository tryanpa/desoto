"""Pass state machine: a queue of students and at most one student out."""
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from .db import TripLog

DEFAULT_DURATION = 7 * 60
BAR_STYLES = ("thick", "pill")
ON_OFF = ("on", "off")

# A student is identified by (class period, name): names can repeat across periods.
Student = Tuple[str, str]


@dataclass
class Trip:
    student: Student
    started_at: float
    log_id: Optional[int] = None


@dataclass
class PassState:
    roster: Dict[str, List[str]]  # period -> names. Public: the kiosk shows these.
    duration: int = DEFAULT_DURATION
    clock: Callable[[], float] = time.time
    queue: List[Student] = field(default_factory=list)
    out: Optional[Trip] = None
    docked: bool = True
    history: List[dict] = field(default_factory=list)
    settings: Dict[str, str] = field(default_factory=lambda: {"bar_style": "thick", "simulator": "on"})
    sensor_configured: bool = False  # True when a real sensor (GPIO pin) is set up
    ids: Dict[Student, str] = field(default_factory=dict)  # PRIVATE: never put in snapshot()
    log: Optional[TripLog] = None

    # -- roster ------------------------------------------------------------
    def set_roster(self, full: Dict[str, List[dict]]) -> None:
        """Replace the roster ({period: [{name, id}]}); drop queued students who are gone."""
        self.roster = {p: [s["name"] for s in students] for p, students in full.items()}
        self.ids = {(p, s["name"]): s["id"] for p, students in full.items() for s in students}
        self.queue = [s for s in self.queue if s[1] in self.roster.get(s[0], [])]

    def roster_full(self) -> Dict[str, List[dict]]:
        return {p: [{"name": n, "id": self.ids.get((p, n), "")} for n in names]
                for p, names in self.roster.items()}

    # -- settings ----------------------------------------------------------
    def set_setting(self, key: str, value: str) -> bool:
        """Change a display setting. Returns False for unknown keys or bad values."""
        allowed = {"bar_style": BAR_STYLES, "simulator": ON_OFF}
        if key in allowed and value in allowed[key]:
            self.settings[key] = value
            return True
        return False

    @property
    def simulator_on(self) -> bool:
        return self.settings.get("simulator") == "on"

    # -- queue -------------------------------------------------------------
    def join(self, period: str, name: str) -> bool:
        """Add a rostered student to the queue unless already queued or out."""
        s = (period, name)
        if name not in self.roster.get(period, []) or s in self.queue:
            return False
        if self.out and self.out.student == s:
            return False
        self.queue.append(s)
        return True

    def leave(self, period: str, name: str) -> bool:
        s = (period, name)
        if s in self.queue:
            self.queue.remove(s)
            return True
        return False

    def clear_queue(self) -> int:
        n = len(self.queue)
        self.queue.clear()
        return n

    # -- the pass ----------------------------------------------------------
    def set_docked(self, docked: bool, source: str = "sensor") -> None:
        """Called by the sensor. Undocking starts the next student's timer."""
        if docked == self.docked:
            return
        self.docked = docked
        now = self.clock()
        if not docked:
            who = self.queue.pop(0) if self.queue else ("", "Unqueued")
            self.out = Trip(who, now)
            if self.log:
                self.out.log_id = self.log.open_trip(who[0], who[1], self.ids.get(who, ""), now, self.duration)
        elif self.out:
            self.history.append({
                "period": self.out.student[0],
                "student": self.out.student[1],
                "started_at": self.out.started_at,
                "seconds": round(now - self.out.started_at),
            })
            if self.log and self.out.log_id is not None:
                self.log.close_trip(self.out.log_id, now, source)
            self.out = None

    def from_sensor(self, docked: bool) -> bool:
        """Real sensor input. Ignored while the simulator is on. Returns whether it was applied."""
        if self.simulator_on:
            return False
        self.set_docked(docked)
        return True

    def from_simulator(self, docked: bool) -> bool:
        """Simulator input (kiosk buttons). Ignored while the simulator is off."""
        if not self.simulator_on:
            return False
        self.set_docked(docked)
        return True

    def return_pass(self) -> bool:
        """Teacher override when the sensor missed the return."""
        if not self.out:
            return False
        self.set_docked(True, source="manual")
        return True

    def snapshot(self) -> dict:
        """Public state for the kiosk and hallway display. Must never contain student IDs."""
        person = lambda s: {"period": s[0], "student": s[1]}
        return {
            "now": self.clock(),
            "duration": self.duration,
            "docked": self.docked,
            "out": (dict(person(self.out.student), started_at=self.out.started_at)
                    if self.out else None),
            "queue": [person(s) for s in self.queue],
            "roster": self.roster,
            "settings": dict(self.settings),
            "sensor": self.sensor_configured,
        }

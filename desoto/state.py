"""Pass state machine: a queue of students and at most one student out."""
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

DEFAULT_DURATION = 7 * 60

# A student is identified by (class period, name): names can repeat across periods.
Student = Tuple[str, str]


@dataclass
class Trip:
    student: Student
    started_at: float


@dataclass
class PassState:
    roster: Dict[str, List[str]]  # period -> names
    duration: int = DEFAULT_DURATION
    clock: Callable[[], float] = time.time
    queue: List[Student] = field(default_factory=list)
    out: Optional[Trip] = None
    docked: bool = True
    history: List[dict] = field(default_factory=list)

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

    def set_docked(self, docked: bool) -> None:
        """Called by the sensor. Undocking starts the next student's timer."""
        if docked == self.docked:
            return
        self.docked = docked
        now = self.clock()
        if not docked:
            who = self.queue.pop(0) if self.queue else ("", "Unqueued")
            self.out = Trip(who, now)
        elif self.out:
            self.history.append({
                "period": self.out.student[0],
                "student": self.out.student[1],
                "started_at": self.out.started_at,
                "seconds": round(now - self.out.started_at),
            })
            self.out = None

    def snapshot(self) -> dict:
        person = lambda s: {"period": s[0], "student": s[1]}
        return {
            "now": self.clock(),
            "duration": self.duration,
            "docked": self.docked,
            "out": (dict(person(self.out.student), started_at=self.out.started_at)
                    if self.out else None),
            "queue": [person(s) for s in self.queue],
            "roster": self.roster,
        }

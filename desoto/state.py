"""Pass state machine: a queue of students and at most one student out."""
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

DEFAULT_DURATION = 7 * 60


@dataclass
class Trip:
    student: str
    started_at: float


@dataclass
class PassState:
    roster: list[str]
    duration: int = DEFAULT_DURATION
    clock: Callable[[], float] = time.time
    queue: list[str] = field(default_factory=list)
    out: Optional[Trip] = None
    docked: bool = True
    history: list[dict] = field(default_factory=list)

    def join(self, student: str) -> bool:
        """Add a rostered student to the queue unless already queued or out."""
        if student not in self.roster or student in self.queue:
            return False
        if self.out and self.out.student == student:
            return False
        self.queue.append(student)
        return True

    def leave(self, student: str) -> bool:
        if student in self.queue:
            self.queue.remove(student)
            return True
        return False

    def set_docked(self, docked: bool) -> None:
        """Called by the sensor. Undocking starts the next student's timer."""
        if docked == self.docked:
            return
        self.docked = docked
        now = self.clock()
        if not docked:
            who = self.queue.pop(0) if self.queue else "Unqueued"
            self.out = Trip(who, now)
        elif self.out:
            self.history.append({
                "student": self.out.student,
                "started_at": self.out.started_at,
                "seconds": round(now - self.out.started_at),
            })
            self.out = None

    def snapshot(self) -> dict:
        return {
            "now": self.clock(),
            "duration": self.duration,
            "docked": self.docked,
            "out": ({"student": self.out.student, "started_at": self.out.started_at}
                    if self.out else None),
            "queue": list(self.queue),
            "everyone": list(self.roster),
            "roster": [s for s in self.roster
                       if s not in self.queue
                       and not (self.out and self.out.student == s)],
        }

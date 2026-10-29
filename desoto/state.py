"""Pass state machine: a queue of students and at most one student out."""
import re
import time
from datetime import date
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from .bank import Bank
from .db import TripLog

DEFAULT_DURATION = 7 * 60
BAR_STYLES = ("thick", "pill")
ON_OFF = ("on", "off")
# Every setting is a short string so it can live in settings.json. Allowed values per setting:
CHOICES = {
    "bar_style": BAR_STYLES,
    "simulator": ON_OFF,                          # pass input: simulator buttons instead of the sensor (tap mode)
    "test_controls": ("show", "hide"),            # show the simulator / type-an-ID test controls (hide = also refused by the server)
    "identify": ("tap", "scan"),                  # how students identify themselves at the kiosk
    "scan_strict": ON_OFF,                        # kiosk ignores IDs typed by hand (scanners type very fast)
    "call_timeout": ("0", "30", "60", "120", "300"),   # seconds a called student has to scan; 0 = never skip
    "max_trip_minutes": ("10", "15", "30", "60"),      # a trip with no return scan is closed after this long
    "bank_enabled": ON_OFF,                       # pass bank: limits, late penalties and holds
    "passes_suspended": ON_OFF,                   # the red switch: nobody can take a pass until it is switched off
}
# Numeric settings: (lowest, highest) whole number
RANGES = {"bank_size": (1, 99),       # passes each student gets when banks refill
          "late_grace": (0, 600),     # seconds past the pass length that still count as on time
          "late_warnings": (0, 10),   # late returns per refill period that only get a warning
          "late_debit": (0, 10)}      # passes taken for each late return after the warnings
DEFAULTS = {"bar_style": "thick", "simulator": "on", "test_controls": "show", "identify": "tap", "scan_strict": "on",
            "call_timeout": "60", "max_trip_minutes": "30",
            "bank_enabled": "off", "passes_suspended": "off", "bank_size": "8", "late_grace": "30", "late_warnings": "1", "late_debit": "1"}
MAX_UNKNOWN_SCANS = 20     # unrecognised scans per minute before scanning pauses (stops ID guessing)

# A student is identified by (class period, name): names can repeat across periods.
Student = Tuple[str, str]


@dataclass
class Trip:
    student: Student
    started_at: float
    log_id: Optional[int] = None
    method: str = "tap"
    allowed: int = DEFAULT_DURATION   # the length this trip was given when it started


@dataclass
class PassState:
    roster: Dict[str, List[str]]  # period -> names. Public: the kiosk shows these.
    duration: int = DEFAULT_DURATION
    clock: Callable[[], float] = time.time
    queue: List[Student] = field(default_factory=list)
    out: Optional[Trip] = None
    docked: bool = True
    history: List[dict] = field(default_factory=list)
    settings: Dict[str, str] = field(default_factory=lambda: dict(DEFAULTS))
    sensor_configured: bool = False  # True when a real sensor (GPIO pin) is set up
    ids: Dict[Student, str] = field(default_factory=dict)  # PRIVATE: never put in snapshot()
    by_id: Dict[str, List[Student]] = field(default_factory=dict)  # PRIVATE: scanned ID -> students
    log: Optional[TripLog] = None
    queue_method: Dict[Student, str] = field(default_factory=dict)  # how each queued student identified
    called_at: Optional[float] = None   # scan mode: when the pass became free for the student at the front
    notice: Optional[dict] = None       # short message for the display, e.g. a skipped student
    bank: Optional[Bank] = None
    late_notice: Optional[dict] = None  # kiosk-only popup: "you are 2:15 late..."
    _late_n: int = 0
    _fails: List[float] = field(default_factory=list)
    _checked_day: Optional[date] = None
    last_late: Optional[dict] = None

    def __post_init__(self) -> None:
        self.settings.setdefault("pass_seconds", str(self.duration))

    # -- roster ------------------------------------------------------------
    def set_roster(self, full: Dict[str, List[dict]]) -> None:
        """Replace the roster ({period: [{name, id}]}); drop queued students who are gone."""
        self.roster = {p: [s["name"] for s in students] for p, students in full.items()}
        self.ids = {(p, s["name"]): s["id"] for p, students in full.items() for s in students}
        self.by_id = {}
        for (p, n), sid in self.ids.items():
            if sid:
                self.by_id.setdefault(sid, []).append((p, n))
        self.queue = [s for s in self.queue if s[1] in self.roster.get(s[0], [])]
        self.queue_method = {s: m for s, m in self.queue_method.items() if s in self.queue}

    def roster_full(self) -> Dict[str, List[dict]]:
        return {p: [{"name": n, "id": self.ids.get((p, n), "")} for n in names]
                for p, names in self.roster.items()}

    def find_student(self, scanned: str) -> Optional[Student]:
        """Match a scanned code to a student. Accepts the bare ID, or a code (e.g. a QR URL) that contains
        exactly one known ID. A student listed in several periods resolves to the first period."""
        s = (scanned or "").strip()
        if not s:
            return None
        hits = self.by_id.get(s)
        if not hits:
            tokens = {t for t in re.findall(r"[A-Za-z0-9]+", s) if t in self.by_id}
            if len(tokens) != 1:
                return None
            hits = self.by_id[next(iter(tokens))]
        return hits[0]

    # -- settings ----------------------------------------------------------
    def set_setting(self, key: str, value: str) -> bool:
        """Change a setting. Returns False for unknown keys or bad values."""
        if key == "pass_seconds":  # the length of a trip: 1 to 60 minutes. Applies from the next trip.
            if isinstance(value, str) and value.isdigit() and 60 <= int(value) <= 3600:
                self.duration = int(value)
                self.settings[key] = str(int(value))
                return True
            return False
        if key in RANGES:
            lo, hi = RANGES[key]
            if isinstance(value, str) and value.isdigit() and lo <= int(value) <= hi:
                self.settings[key] = str(int(value))
                return True
            return False
        if key in CHOICES and value in CHOICES[key]:
            self.settings[key] = value
            if key == "identify":
                self.called_at = None
            if key == "bank_enabled":
                self._checked_day = None
            return True
        return False

    @property
    def simulator_on(self) -> bool:
        return self.settings.get("simulator") == "on"

    @property
    def suspended(self) -> bool:
        return self.settings.get("passes_suspended") == "on"

    @property
    def scan_mode(self) -> bool:
        return self.settings.get("identify") == "scan"

    # -- queue -------------------------------------------------------------
    def join(self, period: str, name: str, method: str = "tap") -> bool:
        """Add a rostered student to the queue unless already queued or out."""
        s = (period, name)
        if name not in self.roster.get(period, []) or s in self.queue:
            return False
        if self.out and self.out.student == s:
            return False
        self.queue.append(s)
        self.queue_method[s] = method
        return True

    def leave(self, period: str, name: str) -> bool:
        s = (period, name)
        if s in self.queue:
            self.queue.remove(s)
            self.queue_method.pop(s, None)
            return True
        return False

    def clear_queue(self) -> int:
        n = len(self.queue)
        self.queue.clear()
        self.queue_method.clear()
        self.called_at = None
        return n

    # -- starting and ending a trip ---------------------------------------
    def _start_trip(self, who: Student, method: str) -> None:
        now = self.clock()
        self.out = Trip(who, now, method=method, allowed=self.duration)
        self.docked = False
        self.called_at = None
        if self.log:
            self.out.log_id = self.log.open_trip(who[0], who[1], self.ids.get(who, ""), now, self.duration, method)
        if self.bank_on and who[0]:   # every trip costs one pass (a teacher override at zero stays at zero)
            self.bank.adjust(who[0], who[1], self.ids.get(who, ""), -1, "trip", now, self._default_bank(), note=method)

    # -- pass bank --------------------------------------------------------
    @property
    def bank_on(self) -> bool:
        return self.bank is not None and self.settings["bank_enabled"] == "on"

    def _default_bank(self) -> int:
        return int(self.settings["bank_size"])

    def bank_row(self, who: Student) -> dict:
        return self.bank.get(who[0], who[1], self.ids.get(who, ""), self._default_bank())

    def blocked_reason(self, who: Student) -> Optional[str]:
        """"paused" (privilege on hold) or "no_passes" (bank empty); None if the student may take a pass or the bank is off."""
        if self.suspended:
            return "suspended"
        if not self.bank_on or not who[0]:
            return None
        row = self.bank_row(who)
        if row["hold"]:
            return "paused"
        return "no_passes" if row["remaining"] <= 0 else None

    def refill_banks(self, note: str = "Manual refill") -> int:
        """Everyone's bank back to the full amount, late counts cleared (holds are kept). Returns how many students."""
        n = self.bank.refill_all(self.roster_full(), self._default_bank(), self.clock(), note)
        cur = self.bank.current_period(date.fromtimestamp(self.clock()))
        if cur:
            self.bank.set_meta("applied_period", str(cur["id"]))
        return n

    def recheck_periods(self) -> None:
        self._checked_day = None

    def _maybe_refill(self, now: float) -> bool:
        """A new grading period has started: refill every bank, once."""
        today = date.fromtimestamp(now)
        if self._checked_day == today:
            return False
        self._checked_day = today
        cur = self.bank.current_period(today)
        if cur and self.bank.meta("applied_period") != str(cur["id"]):
            self.refill_banks(f"New grading period: {cur['name']}")
            return True
        return False

    def _apply_late(self, who: Student, over: int, now: float) -> dict:
        """A late return: the first few (late_warnings) only warn, each one after that costs late_debit passes."""
        sid, default = self.ids.get(who, ""), self._default_bank()
        lates = self.bank.add_late(who[0], who[1], sid, now, default, note=f"{over // 60}:{over % 60:02d} late")
        debited = 0
        if lates > int(self.settings["late_warnings"]) and int(self.settings["late_debit"]) > 0:
            r = self.bank.adjust(who[0], who[1], sid, -int(self.settings["late_debit"]), "late debit", now, default,
                                 note=f"{over // 60}:{over % 60:02d} late (late return #{lates})")
            debited = -r["delta"]
        return {"seconds": over, "lates": lates, "debited": debited, "remaining": self.bank_row(who)["remaining"]}

    def _end_trip(self, source: str) -> int:
        now, trip = self.clock(), self.out
        secs = round(now - trip.started_at)
        self.last_late = None
        if self.bank_on and trip.student[0] and source in ("sensor", "scan"):   # a teacher's "Mark returned" is never a penalty
            over = secs - trip.allowed
            if over > int(self.settings["late_grace"]):
                self.last_late = self._apply_late(trip.student, over, now)
                self._late_n += 1
                self.late_notice = dict(self.last_late, student=trip.student[1], period=trip.student[0], n=self._late_n, until=now + 20)
        self.history.append({"period": trip.student[0], "student": trip.student[1],
                             "started_at": trip.started_at, "seconds": secs})
        if self.log and trip.log_id is not None:
            self.log.close_trip(trip.log_id, now, source)
        self.out = None
        self.docked = True
        self.called_at = now if (self.scan_mode and self.queue) else None
        return secs

    # -- the physical pass (tap mode) -------------------------------------
    def set_docked(self, docked: bool, source: str = "sensor") -> None:
        """Called by the sensor. Undocking starts the next student's timer."""
        if docked == self.docked:
            return
        self.docked = docked
        if not docked:
            who = self.queue.pop(0) if self.queue else ("", "Unqueued")
            self._start_trip(who, self.queue_method.pop(who, "tap") if who[0] else "sensor")
        elif self.out:
            self._end_trip(source)

    def from_sensor(self, docked: bool) -> bool:
        """Real sensor input. Ignored while the simulator is on, or in scan mode. Returns whether it was applied."""
        if self.simulator_on or self.scan_mode:
            return False
        self.set_docked(docked)
        return True

    def from_simulator(self, docked: bool) -> bool:
        """Simulator input (kiosk buttons). Ignored while the simulator is off, the test controls are hidden,
        or in scan mode."""
        if not self.simulator_on or self.scan_mode or self.settings["test_controls"] != "show":
            return False
        self.set_docked(docked)
        return True

    def return_pass(self) -> bool:
        """Teacher override when the sensor missed the return (or a student forgot to scan back)."""
        if not self.out:
            return False
        self._end_trip("manual")
        return True

    # -- ID scanning (scan mode) ------------------------------------------
    def scan(self, scanned: str) -> dict:
        """One ID scan. Returns {"result": ..., ...}; the app turns it into a message for the kiosk."""
        if not self.scan_mode:
            return {"result": "scan_off"}
        now = self.clock()
        self._fails = [t for t in self._fails if now - t < 60]
        if len(self._fails) >= MAX_UNKNOWN_SCANS:
            return {"result": "rate_limited"}
        who = self.find_student(scanned)
        if who is None:
            if self.suspended:                       # the same answer for every code: it doesn't reveal which IDs are real
                return {"result": "suspended"}
            self._fails.append(now)
            return {"result": "not_found"}
        res = {"student": who[1], "period": who[0]}
        if self.out and self.out.student == who:
            allowed = self.out.allowed
            secs = self._end_trip("scan")
            return {**res, "result": "returned", "seconds": secs, "over": secs > allowed, "late": self.last_late,
                    "remaining": self.bank_row(who)["remaining"] if self.bank_on else None}
        reason = self.blocked_reason(who)
        if reason:                                    # on hold, or out of passes: not even in the line
            if who in self.queue:
                self.queue.remove(who)
                self.queue_method.pop(who, None)
            return {**res, "result": reason}
        if who in self.queue:
            pos = self.queue.index(who) + 1
            if self.out is None and pos == 1:
                self.queue.pop(0)
                self.queue_method.pop(who, None)
                self._start_trip(who, "scan")
                return {**res, "result": "started", "remaining": self.bank_row(who)["remaining"] if self.bank_on else None}
            return {**res, "result": "waiting", "position": pos, "busy": self.out is not None,
                    "next": self.queue[0][1] if pos > 1 else None}
        if self.out is None and not self.queue:
            self._start_trip(who, "scan")
            return {**res, "result": "started", "remaining": self.bank_row(who)["remaining"] if self.bank_on else None}
        self.queue.append(who)
        self.queue_method[who] = "scan"
        return {**res, "result": "queued", "position": len(self.queue)}

    def start_now(self, period: str, name: str) -> bool:
        """Teacher: start this student's trip now (forgotten card). The pass must be free."""
        who = (period, name)
        if self.out or name not in self.roster.get(period, []):
            return False
        if who in self.queue:
            self.queue.remove(who)
            self.queue_method.pop(who, None)
        self._start_trip(who, "teacher")
        return True

    def tick(self) -> bool:
        """Housekeeping, about once a second. Returns True if anything changed (so clients should refresh)."""
        now, changed = self.clock(), False
        if self.notice and now >= self.notice["until"]:
            self.notice, changed = None, True
        if self.late_notice and now >= self.late_notice["until"]:
            self.late_notice, changed = None, True
        if self.bank_on and self._maybe_refill(now):
            changed = True
        if not self.scan_mode:
            return changed
        timeout = int(self.settings["call_timeout"])
        if self.out is None and self.queue:
            if self.called_at is None:
                self.called_at, changed = now, True
            elif self.suspended:
                self.called_at = now                 # the line is frozen; the next student gets a fresh window on resume
            elif timeout and now - self.called_at >= timeout:
                skipped = self.queue.pop(0)
                self.queue_method.pop(skipped, None)
                self.notice = {"text": f"{skipped[1]} did not scan in time and was skipped", "until": now + 10}
                self.called_at = now if self.queue else None
                changed = True
        elif self.called_at is not None:
            self.called_at, changed = None, True
        limit = int(self.settings["max_trip_minutes"]) * 60
        if self.out and now - self.out.started_at >= limit:
            trip = self.out
            if self.log and trip.log_id is not None:
                self.log.abandon_trip(trip.log_id)
            self.notice = {"text": f"{trip.student[1]} never scanned back in. The trip was closed.", "until": now + 10}
            self.out, self.docked = None, True
            self.called_at = now if self.queue else None
            changed = True
        return changed

    def snapshot(self, kiosk: bool = False) -> dict:
        """Public state for the kiosk and hallway display. Must never contain student IDs.
        The late-return popup is only included for the kiosk (kiosk=True), never for the hallway display."""
        person = lambda s: {"period": s[0], "student": s[1]}
        timeout = int(self.settings["call_timeout"])
        called = None
        if self.scan_mode and not self.suspended and self.out is None and self.queue and self.called_at is not None:
            called = dict(person(self.queue[0]), expires_at=(self.called_at + timeout) if timeout else None, window=timeout)
        return {
            "now": self.clock(),
            "duration": self.out.allowed if self.out else self.duration,   # a trip in progress keeps its own length
            "docked": self.docked,
            "out": (dict(person(self.out.student), started_at=self.out.started_at)
                    if self.out else None),
            "queue": [person(s) for s in self.queue],
            "roster": self.roster,
            "settings": dict(self.settings),
            "sensor": self.sensor_configured,
            "called": called,
            "notice": self.notice["text"] if self.notice else None,
            "late_notice": ({k: v for k, v in self.late_notice.items() if k != "until"}
                            if kiosk and self.late_notice else None),
        }

"""Pass bank: how many passes each student has left, who is on hold, grading periods and a ledger of every change.

Lives in the same SQLite file as the trip log. A student is keyed by their ID when they have one (so the bank
survives a roster re-import and follows a student listed in two periods), otherwise by "period|name".
"""
from datetime import date
from typing import Dict, List, Optional

from .db import TripLog

SCHEMA = """
CREATE TABLE IF NOT EXISTS bank(
  skey TEXT PRIMARY KEY, period TEXT NOT NULL, name TEXT NOT NULL, student_id TEXT NOT NULL DEFAULT '',
  remaining INTEGER NOT NULL, lates INTEGER NOT NULL DEFAULT 0, hold INTEGER NOT NULL DEFAULT 0,
  note TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS bank_ledger(
  id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, skey TEXT NOT NULL, period TEXT NOT NULL, name TEXT NOT NULL,
  delta INTEGER NOT NULL, balance INTEGER NOT NULL, reason TEXT NOT NULL, note TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS bank_ledger_skey ON bank_ledger(skey, at);
CREATE TABLE IF NOT EXISTS grading_periods(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, start TEXT NOT NULL, end TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS bank_meta(k TEXT PRIMARY KEY, v TEXT NOT NULL);
"""


def skey(period: str, name: str, student_id: str = "") -> str:
    return student_id if student_id else f"{period}|{name}"


class Bank:
    def __init__(self, log: TripLog):
        self.db, self.lock = log.db, log.lock
        with self.lock:
            self.db.executescript(SCHEMA)
            self.db.commit()

    # -- reading ---------------------------------------------------------
    def get(self, period: str, name: str, student_id: str, default: int) -> dict:
        """One student's bank. A student with no row yet has the default allotment and a clean record."""
        key = skey(period, name, student_id)
        with self.lock:
            r = self.db.execute("SELECT * FROM bank WHERE skey=?", (key,)).fetchone()
        if r:
            return {"skey": key, "remaining": r["remaining"], "lates": r["lates"], "hold": bool(r["hold"]), "note": r["note"]}
        return {"skey": key, "remaining": default, "lates": 0, "hold": False, "note": ""}

    def students(self, roster: Dict[str, List[dict]], default: int) -> List[dict]:
        with self.lock:
            rows = {r["skey"]: r for r in self.db.execute("SELECT * FROM bank")}
        out = []
        for period, students in roster.items():
            for s in students:
                key = skey(period, s["name"], s["id"])
                r = rows.get(key)
                out.append({"period": period, "name": s["name"], "id": s["id"], "skey": key,
                            "remaining": r["remaining"] if r else default, "lates": r["lates"] if r else 0,
                            "hold": bool(r["hold"]) if r else False, "note": r["note"] if r else ""})
        return out

    def ledger(self, period: str, name: str, student_id: str, limit: int = 100) -> List[dict]:
        key = skey(period, name, student_id)
        with self.lock:
            rows = self.db.execute("SELECT at,delta,balance,reason,note FROM bank_ledger WHERE skey=? ORDER BY id DESC LIMIT ?",
                                   (key, limit)).fetchall()
        return [dict(r) for r in rows]

    # -- changing --------------------------------------------------------
    def _write(self, key, period, name, sid, remaining=None, lates=None, hold=None, note=None, default=0):
        r = self.db.execute("SELECT * FROM bank WHERE skey=?", (key,)).fetchone()
        cur = {"remaining": r["remaining"] if r else default, "lates": r["lates"] if r else 0,
               "hold": r["hold"] if r else 0, "note": r["note"] if r else ""}
        for k, v in (("remaining", remaining), ("lates", lates), ("hold", None if hold is None else int(hold)), ("note", note)):
            if v is not None:
                cur[k] = v
        self.db.execute("INSERT INTO bank(skey,period,name,student_id,remaining,lates,hold,note) VALUES(?,?,?,?,?,?,?,?) "
                        "ON CONFLICT(skey) DO UPDATE SET period=excluded.period,name=excluded.name,student_id=excluded.student_id,"
                        "remaining=excluded.remaining,lates=excluded.lates,hold=excluded.hold,note=excluded.note",
                        (key, period, name, sid, cur["remaining"], cur["lates"], cur["hold"], cur["note"]))
        return cur

    def _entry(self, now, key, period, name, delta, balance, reason, note=""):
        self.db.execute("INSERT INTO bank_ledger(at,skey,period,name,delta,balance,reason,note) VALUES(?,?,?,?,?,?,?,?)",
                        (now, key, period, name, delta, balance, reason, note))

    def adjust(self, period, name, sid, delta, reason, now, default, note="") -> dict:
        """Add or take passes. The balance never goes below zero. Returns {"delta": actual change, "remaining": n}."""
        key = skey(period, name, sid)
        with self.lock:
            r = self.db.execute("SELECT remaining FROM bank WHERE skey=?", (key,)).fetchone()
            before = r["remaining"] if r else default
            after = max(0, before + delta)
            self._write(key, period, name, sid, remaining=after, default=default)
            self._entry(now, key, period, name, after - before, after, reason, note)
            self.db.commit()
        return {"delta": after - before, "remaining": after}

    def add_late(self, period, name, sid, now, default, note="") -> int:
        """Count one late return in this refill period. Returns the new count."""
        key = skey(period, name, sid)
        with self.lock:
            cur = self._write(key, period, name, sid, default=default)
            lates = cur["lates"] + 1
            self._write(key, period, name, sid, lates=lates, default=default)
            self._entry(now, key, period, name, 0, cur["remaining"], "late", note)
            self.db.commit()
        return lates

    def set_hold(self, period, name, sid, hold: bool, note: str, now, default) -> None:
        key = skey(period, name, sid)
        with self.lock:
            cur = self._write(key, period, name, sid, hold=hold, note=note if hold else "", default=default)
            self._entry(now, key, period, name, 0, cur["remaining"], "hold" if hold else "resume", note)
            self.db.commit()

    def refill_all(self, roster: Dict[str, List[dict]], size: int, now, note: str = "") -> int:
        """Everyone's bank back to `size`, late counts cleared. Holds are kept. Returns how many students."""
        n = 0
        with self.lock:
            for period, students in roster.items():
                for s in students:
                    key = skey(period, s["name"], s["id"])
                    r = self.db.execute("SELECT remaining FROM bank WHERE skey=?", (key,)).fetchone()
                    before = r["remaining"] if r else size
                    self._write(key, period, s["name"], s["id"], remaining=size, lates=0, default=size)
                    self._entry(now, key, period, s["name"], size - before, size, "refill", note)
                    n += 1
            self.db.commit()
        return n

    # -- grading periods -------------------------------------------------
    def periods(self) -> List[dict]:
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT id,name,start,end FROM grading_periods ORDER BY start")]

    def add_period(self, name: str, start: str, end: str) -> int:
        date.fromisoformat(start), date.fromisoformat(end)       # ValueError if not YYYY-MM-DD
        if end < start:
            raise ValueError("The end date is before the start date")
        with self.lock:
            cur = self.db.execute("INSERT INTO grading_periods(name,start,end) VALUES(?,?,?)", (name.strip() or "Period", start, end))
            self.db.commit()
            return cur.lastrowid

    def remove_period(self, period_id: int) -> bool:
        with self.lock:
            cur = self.db.execute("DELETE FROM grading_periods WHERE id=?", (period_id,))
            self.db.commit()
            return cur.rowcount > 0

    def current_period(self, day: date) -> Optional[dict]:
        d = day.isoformat()
        for p in self.periods():
            if p["start"] <= d <= p["end"]:
                return p
        return None

    def meta(self, key: str) -> Optional[str]:
        with self.lock:
            r = self.db.execute("SELECT v FROM bank_meta WHERE k=?", (key,)).fetchone()
        return r["v"] if r else None

    def set_meta(self, key: str, value: str) -> None:
        with self.lock:
            self.db.execute("INSERT INTO bank_meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (key, value))
            self.db.commit()

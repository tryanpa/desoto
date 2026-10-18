"""Trip log in SQLite (stdlib). One row per pass trip."""
import sqlite3
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Optional, Tuple, Union

SCHEMA = """
CREATE TABLE IF NOT EXISTS trips(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  period TEXT NOT NULL, name TEXT NOT NULL, student_id TEXT NOT NULL DEFAULT '',
  out_at REAL NOT NULL, in_at REAL, allowed INTEGER NOT NULL,
  source TEXT NOT NULL DEFAULT 'sensor', incomplete INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS trips_out ON trips(out_at);
"""


def day_bounds(start: Optional[str], end: Optional[str]) -> Tuple[Optional[float], Optional[float]]:
    """YYYY-MM-DD (local, inclusive) -> epoch [lo, hi)."""
    lo = hi = None
    if start:
        lo = datetime.combine(date.fromisoformat(start), datetime.min.time()).timestamp()
    if end:
        hi = datetime.combine(date.fromisoformat(end) + timedelta(days=1), datetime.min.time()).timestamp()
    return lo, hi


def _row(r: sqlite3.Row) -> dict:
    d = dict(r)
    if d["in_at"] is not None:
        d["seconds"] = round(d["in_at"] - d["out_at"])
        d["status"] = "done"
    else:
        d["seconds"] = None
        d["status"] = "incomplete" if d["incomplete"] else "out"
    d["over"] = d["seconds"] is not None and d["seconds"] > d["allowed"]
    d["unqueued"] = d["period"] == ""
    del d["incomplete"]
    return d


class TripLog:
    def __init__(self, path: Union[str, Path] = ":memory:"):
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.db.executescript(SCHEMA)
            # A trip still open at startup lost its return event (restart or crash).
            self.db.execute("UPDATE trips SET incomplete=1 WHERE in_at IS NULL")
            self.db.commit()

    def open_trip(self, period: str, name: str, student_id: str, out_at: float, allowed: int) -> int:
        with self.lock:
            cur = self.db.execute(
                "INSERT INTO trips(period,name,student_id,out_at,allowed) VALUES(?,?,?,?,?)",
                (period, name, student_id, out_at, allowed))
            self.db.commit()
            return cur.lastrowid

    def close_trip(self, trip_id: int, in_at: float, source: str = "sensor") -> None:
        with self.lock:
            self.db.execute("UPDATE trips SET in_at=?, source=? WHERE id=?", (in_at, source, trip_id))
            self.db.commit()

    def _where(self, start, end, period, q):
        lo, hi = day_bounds(start, end)
        sql, args = [], []
        if lo is not None:
            sql.append("out_at>=?"); args.append(lo)
        if hi is not None:
            sql.append("out_at<?"); args.append(hi)
        if period:
            sql.append("period=?"); args.append(period)
        if q:
            sql.append("(name LIKE ? OR student_id LIKE ?)"); args += [f"%{q}%", f"%{q}%"]
        return (" WHERE " + " AND ".join(sql)) if sql else "", args

    def query(self, start=None, end=None, period=None, q=None,
              limit: Optional[int] = None, offset: int = 0) -> Tuple[List[dict], int]:
        where, args = self._where(start, end, period, q)
        with self.lock:
            total = self.db.execute("SELECT COUNT(*) FROM trips" + where, args).fetchone()[0]
            sql = "SELECT * FROM trips" + where + " ORDER BY out_at DESC"
            if limit is not None:
                sql += " LIMIT ? OFFSET ?"; args = args + [limit, offset]
            rows = self.db.execute(sql, args).fetchall()
        return [_row(r) for r in rows], total

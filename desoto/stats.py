"""Aggregate trip rows into the numbers the admin Data tab shows. Pure functions, local time."""
import statistics
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

WEEKS = 12


def _local(ts: float) -> datetime:
    return datetime.fromtimestamp(ts)


def _summary(trips: List[dict]) -> dict:
    secs = [t["seconds"] for t in trips]
    return {
        "trips": len(trips),
        "over": sum(1 for t in trips if t["over"]),
        "total_seconds": sum(secs),
        "avg_seconds": round(sum(secs) / len(secs)) if secs else 0,
        "longest_seconds": max(secs) if secs else 0,
    }


def compute(rows: List[dict], roster: Optional[Dict[str, List[dict]]] = None,
            period: Optional[str] = None, end: Optional[str] = None, today: Optional[date] = None) -> dict:
    today = today or date.today()
    done = [r for r in rows if r["status"] == "done"]
    named = [r for r in done if not r["unqueued"]]

    overall = _summary(done)
    secs = [r["seconds"] for r in done]
    overall.update({
        "unique_students": len({(r["period"], r["name"]) for r in named}),
        "median_seconds": round(statistics.median(secs)) if secs else 0,
        "over_pct": round(100 * overall["over"] / len(done), 1) if done else 0,
        "unqueued": sum(1 for r in done if r["unqueued"]),
        "manual_returns": sum(1 for r in done if r["source"] == "manual"),
        "incomplete": sum(1 for r in rows if r["status"] == "incomplete"),
    })

    by_day: Dict[str, List[dict]] = {}
    for r in done:
        by_day.setdefault(_local(r["out_at"]).date().isoformat(), []).append(r)
    daily = [dict(date=d, **_summary(ts)) for d, ts in sorted(by_day.items())]

    hourly = [{"hour": h, "trips": 0, "over": 0} for h in range(24)]
    for r in done:
        h = hourly[_local(r["out_at"]).hour]
        h["trips"] += 1
        h["over"] += 1 if r["over"] else 0

    by_p: Dict[str, List[dict]] = {}
    for r in named:
        by_p.setdefault(r["period"], []).append(r)
    by_period = []
    for p, ts in by_p.items():
        s = _summary(ts)
        s.update(period=p, students=len({t["name"] for t in ts}))
        by_period.append(s)
    by_period.sort(key=lambda s: (0, int(s["period"])) if s["period"].isdigit() else (1, 0))

    anchor = min(date.fromisoformat(end), today) if end else today
    last_monday = anchor - timedelta(days=anchor.weekday())
    week_starts = [last_monday - timedelta(weeks=WEEKS - 1 - i) for i in range(WEEKS)]

    people: Dict[tuple, dict] = {}
    if roster:
        for p, students in roster.items():
            if period and p != period:
                continue
            for s in students:
                people[(p, s["name"])] = {"period": p, "name": s["name"], "id": s["id"], "trips_list": []}
    for r in named:
        k = (r["period"], r["name"])
        people.setdefault(k, {"period": r["period"], "name": r["name"], "id": r["student_id"], "trips_list": []})
        people[k]["trips_list"].append(r)
    students = []
    for person in people.values():
        ts = person.pop("trips_list")
        s = _summary(ts)
        s.update(person)
        s["over_seconds"] = sum(max(0, t["seconds"] - t["allowed"]) for t in ts)
        s["last_out"] = max((t["out_at"] for t in ts), default=None)
        weekly = [0] * WEEKS
        for t in ts:
            d = _local(t["out_at"]).date()
            idx = (d - week_starts[0]).days // 7
            if 0 <= idx < WEEKS:
                weekly[idx] += 1
        s["weekly"] = weekly
        students.append(s)
    students.sort(key=lambda s: (-s["trips"], s["name"]))

    return {"overall": overall, "daily": daily, "hourly": hourly, "by_period": by_period,
            "students": students, "week_starts": [w.isoformat() for w in week_starts]}

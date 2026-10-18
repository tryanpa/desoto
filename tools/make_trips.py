"""Fill a trip log with fake history so the Data and Activity pages can be judged at scale.

    python tools/make_trips.py --roster rosters/scale-test.json --days 30 --reset
    DESOTO_ROSTER=rosters/scale-test.json uvicorn desoto.app:app

Writes data/trips.db (or $DESOTO_DATA/trips.db). Fake data only: do not run on a real log without --reset awareness.
"""
import argparse
import json
import os
import random
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from desoto.db import TripLog  # noqa: E402
from desoto.roster import normalize_roster  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--roster", default="rosters/scale-test.json")
ap.add_argument("--days", type=int, default=30, help="calendar days back; weekends are skipped")
ap.add_argument("--seed", type=int, default=1)
ap.add_argument("--reset", action="store_true", help="delete the existing log first")
args = ap.parse_args()

data = Path(os.environ.get("DESOTO_DATA", Path(__file__).resolve().parent.parent / "data"))
data.mkdir(parents=True, exist_ok=True)
db = data / "trips.db"
if args.reset and db.exists():
    db.unlink()
log = TripLog(db)
roster = normalize_roster(json.loads(Path(args.roster).read_text()))
rng = random.Random(args.seed)

# Period start times (24h) with 50 minute classes; students go in the middle of class, rarely at the edges.
starts = {str(i + 1): 8 * 60 + 55 * i for i in range(8)}
habit = {(p, s["name"]): rng.choice([0.0, 0.02, 0.05, 0.05, 0.1, 0.15, 0.3]) for p, ss in roster.items() for s in ss}
n = 0
for back in range(args.days, -1, -1):
    day = date.today() - timedelta(days=back)
    if day.weekday() >= 5:
        continue
    for p, students in roster.items():
        for s in students:
            if rng.random() > habit[(p, s["name"])]:
                continue
            minute = starts.get(p, 9 * 60) + rng.randint(8, 42)
            out = datetime.combine(day, datetime.min.time()) + timedelta(minutes=minute, seconds=rng.randint(0, 59))
            if out.timestamp() > datetime.now().timestamp():
                continue
            secs = max(40, int(rng.lognormvariate(5.4, 0.45)))  # median ~4 min
            tid = log.open_trip(p, s["name"], s["id"], out.timestamp(), 420)
            log.close_trip(tid, out.timestamp() + secs, "manual" if rng.random() < 0.01 else "sensor")
            n += 1
print(f"Wrote {n} trips to {db}")

# Desoto

Bathroom pass tracker. Students tap their class period, then their name, on a kiosk to join a
queue. Lifting the physical pass off its dock (hall effect sensor) starts a 7-minute countdown
for the next student in line; returning it stops the clock. A hallway display shows who is out,
the countdown (turns red and counts up when over time) and the queue. Teachers use an admin
site for the roster, the activity log and usage data.

## Run

    pip install -r requirements.txt
    uvicorn desoto.app:app --host 0.0.0.0 --port 8000

| Page | URL |
|---|---|
| Kiosk (has simulator buttons for take/return pass) | `http://<host>:8000/` |
| Hallway display | `http://<host>:8000/display.html` |
| Admin | `http://<host>:8000/admin.html` |

**Set an admin PIN** so only you can open the admin pages (they show student IDs):

    DESOTO_ADMIN_PIN=1234 uvicorn desoto.app:app --host 0.0.0.0 --port 8000

## Student IDs and privacy

Rosters carry a student ID, but IDs are **never** sent to the kiosk or the hallway display; only
the PIN-protected admin endpoints return them. In the admin pages IDs are masked
(`••••567`) until you tick **Show student IDs**. Real student data lives in `data/` (git-ignored):
`roster.json`, `trips.db` (the activity log) and `settings.json`. Don't commit that folder.

## Admin

- **Live**: what the kiosk shows now: pass status, the queue, **Clear queue**, remove one student,
  and **Mark returned** if the sensor missed a return.
- **Roster**: import from CSV, add or remove a student. The roster is stored as JSON.
- **Activity Log**: every trip with student, period, ID, out time, in time, date, length and result
  (on time / over time). Filter by date, period or search; export to CSV.
- **Data**: totals, average / longest trip, over-time counts, trips per day, average length by day,
  time-of-day and per-period charts, and a sortable per-student table with a 12-week trend.
  Every chart has a **Table** view. Export per-student numbers to CSV.
- **Settings**: countdown style (thick bar or pill), applied to the display live.

### Roster CSV

A header row, then one student per row. Columns can be in any order and are matched by name:

| Meaning | Accepted headers |
|---|---|
| Name | `Name`, `Student Name`, `Full Name` **or** `First` + `Last` |
| ID | `ID`, `Student ID`, `Student Number`, `Barcode` |
| Period | `Period`, `Class Period`, `Pd`, `Hour` (values like `3`, `P3`, `Period 3`) |

`Last, First` names become `First Last`. If the file has no Period column, enter a period for the
whole file. Importing shows a preview (new / removed counts and any warnings) before anything changes.
**Replace roster** swaps the whole roster; **Add & update** only adds or updates (matched by period + ID).
Two students with the same name in one period are listed as `Name` and `Name (2)`.

## Config (environment variables)

| Variable | Meaning |
|---|---|
| `DESOTO_ADMIN_PIN` | PIN required for admin pages and endpoints |
| `DESOTO_DATA` | data folder (default `./data`) |
| `DESOTO_ROSTER` | roster file (default `<data>/roster.json`, seeded from `roster.sample.json`) |
| `DESOTO_SECONDS` | trip length (default 420) |
| `DESOTO_GPIO_PIN` | BCM pin of the hall sensor (Raspberry Pi) |

## Raspberry Pi hall sensor

Set `DESOTO_GPIO_PIN=<BCM pin>` (and `pip install gpiozero lgpio`). Wire an open-collector hall
sensor (e.g. A3144) between the pin and ground (internal pull-up used). A magnet in the pass pulls
the pin low while docked; removing the pass starts the timer.

## Try it at scale with fake data

    python tools/make_roster.py 6 28 > rosters/scale-test.json     # 6 periods x 28 students, with IDs
    mkdir -p data && cp rosters/scale-test.json data/roster.json
    python tools/make_trips.py --roster data/roster.json --days 60 --reset   # fake trip history
    uvicorn desoto.app:app --host 0.0.0.0 --port 8000

Open `/#3` to jump the kiosk straight to period 3. Delete `data/` to go back to a clean slate.

## Test

    pytest

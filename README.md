# Desoto

Bathroom pass tracker. Students tap their class period, then their name, on a kiosk to join a
queue. Lifting the physical pass off its dock (hall effect sensor) starts a 7-minute countdown
for the next student in line; returning it stops the clock. A hallway display shows who is out,
the countdown (turns red and counts up when over time) and the queue. Teachers use an admin
site for the roster, the activity log and usage data.

## Run

    ./start.sh

That sets up the virtualenv and dependencies the first time, then starts the server and prints the
kiosk, display and admin addresses. To change settings, copy `.env.example` to `.env` and edit it
(for example `DESOTO_ADMIN_PIN=1234`). Manual equivalent:

    pip install -r requirements.txt
    uvicorn desoto.app:app --host 0.0.0.0 --port 8000

| Page | URL |
|---|---|
| Kiosk (simulator buttons appear when the simulator is on) | `http://<host>:8000/` |
| Hallway display | `http://<host>:8000/display.html` |
| Admin | `http://<host>:8000/admin.html` |

**Set an admin PIN** so only you can open the admin pages (they show student IDs): put
`DESOTO_ADMIN_PIN=1234` in `.env`.

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
- **Settings**: **Student identification** (tap names or scan ID cards), **Pass input** (Sensor or Simulator), **Simulator and test controls** (Show or Hide) and countdown style (thick bar or pill), applied live.
  Simulator on = the kiosk shows Take pass / Return pass buttons and the real sensor is ignored; off = the
  sensor controls the pass and the buttons disappear. Switching back to Sensor catches up with where the pass
  really is. A computer with no sensor configured can't turn the simulator off. When `DESOTO_GPIO_PIN` is set,
  a fresh install starts on Sensor; otherwise it starts on Simulator. The page header shows "· Simulator" while it is on.

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

## ID card scanning (instead of tapping names)

Admin **Settings → Student identification → Scan ID card** replaces tap-a-name with scanning the student's ID card.
No sensor or physical pass is used in this mode (the Pass input card is dimmed).

How a trip goes:

1. A student scans their card. If the pass is free and nobody is waiting, their trip **starts at once**. Otherwise they
   are **added to the line** and the kiosk says "you are #3 in line".
2. When the pass is free, the first student in line is **called**: the hallway display shows "Your turn: scan your ID to
   start" with a countdown. They scan again to check in and the timer starts. Nobody can jump the line.
3. If they don't scan in time (default 1 minute, set in Settings), they are skipped and can scan to rejoin at the back.
4. A scan when they are back **ends the trip**. A trip that never gets a return scan is closed after 10 min to 1 hour
   (default 30) and kept in the log as "No return logged", so a forgotten scan can't block the pass.

Safeguards: unrecognised IDs just show "ID not recognized" (and after 20 in a minute scanning pauses, so nobody can
guess IDs); the tap endpoints are switched off in this mode; by default IDs typed by hand are refused (a scanner types a
whole ID in a fraction of a second); the ID is never displayed or sent to the kiosk or display. Each log row records how
the student identified: **Scan**, **Tap** or **Teacher**.

**Forgotten or lost card:** admin **Live → Add a student by name** adds them to the line or starts their trip, logged as
"Teacher". **Mark returned** ends a trip, and **Clear queue** / **Remove** work as before.

**Scanner:** any USB barcode/QR scanner that works like a keyboard (it types the ID, then Enter) works; use a 2D scanner
for QR codes. The kiosk only listens for scans while it is the open window in scan mode (the display and admin pages ignore them). It
accepts a scan that ends in Enter or Tab, or with no ending at all (a short pause means the scan is finished).
If a QR code holds more than the number (for example a web address), Desoto accepts it as long as it contains
exactly one known student ID. Keep the Pi's keyboard layout on US, since scanners send US key codes. To test with no scanner,
set **Simulator and test controls** to **Show**: the kiosk footer then has a box where you can type an ID.

What scanning does and doesn't prove: it shows someone has the card, not that they are the student. Cards can be lent or a
barcode photographed, so it is better than tapping a name, not perfect. A student listed in more than one period is
treated as the first period listed.

## Raspberry Pi setup (kiosk + hallway display)

Tested only by dry run so far (`--dry-run` prints every step and changes nothing). Written for Raspberry Pi OS
**Trixie** (also fine on Bookworm), desktop edition, with the Pi set to your network and this repo cloned on it.

**Pi 1: server + touch kiosk** (the Pi that has the sensor and the touchscreen):

    git clone <your repo URL> desoto && cd desoto
    ./deploy/pi-install.sh --hostname desoto          # try it first with --dry-run
    sudo reboot

It installs the packages, builds the virtualenv (with the system GPIO libraries), asks for an admin PIN, runs the
server as a systemd service (`desoto`, starts at boot and restarts if it stops), and opens the kiosk full screen at
every login (desktop autologin on, screen blanking off). Use `--no-kiosk` for a server with no screen.
Check it: `systemctl status desoto`, logs: `journalctl -u desoto -f`.

**Pi 2: hallway display** (any Pi with a screen; no Python needed):

    git clone <your repo URL> desoto && cd desoto
    ./deploy/pi-install.sh display desoto.local       # or the server Pi's IP address
    sudo reboot

If `desoto.local` doesn't resolve (some school networks block it), use the server Pi's IP address instead, and give
that Pi a fixed address in your router. The browser waits for the server, so power-up order doesn't matter.

If the touch lands in the wrong place, or the screen still sleeps, check Raspberry Pi Configuration -> Display
(screen blanking) and the Screen Configuration tool (touch mapping); both are standard Trixie settings.

### Hall sensor / reed switch wiring

`DESOTO_GPIO_PIN` (default 17 = physical pin 11, set by the installer in `.env`) is the BCM pin that reads the pass.
The pin is read with its internal pull-up, so **docked = pin pulled to ground**. Two ways to wire it:

- **Reed switch (simplest, no power):** one wire to GPIO 17 (pin 11), the other to ground (pin 9). A magnet in the
  pass closes the switch while the pass sits in its dock.
- **Hall sensor, e.g. A3144 (open collector):** VCC to 5 V (pin 2), GND to ground (pin 9), output to GPIO 17
  (pin 11). The Pi's pull-up lifts the output to 3.3 V, which is safe for the pin. It needs 4.5 V or more to run, so don't
  power it from 3.3 V.

**Simulator and test controls:** set to **Hide** for the real classroom: the kiosk shows no Take pass / Return pass buttons or
type-an-ID box, and the server refuses simulator requests. **Show** is for testing. A fresh install on a Pi with a sensor starts
on Sensor with the controls hidden; a laptop starts with them shown.

**Sensor test:** admin **Settings → Sensor test** shows the sensor's live reading (docked / out), a count of changes,
the shortest gap between changes and a list of recent readings. Lift and replace the pass a few times: each lift and
each return should add exactly one row. Rows closer than half a second are flagged as flicker. It works on the Pi that
has the sensor and keeps recording even while the simulator is on. (Changes shorter than 0.1 s are filtered out before
they get here.)

The Pi 400's header is on the back and unlabeled, so verify pin positions with a Pi 400 pin chart or by running
`pinout` before connecting wires. Then turn the simulator off in the admin **Settings** (a fresh install with a sensor
configured already starts on Sensor).

## Try it at scale with fake data

    python tools/make_roster.py 6 28 > rosters/scale-test.json     # 6 periods x 28 students, with IDs
    mkdir -p data && cp rosters/scale-test.json data/roster.json
    python tools/make_trips.py --roster data/roster.json --days 60 --reset   # fake trip history
    ./start.sh

Open `/#3` to jump the kiosk straight to period 3. Delete `data/` to go back to a clean slate.

## Test

    pytest

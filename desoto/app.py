import asyncio
import csv
import io
import json
import os
import secrets
import shutil
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import power
from .bank import Bank
from . import roster as rosterlib
from . import stats
from .db import TripLog
from .sensor import SensorMonitor
from .state import DEFAULT_DURATION, PassState

ROOT = Path(__file__).resolve().parent.parent
STATIC = Path(__file__).parent / "static"
MAX_CSV_BYTES = 2_000_000


def _safe(cell) -> str:
    """Stop spreadsheet apps from running a name like '=SUM(...)' as a formula."""
    s = "" if cell is None else str(cell)
    return "'" + s if s[:1] in "=+-@\t\r" else s


def _fmt_time(ts: Optional[float]) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S") if ts else ""


def _csv_response(name: str, header, rows) -> Response:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(rows)
    return Response("﻿" + buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


BLOCKED = {"paused": "your pass privilege is on hold", "no_passes": "you have no passes left"}
SUSPENDED_MESSAGE = "All passes have been suspended. Please see your teacher."


def block_message(name: str, reason: str) -> str:
    if reason == "suspended":
        return SUSPENDED_MESSAGE
    return f"{name}, {BLOCKED[reason]}. Please see your teacher."


def scan_message(r: dict) -> str:
    """Words for the kiosk after a scan. Never includes the ID."""
    name = r.get("student", "")
    code = r["result"]
    left = r.get("remaining")
    if code == "started":
        bank = "" if left is None else f" {left} pass{'' if left == 1 else 'es'} left."
        return f"{name}: your trip has started.{bank} Scan again when you are back."
    if code == "returned":
        s = r["seconds"]
        return f"Welcome back, {name}. Trip time {s // 60}:{s % 60:02d}" + (" (over time)." if r["over"] else ".")
    if code in BLOCKED or code == "suspended":
        return block_message(name, code)
    if code == "queued":
        return f"{name}: you are #{r['position']} in line."
    if code == "waiting":
        if r["position"] == 1:
            return f"{name}: you are next. Scan again when the pass is free."
        return f"{name}: you are #{r['position']} in line. {r['next']} is next."
    if code == "not_found":
        return "ID not recognized. Please see your teacher."
    if code == "rate_limited":
        return "Too many unrecognized scans. Please wait a minute or see your teacher."
    return "Scanning is not turned on."


def create_app(state: Optional[PassState] = None, settings_path: Optional[Path] = None,
               log: Optional[TripLog] = None, roster_path: Optional[Path] = None) -> FastAPI:
    owns_state = state is None
    if state is None:
        data = Path(os.environ.get("DESOTO_DATA", ROOT / "data"))
        data.mkdir(parents=True, exist_ok=True)
        roster_path = Path(os.environ.get("DESOTO_ROSTER", data / "roster.json"))
        if not roster_path.exists():  # first run: seed from the sample roster
            shutil.copy(ROOT / "roster.sample.json", roster_path)
        state = PassState({}, int(os.environ.get("DESOTO_SECONDS", DEFAULT_DURATION)))
        state.set_roster(rosterlib.normalize_roster(json.loads(roster_path.read_text())))
        settings_path = Path(os.environ.get("DESOTO_SETTINGS", data / "settings.json"))
        log = TripLog(data / "trips.db")
    if log is not None:
        state.log = log
        state.bank = Bank(log)
    default_seconds = state.duration   # from DESOTO_SECONDS; a length chosen in Settings overrides it
    saved = json.loads(settings_path.read_text()) if settings_path and settings_path.exists() else {}
    for k, v in saved.items():
        state.set_setting(k, v)
    if owns_state:
        state.sensor_configured = bool(os.environ.get("DESOTO_GPIO_PIN"))
        if "simulator" not in saved and state.sensor_configured:
            state.set_setting("simulator", "off")  # a real install starts on the sensor
        if "test_controls" not in saved and state.sensor_configured:
            state.set_setting("test_controls", "hide")  # and starts with the test controls hidden
    admin_pin = os.environ.get("DESOTO_ADMIN_PIN")

    app = FastAPI(title="Desoto")
    app.state.pass_state = state
    clients: dict = {}   # websocket -> "kiosk" or ""

    async def broadcast() -> None:
        for ws, role in list(clients.items()):
            try:
                await ws.send_json(state.snapshot(kiosk=(role == "kiosk")))
            except Exception:
                clients.pop(ws, None)

    monitor = SensorMonitor()
    app.state.monitor = monitor

    def on_sensor(docked: bool) -> None:
        monitor.record(docked)  # the Sensor test sees every reading, even while the simulator is on
        if state.from_sensor(docked):
            asyncio.ensure_future(broadcast())

    async def ticker() -> None:
        """Once a second: skip students who don't scan when called, close runaway trips."""
        while True:
            await asyncio.sleep(1)
            try:
                if state.tick():
                    await broadcast()
            except Exception:  # keep the clock running whatever happens
                pass

    @app.on_event("shutdown")
    async def shutdown() -> None:
        task = getattr(app.state, "ticker", None)
        if task:
            task.cancel()

    @app.on_event("startup")
    async def startup() -> None:
        app.state.ticker = asyncio.ensure_future(ticker())
        pin = os.environ.get("DESOTO_GPIO_PIN")
        if pin:
            from .sensor import GpioHallSensor
            app.state.sensor = GpioHallSensor(int(pin), on_sensor)

    def save_roster() -> None:
        if roster_path:
            roster_path.write_text(json.dumps(state.roster_full(), indent=1))

    # ---------------------------------------------------------------- public
    class Who(BaseModel):
        period: str
        student: str

    class Docked(BaseModel):
        docked: bool

    @app.get("/api/auth")
    def auth_info():
        return {"pin_required": bool(admin_pin)}

    @app.get("/api/state")
    def get_state():
        return state.snapshot()

    @app.post("/api/queue")
    async def join(who: Who):
        if state.scan_mode:
            raise HTTPException(403, "Students scan their ID card to join the queue")
        reason = state.blocked_reason((who.period, who.student))
        if reason:
            return JSONResponse({"detail": block_message(who.student, reason), "reason": reason,
                                 "message": block_message(who.student, reason)}, status_code=409)
        if not state.join(who.period, who.student):
            raise HTTPException(409, "Not on roster, already queued, or already out")
        await broadcast()
        return state.snapshot()

    @app.delete("/api/queue/{period}/{student}")
    async def leave(period: str, student: str):
        if state.scan_mode:
            raise HTTPException(403, "Ask your teacher to remove you from the queue")
        if not state.leave(period, student):
            raise HTTPException(404, "Not in queue")
        await broadcast()
        return state.snapshot()

    @app.post("/api/sim/docked")
    async def simulate(body: Docked):
        """Simulated sensor for development without hardware."""
        if not state.from_simulator(body.docked):
            raise HTTPException(403, "The simulator is not available (turned off, test controls hidden, or ID scan mode is on)")
        await broadcast()
        return state.snapshot()

    class Scan(BaseModel):
        id: str

    @app.post("/api/scan")
    async def scan(body: Scan):
        if len(body.id) > 200:
            raise HTTPException(422, "Too long")
        r = state.scan(body.id)
        if r["result"] == "scan_off":
            raise HTTPException(403, "ID scanning is turned off in Settings")
        await broadcast()
        status = 429 if r["result"] == "rate_limited" else 200
        return JSONResponse({**r, "message": scan_message(r)}, status_code=status)

    @app.websocket("/ws")
    async def ws(socket: WebSocket):
        await socket.accept()
        clients[socket] = "kiosk" if socket.query_params.get("role") == "kiosk" else ""
        await socket.send_json(state.snapshot(kiosk=(clients[socket] == "kiosk")))
        try:
            while True:
                await socket.receive_text()
        except WebSocketDisconnect:
            clients.pop(socket, None)

    # ----------------------------------------------------------------- admin
    def require_pin(x_admin_pin: Optional[str] = Header(None)):
        if admin_pin and not secrets.compare_digest((x_admin_pin or "").encode(), admin_pin.encode()):
            raise HTTPException(401, "Admin PIN required")

    admin = APIRouter(prefix="/api/admin", dependencies=[Depends(require_pin)])

    def parse_range(start, end):
        try:
            for d in (start, end):
                if d:
                    datetime.strptime(d, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(422, "Dates must be YYYY-MM-DD")

    @admin.get("/ping")
    def ping():
        return {"ok": True}

    class Setting(BaseModel):
        bar_style: Optional[str] = None
        simulator: Optional[str] = None
        test_controls: Optional[str] = None
        pass_seconds: Optional[str] = None
        bank_enabled: Optional[str] = None
        passes_suspended: Optional[str] = None
        bank_size: Optional[str] = None
        late_grace: Optional[str] = None
        late_warnings: Optional[str] = None
        late_debit: Optional[str] = None
        identify: Optional[str] = None
        scan_strict: Optional[str] = None
        call_timeout: Optional[str] = None
        max_trip_minutes: Optional[str] = None

    @admin.put("/settings")
    async def put_settings(body: Setting):
        changes = {k: v for k, v in body.dict().items() if v is not None}
        if not changes:
            raise HTTPException(422, "Nothing to change")
        if changes.get("simulator") == "off" and not state.sensor_configured:
            raise HTTPException(409, "No sensor is configured (set DESOTO_GPIO_PIN), so nothing could take the pass")
        if "identify" in changes and changes["identify"] != state.settings["identify"] and state.out:
            raise HTTPException(409, "Finish the trip in progress (Mark returned) before changing how students identify")
        for k, v in changes.items():
            if not state.set_setting(k, v):
                raise HTTPException(422, f"Invalid value for {k}")
        if changes.get("identify") == "tap":  # back to the pass: catch up with the sensor if it is in charge
            sensor = getattr(app.state, "sensor", None)
            if sensor is not None and not state.simulator_on:
                state.set_docked(sensor.docked)
        if changes.get("simulator") == "off":
            sensor = getattr(app.state, "sensor", None)
            if sensor is not None:  # hand control back to the sensor and catch up with where the pass really is
                state.set_docked(sensor.docked)
        if settings_path:
            data = dict(state.settings)
            if data.get("pass_seconds") == str(default_seconds):
                data.pop("pass_seconds")   # still the DESOTO_SECONDS default: let that setting keep working
            settings_path.write_text(json.dumps(data))
        await broadcast()
        return state.settings

    @admin.get("/sensor")
    def get_sensor():
        return {"configured": state.sensor_configured, "simulator": state.simulator_on, **monitor.snapshot()}

    @admin.post("/sensor/reset")
    def reset_sensor():
        monitor.reset()
        return {"ok": True}

    class PowerBody(BaseModel):
        confirm: bool = False

    @admin.get("/system")
    def system_info():
        if not admin_pin:
            return {"can_power": False, "why": "Set an admin PIN (DESOTO_ADMIN_PIN) to turn this on."}
        if not power.available():
            return {"can_power": False, "why": "Not available on this computer. On the Pi, run ./deploy/pi-install.sh --power-only."}
        return {"can_power": True, "why": ""}

    @admin.post("/system/{action}")
    def system_power(action: str, body: PowerBody):
        if action not in power.ACTIONS:
            raise HTTPException(404, "Unknown action")
        if not admin_pin:  # never let an open admin page power the computer off
            raise HTTPException(403, "Set an admin PIN (DESOTO_ADMIN_PIN) before using this")
        if not body.confirm:
            raise HTTPException(422, "Confirmation required")
        if not power.available():
            raise HTTPException(501, "This computer is not set up for it (see ./deploy/pi-install.sh --power-only)")
        power.schedule(action)
        return {"ok": True, "action": action}

    @admin.post("/queue/clear")
    async def clear_queue():
        n = state.clear_queue()
        await broadcast()
        return {"cleared": n}

    class AddToQueue(BaseModel):
        period: str
        student: str
        start: bool = False

    @admin.post("/queue/add")
    async def admin_add(body: AddToQueue):
        """Teacher adds a student by name (forgotten card), optionally starting their trip at once."""
        if body.start:
            if not state.start_now(body.period, body.student):
                raise HTTPException(409, "The pass is not free, or that student is not on the roster")
        elif not state.join(body.period, body.student, "teacher"):
            raise HTTPException(409, "Not on the roster, already in the queue, or already out")
        await broadcast()
        return state.snapshot()

    @admin.post("/queue/start")
    async def admin_start(body: AddToQueue):
        if not state.start_now(body.period, body.student):
            raise HTTPException(409, "The pass is not free, or that student is not on the roster")
        await broadcast()
        return state.snapshot()

    @admin.delete("/queue/{period}/{student}")
    async def admin_remove(period: str, student: str):
        if not state.leave(period, student):
            raise HTTPException(404, "Not in queue")
        await broadcast()
        return state.snapshot()

    @admin.post("/pass/return")
    async def return_pass():
        if not state.return_pass():
            raise HTTPException(409, "The pass is not out")
        await broadcast()
        return state.snapshot()

    # roster
    @admin.get("/roster")
    def get_roster():
        full = state.roster_full()
        return {"roster": full, "count": sum(len(v) for v in full.values())}

    class ImportBody(BaseModel):
        csv: str
        mode: str = "replace"  # replace | merge
        default_period: str = ""
        preview: bool = True

    @admin.post("/roster/import")
    async def import_roster(body: ImportBody):
        if len(body.csv.encode()) > MAX_CSV_BYTES:
            raise HTTPException(413, "File too large")
        if body.mode not in ("replace", "merge"):
            raise HTTPException(422, "mode must be replace or merge")
        parsed, warnings, errors = rosterlib.parse_csv(body.csv, body.default_period)
        if errors:
            return JSONResponse({"ok": False, "errors": errors, "warnings": warnings}, status_code=422)
        current = state.roster_full()
        result = parsed if body.mode == "replace" else rosterlib.merge(current, parsed)
        before, after = rosterlib.keys(current), rosterlib.keys(result)
        summary = {
            "ok": True, "applied": False, "mode": body.mode, "warnings": warnings,
            "total": len(after), "added": len(after - before), "removed": len(before - after),
            "periods": [{"period": p, "count": len(s), "sample": s[:6]} for p, s in result.items()],
        }
        if not body.preview:
            state.set_roster(result)
            save_roster()
            await broadcast()
            summary["applied"] = True
        return summary

    class NewStudent(BaseModel):
        period: str
        name: str
        id: str = ""

    @admin.post("/roster/student")
    async def add_student(body: NewStudent):
        period, name = rosterlib.normalize_period(body.period), " ".join(body.name.split())
        if not period or not name:
            raise HTTPException(422, "Period and name are required")
        full = state.roster_full()
        if name in state.roster.get(period, []):
            raise HTTPException(409, "That name is already in this period")
        full.setdefault(period, []).append({"name": name, "id": body.id.strip()})
        state.set_roster(rosterlib.normalize_roster(full))
        save_roster()
        await broadcast()
        return {"ok": True}

    @admin.delete("/roster/student")
    async def remove_student(period: str, name: str):
        full = state.roster_full()
        students = [s for s in full.get(period, []) if s["name"] != name]
        if len(students) == len(full.get(period, [])):
            raise HTTPException(404, "No such student")
        if students:
            full[period] = students
        else:
            del full[period]
        state.set_roster(full)
        save_roster()
        await broadcast()
        return {"ok": True}

    # activity log + data
    @admin.get("/log")
    def get_log(start: Optional[str] = None, end: Optional[str] = None, period: Optional[str] = None,
                q: Optional[str] = None, limit: int = 100, offset: int = 0):
        parse_range(start, end)
        rows, total = state.log.query(start, end, period, q, limit=min(limit, 1000), offset=offset)
        return {"rows": rows, "total": total}

    @admin.get("/log.csv")
    def get_log_csv(start: Optional[str] = None, end: Optional[str] = None,
                    period: Optional[str] = None, q: Optional[str] = None):
        parse_range(start, end)
        rows, _ = state.log.query(start, end, period, q)
        data = []
        for r in rows:
            data.append([
                datetime.fromtimestamp(r["out_at"]).strftime("%Y-%m-%d"), _safe(r["period"]), _safe(r["name"]),
                _safe(r["student_id"]), _fmt_time(r["out_at"]), _fmt_time(r["in_at"]),
                "" if r["seconds"] is None else round(r["seconds"] / 60, 2), round(r["allowed"] / 60, 2),
                "yes" if r["over"] else "no", r["status"], r["source"], r["method"]])
        return _csv_response("desoto-activity.csv",
                             ["Date", "Period", "Name", "Student ID", "Out", "In", "Minutes", "Allowed minutes",
                              "Over time", "Status", "Ended by", "Identified by"], data)

    def _stats(start, end, period):
        parse_range(start, end)
        rows, _ = state.log.query(start, end, period)
        return stats.compute(rows, state.roster_full(), period, end)

    @admin.get("/stats")
    def get_stats(start: Optional[str] = None, end: Optional[str] = None, period: Optional[str] = None):
        return _stats(start, end, period)

    @admin.get("/stats.csv")
    def get_stats_csv(start: Optional[str] = None, end: Optional[str] = None, period: Optional[str] = None):
        data = _stats(start, end, period)
        rows = [[_safe(s["period"]), _safe(s["name"]), _safe(s["id"]), s["trips"], round(s["total_seconds"] / 60, 2),
                 round(s["avg_seconds"] / 60, 2), round(s["longest_seconds"] / 60, 2), s["over"],
                 round(s["over_seconds"] / 60, 2),
                 datetime.fromtimestamp(s["last_out"]).strftime("%Y-%m-%d %H:%M") if s["last_out"] else ""]
                for s in data["students"]]
        return _csv_response("desoto-student-data.csv",
                             ["Period", "Name", "Student ID", "Trips", "Total minutes", "Average minutes",
                              "Longest minutes", "Over-time trips", "Minutes over", "Last trip"], rows)

    # pass bank
    def need_bank():
        if state.bank is None:
            raise HTTPException(501, "The pass bank needs the trip log")
        return state.bank

    def known(period: str, name: str):
        if name not in state.roster.get(period, []):
            raise HTTPException(404, "No such student")
        return state.ids.get((period, name), "")

    class BankWho(BaseModel):
        period: str
        name: str

    class BankAdjust(BankWho):
        delta: int
        note: str = ""

    class BankHold(BankWho):
        hold: bool
        note: str = ""

    class PeriodBody(BaseModel):
        name: str
        start: str
        end: str

    def today():
        return datetime.fromtimestamp(state.clock()).date()

    @admin.get("/bank")
    def get_bank():
        bank = need_bank()
        cur, d = bank.current_period(today()), today().isoformat()
        periods = [dict(p, status="current" if cur and cur["id"] == p["id"] else "past" if p["end"] < d else "future")
                   for p in bank.periods()]
        keys = ("bank_enabled", "bank_size", "late_grace", "late_warnings", "late_debit")
        return {"enabled": state.bank_on, "settings": {k: state.settings[k] for k in keys}, "periods": periods, "today": d,
                "students": bank.students(state.roster_full(), int(state.settings["bank_size"]))}

    @admin.post("/bank/adjust")
    async def bank_adjust(body: BankAdjust):
        bank, sid = need_bank(), known(body.period, body.name)
        if not -99 <= body.delta <= 99:
            raise HTTPException(422, "Change must be between -99 and 99")
        r = bank.adjust(body.period, body.name, sid, body.delta, "adjust", state.clock(),
                        int(state.settings["bank_size"]), body.note[:200])
        return r

    @admin.post("/bank/hold")
    async def bank_hold(body: BankHold):
        bank, sid = need_bank(), known(body.period, body.name)
        bank.set_hold(body.period, body.name, sid, body.hold, body.note[:200], state.clock(), int(state.settings["bank_size"]))
        if body.hold:
            state.leave(body.period, body.name)        # someone on hold does not stay in the line
            await broadcast()
        return {"ok": True}

    @admin.post("/bank/refill")
    async def bank_refill():
        need_bank()
        return {"students": state.refill_banks("Manual refill")}

    @admin.post("/bank/periods")
    def bank_add_period(body: PeriodBody):
        bank = need_bank()
        try:
            pid = bank.add_period(body.name, body.start, body.end)
        except ValueError as e:
            raise HTTPException(422, str(e))
        if (bank.current_period(today()) or {}).get("id") == pid:
            bank.set_meta("applied_period", str(pid))   # already in effect: don't refill mid-term, only when the next one starts
        state.recheck_periods()
        return {"id": pid}

    @admin.delete("/bank/periods/{period_id}")
    def bank_remove_period(period_id: int):
        if not need_bank().remove_period(period_id):
            raise HTTPException(404, "No such grading period")
        return {"ok": True}

    @admin.get("/bank/ledger")
    def bank_ledger(period: str, name: str):
        return {"rows": need_bank().ledger(period, name, known(period, name))}

    @admin.get("/bank.csv")
    def bank_csv():
        rows = need_bank().students(state.roster_full(), int(state.settings["bank_size"]))
        return _csv_response("desoto-pass-bank.csv", ["Period", "Name", "Student ID", "Passes left", "Late returns", "Status", "Note"],
                             [[_safe(r["period"]), _safe(r["name"]), _safe(r["id"]), r["remaining"], r["lates"],
                               "on hold" if r["hold"] else "active", _safe(r["note"])] for r in rows])

    app.include_router(admin)

    @app.get("/")
    def index():
        return FileResponse(STATIC / "kiosk.html")

    app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
    return app


app = create_app()

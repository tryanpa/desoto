import asyncio
import csv
import io
import json
import os
import secrets
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import roster as rosterlib
from . import stats
from .db import TripLog
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


def create_app(state: Optional[PassState] = None, settings_path: Optional[Path] = None,
               log: Optional[TripLog] = None, roster_path: Optional[Path] = None) -> FastAPI:
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
    if settings_path and settings_path.exists():
        for k, v in json.loads(settings_path.read_text()).items():
            state.set_setting(k, v)
    admin_pin = os.environ.get("DESOTO_ADMIN_PIN")

    app = FastAPI(title="Desoto")
    app.state.pass_state = state
    clients: set = set()

    async def broadcast() -> None:
        msg = state.snapshot()
        for ws in list(clients):
            try:
                await ws.send_json(msg)
            except Exception:
                clients.discard(ws)

    def on_sensor(docked: bool) -> None:
        state.set_docked(docked)
        asyncio.ensure_future(broadcast())

    @app.on_event("startup")
    async def startup() -> None:
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
        if not state.join(who.period, who.student):
            raise HTTPException(409, "Not on roster, already queued, or already out")
        await broadcast()
        return state.snapshot()

    @app.delete("/api/queue/{period}/{student}")
    async def leave(period: str, student: str):
        if not state.leave(period, student):
            raise HTTPException(404, "Not in queue")
        await broadcast()
        return state.snapshot()

    @app.post("/api/sim/docked")
    async def simulate(body: Docked):
        """Simulated sensor for development without hardware."""
        state.set_docked(body.docked)
        await broadcast()
        return state.snapshot()

    @app.websocket("/ws")
    async def ws(socket: WebSocket):
        await socket.accept()
        clients.add(socket)
        await socket.send_json(state.snapshot())
        try:
            while True:
                await socket.receive_text()
        except WebSocketDisconnect:
            clients.discard(socket)

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
        bar_style: str

    @admin.put("/settings")
    async def put_settings(body: Setting):
        if not state.set_setting("bar_style", body.bar_style):
            raise HTTPException(422, "Invalid bar_style")
        if settings_path:
            settings_path.write_text(json.dumps(state.settings))
        await broadcast()
        return state.settings

    @admin.post("/queue/clear")
    async def clear_queue():
        n = state.clear_queue()
        await broadcast()
        return {"cleared": n}

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
                "yes" if r["over"] else "no", r["status"], r["source"]])
        return _csv_response("desoto-activity.csv",
                             ["Date", "Period", "Name", "Student ID", "Out", "In", "Minutes", "Allowed minutes",
                              "Over time", "Status", "Ended by"], data)

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

    app.include_router(admin)

    @app.get("/")
    def index():
        return FileResponse(STATIC / "kiosk.html")

    app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
    return app


app = create_app()

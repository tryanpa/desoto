import asyncio
import json
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .state import DEFAULT_DURATION, PassState

ROOT = Path(__file__).resolve().parent.parent
STATIC = Path(__file__).parent / "static"


def create_app(state: PassState | None = None) -> FastAPI:
    if state is None:
        roster = json.loads(Path(os.environ.get("DESOTO_ROSTER", ROOT / "roster.json")).read_text())
        state = PassState(roster, int(os.environ.get("DESOTO_SECONDS", DEFAULT_DURATION)))
    app = FastAPI(title="Desoto")
    app.state.pass_state = state
    clients: set[WebSocket] = set()

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

    class Who(BaseModel):
        student: str

    class Docked(BaseModel):
        docked: bool

    @app.get("/api/state")
    def get_state():
        return state.snapshot()

    @app.post("/api/queue")
    async def join(who: Who):
        if not state.join(who.student):
            raise HTTPException(409, "Not on roster, already queued, or already out")
        await broadcast()
        return state.snapshot()

    @app.delete("/api/queue/{student}")
    async def leave(student: str):
        if not state.leave(student):
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

    @app.get("/")
    def index():
        return FileResponse(STATIC / "kiosk.html")

    app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
    return app


app = create_app()

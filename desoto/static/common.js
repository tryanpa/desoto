// Shared live-state client. Calls onState(state, secondsLeft()) on each tick.
function connect(onState) {
  let state = null, offset = 0;
  const left = () => state && state.out
    ? state.duration - (Date.now() / 1000 + offset - state.out.started_at) : null;
  function open() {
    const ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws");
    ws.onmessage = e => { state = JSON.parse(e.data); offset = state.now - Date.now() / 1000; };
    ws.onclose = () => setTimeout(open, 1000);
  }
  open();
  setInterval(() => state && onState(state, left()), 250);
}
const fmt = s => { const a = Math.abs(Math.floor(s)); return (s < 0 ? "+" : "") + Math.floor(a / 60) + ":" + String(a % 60).padStart(2, "0"); };

#!/usr/bin/env bash
# Launch Desoto with no flags:  ./start.sh
# Optional settings go in a file named .env next to this script (see .env.example).
set -e
cd "$(dirname "$0")"

# Settings from .env (DESOTO_ADMIN_PIN, DESOTO_SECONDS, PORT, ...)
if [ -f .env ]; then set -a; . ./.env; set +a; fi
PORT="${PORT:-8000}"

# Use an existing virtualenv (venv or .venv), or make one called venv
VENV=""
for d in venv .venv; do [ -x "$d/bin/python" ] && VENV="$d" && break; done
if [ -z "$VENV" ]; then
  VENV=venv
  echo "Creating virtualenv in ./$VENV ..."
  python3 -m venv "$VENV"
fi
PY="$VENV/bin/python"

# Install dependencies on first run and whenever requirements.txt changes
if [ ! -f "$VENV/.desoto-deps" ] || [ requirements.txt -nt "$VENV/.desoto-deps" ]; then
  echo "Installing dependencies ..."
  "$PY" -m pip install -q -r requirements.txt
  touch "$VENV/.desoto-deps"
fi
# Raspberry Pi hall sensor support, only when a sensor pin is configured
if [ -n "$DESOTO_GPIO_PIN" ] && ! "$PY" -c "import gpiozero" 2>/dev/null; then
  echo "Installing GPIO support ..."
  "$PY" -m pip install -q gpiozero lgpio
fi

# Show the addresses to open (works on macOS and Linux)
IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}' || true)"
HOST="${IP:-localhost}"
echo
echo "  Kiosk:    http://$HOST:$PORT/"
echo "  Display:  http://$HOST:$PORT/display.html"
echo "  Admin:    http://$HOST:$PORT/admin.html"
[ -z "$DESOTO_ADMIN_PIN" ] && echo "  (No admin PIN set: add DESOTO_ADMIN_PIN=1234 to .env to require one)"
echo "  Press Ctrl+C to stop."
echo

exec "$PY" -m uvicorn desoto.app:app --host 0.0.0.0 --port "$PORT"

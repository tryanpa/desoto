#!/usr/bin/env bash
# Raspberry Pi setup for Raspberry Pi OS (Trixie / Bookworm, desktop edition).
#
#   ./deploy/pi-install.sh                    Server + touch kiosk on THIS Pi (starts at every boot)
#   ./deploy/pi-install.sh display [SERVER]   A hallway-display Pi: shows SERVER's display page
#                                             (SERVER = hostname or IP, default desoto.local)
#
# Options: --dry-run (print what would happen, change nothing)   --no-kiosk (server only)
#          --hostname NAME   --gpio-pin N (default 17)   --pin ADMIN_PIN
#          --power-only   just allow the admin page's Shut down / Restart buttons (for an existing install)
set -euo pipefail

MODE=server; SERVER=desoto.local; DRY=0; KIOSK=1; NEWHOST=""; GPIO=17; ADMIN_PIN=""; PIN_SET=0; POWER_ONLY=0
while [ $# -gt 0 ]; do
  case "$1" in
    display) MODE=display; if [ $# -gt 1 ] && [ "${2#--}" = "$2" ]; then SERVER="$2"; shift; fi ;;
    --dry-run) DRY=1 ;;
    --no-kiosk) KIOSK=0 ;;
    --power-only) POWER_ONLY=1 ;;
    --hostname) NEWHOST="$2"; shift ;;
    --gpio-pin) GPIO="$2"; shift ;;
    --pin) ADMIN_PIN="$2"; PIN_SET=1; shift ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

cd "$(dirname "$0")/.."
DIR="$(pwd)"
say() { printf '\n==> %s\n' "$*"; }
die() { echo "Error: $*" >&2; exit 1; }
run() { if [ "$DRY" = 1 ]; then echo "  + $*"; else "$@"; fi; }
write() {  # write FILE  (content on stdin); sudo when the path is not ours
  local f="$1" sudo=""; [ -w "$(dirname "$f")" ] || sudo=sudo
  if [ "$DRY" = 1 ]; then echo "  + write $f:"; sed 's/^/      | /'; else $sudo mkdir -p "$(dirname "$f")"; $sudo tee "$f" >/dev/null; fi
}

[ "$DRY" = 1 ] || [ "$(id -u)" -ne 0 ] || die "Run as your normal user, not root (the script uses sudo where it needs to)."
if [ "$DRY" = 0 ] && ! grep -qi "raspberry" /proc/device-tree/model 2>/dev/null; then
  die "This doesn't look like a Raspberry Pi. Use --dry-run to preview."
fi

install_power_rule() {
  # Lets this user run exactly `systemctl poweroff` and `systemctl reboot` without a password (and nothing else),
  # which is how the admin page's Shut down / Restart buttons work.
  local ctl tmp=/etc/sudoers.d/desoto-power.tmp
  ctl="$(readlink -f "$(command -v systemctl 2>/dev/null || echo /usr/bin/systemctl)")"
  say "Letting the admin page shut down / restart this Pi (a narrow sudo rule for $(id -un))"
  write "$tmp" <<RULE
$(id -un) ALL=(root) NOPASSWD: $ctl poweroff, $ctl reboot
RULE
  run sudo chmod 440 "$tmp"
  run sudo visudo -cf "$tmp"          # refuses to continue if the rule has a mistake
  run sudo mv "$tmp" /etc/sudoers.d/desoto-power
}

setup_kiosk() {  # URL
  local url="$1" auto="$HOME/.config/labwc/autostart"
  say "Opening $url full screen at every login"
  run sudo raspi-config nonint do_boot_behaviour B4      # boot to desktop, logged in automatically
  run sudo raspi-config nonint do_blanking 1             # never blank or sleep the screen
  run mkdir -p "$(dirname "$auto")"
  if [ -f "$auto" ] && [ "$DRY" = 0 ]; then cp -n "$auto" "$auto.bak-desoto"; sed -i '/desoto-kiosk/d' "$auto"; fi
  if [ "$DRY" = 1 ]; then echo "  + append to $auto:"; echo "      | \"$DIR/deploy/kiosk.sh\" '$url' &  # desoto-kiosk"
  else echo "\"$DIR/deploy/kiosk.sh\" '$url' &  # desoto-kiosk" >> "$auto"; fi
  run chmod +x "$DIR/deploy/kiosk.sh"
}

if [ "$POWER_ONLY" = 1 ]; then install_power_rule; say "Done. Restart the server: sudo systemctl restart desoto"; exit 0; fi

if [ -n "$NEWHOST" ]; then
  say "Setting the hostname to $NEWHOST (reachable as $NEWHOST.local)"
  run sudo raspi-config nonint do_hostname "$NEWHOST"
fi

if [ "$MODE" = display ]; then
  say "Hallway display Pi: installing the browser"
  run sudo apt-get update -qq
  run sudo apt-get install -y chromium curl
  case "$SERVER" in http*) URL="${SERVER%/}/display.html" ;; *) URL="http://$SERVER:8000/display.html" ;; esac
  setup_kiosk "$URL"
  say "Done. Reboot to start: sudo reboot"; exit 0
fi

say "Installing packages (Python, GPIO support, Chromium)"
run sudo apt-get update -qq
run sudo apt-get install -y python3-venv python3-gpiozero python3-lgpio chromium curl

say "Python environment"
# --system-site-packages so the apt-installed GPIO libraries are visible inside the venv
if [ -d venv ] && ! grep -q 'include-system-site-packages = true' venv/pyvenv.cfg 2>/dev/null; then
  echo "  (recreating ./venv so it can see the system GPIO libraries)"; run rm -rf venv
fi
[ -d venv ] || run python3 -m venv --system-site-packages venv
run venv/bin/python -m pip install -q -r requirements.txt
run touch venv/.desoto-deps

if [ ! -f .env ]; then
  say "Settings (.env)"
  if [ "$PIN_SET" = 0 ] && [ -t 0 ]; then read -r -p "  Admin PIN for the admin pages (blank = none): " ADMIN_PIN; fi
  { [ -n "$ADMIN_PIN" ] && echo "DESOTO_ADMIN_PIN=$ADMIN_PIN"; echo "DESOTO_GPIO_PIN=$GPIO"; } | write "$DIR/.env"
else
  echo "  (keeping your existing .env)"
fi

say "Starting the server at every boot (systemd service: desoto)"
write /etc/systemd/system/desoto.service <<UNIT
[Unit]
Description=Desoto bathroom pass server
After=network-online.target
Wants=network-online.target

[Service]
User=$(id -un)
WorkingDirectory=$DIR
Environment=PORT=8000
EnvironmentFile=-$DIR/.env
ExecStart=$DIR/venv/bin/python -m uvicorn desoto.app:app --host 0.0.0.0 --port \${PORT}
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT
run sudo systemctl daemon-reload
run sudo systemctl enable --now desoto
install_power_rule

[ "$KIOSK" = 1 ] && setup_kiosk "http://localhost:8000/"

say "Done."
echo "  Check the server:   systemctl status desoto      (logs: journalctl -u desoto -f)"
NAME="${NEWHOST:-$(hostname)}"
echo "  Admin page:         http://$NAME.local:8000/admin.html"
echo "  Hallway display Pi: ./deploy/pi-install.sh display $NAME.local   (or the IP address of this Pi)"
[ "$KIOSK" = 1 ] && echo "  Reboot to start the kiosk: sudo reboot"

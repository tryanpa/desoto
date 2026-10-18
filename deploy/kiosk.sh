#!/usr/bin/env bash
# Open a page full screen in Chromium and keep it open.   Usage: deploy/kiosk.sh URL
# Started at login by ~/.config/labwc/autostart (see deploy/pi-install.sh).
URL="${1:?usage: kiosk.sh URL}"
BROWSER="$(command -v chromium || command -v chromium-browser || true)"
[ -n "$BROWSER" ] || { echo "Chromium is not installed" >&2; exit 1; }

# The server may still be starting at boot (or on another Pi): wait until the page answers.
until curl -fs -o /dev/null "$URL"; do sleep 2; done

# Reopen the browser if it is closed or crashes.
while true; do
  "$BROWSER" "$URL" --kiosk --noerrdialogs --disable-infobars \
    --disable-session-crashed-bubble --hide-crash-restore-bubble \
    --disable-pinch --overscroll-history-navigation=0 --disable-pull-to-refresh-effect \
    --password-store=basic --disable-features=Translate --check-for-update-interval=2592000 \
    --ozone-platform-hint=auto --user-data-dir="$HOME/.config/desoto-kiosk"
  sleep 3
done

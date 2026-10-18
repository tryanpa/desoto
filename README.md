# Desoto

Bathroom pass tracker. Students tap their class period, then their name, on a kiosk to join a queue. Lifting the
physical pass off its dock (hall effect sensor) starts a 7-minute countdown for the next
student in line; returning it stops the clock. A hallway display shows who is out, the
countdown (turns red and counts up when over time), and who is next.

## Run

    pip install -r requirements.txt
    uvicorn desoto.app:app --host 0.0.0.0 --port 8000

- Kiosk: `http://<host>:8000/` (includes simulator buttons for take/return pass)
- Display: `http://<host>:8000/display.html`
- Roster: edit `roster.json` (`{"period": ["names"]}`) (or `DESOTO_ROSTER=path`). Timer: `DESOTO_SECONDS` (default 420).

## Raspberry Pi hall sensor

Set `DESOTO_GPIO_PIN=<BCM pin>` and wire an open-collector hall sensor (e.g. A3144) between
the pin and ground (3.3V supply, internal pull-up used). A magnet in the pass pulls the pin
low while docked; removing the pass starts the timer.

## Test

    pytest

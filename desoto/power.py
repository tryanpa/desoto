"""Shut down / restart the computer from the admin page (Linux only, through one narrow sudo rule).

deploy/pi-install.sh adds /etc/sudoers.d/desoto-power, which lets the Desoto service's user run exactly
`systemctl poweroff` and `systemctl reboot` without a password, and nothing else.
"""
import os
import shutil
import subprocess
import sys
from typing import Optional

ACTIONS = {"shutdown": "poweroff", "restart": "reboot"}


def _systemctl() -> Optional[str]:
    path = shutil.which("systemctl")
    return os.path.realpath(path) if path else None


def available() -> bool:
    """True when this computer can power itself off for us (Linux, systemd, and the sudo rule is installed)."""
    ctl, sudo = _systemctl(), shutil.which("sudo")
    if not sys.platform.startswith("linux") or not ctl or not sudo:
        return False
    try:
        # `sudo -n -l CMD` succeeds only if CMD is allowed without a password. It runs nothing.
        return subprocess.run([sudo, "-n", "-l", ctl, "poweroff"], capture_output=True, timeout=5).returncode == 0
    except Exception:
        return False


def schedule(action: str, delay: int = 3) -> None:
    """Do it in a few seconds, so the page gets its answer first."""
    verb = ACTIONS[action]
    subprocess.Popen(["sh", "-c", f"sleep {int(delay)}; exec sudo -n {_systemctl()} {verb}"],
                     start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

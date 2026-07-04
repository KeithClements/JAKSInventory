"""
Axle ERP auto-start installer (Windows Task Scheduler).

Run in an **Administrator** terminal from the rebuild folder:

  python service_install.py install   -- register "Axle ERP" to start at logon
  python service_install.py start     -- start it now
  python service_install.py stop      -- stop it (ends the running task)
  python service_install.py status    -- show whether it's installed / running
  python service_install.py remove    -- unregister it

Why Task Scheduler and not a Windows service?
---------------------------------------------
The previous pywin32 service implementation could not work: it referenced a
class-registration attribute pywin32 does not define (so ``install`` crashed) and
it exited immediately when the service manager launched it. Rather than fight
that library, this registers a Scheduled Task that runs the SUPERVISED headless runner
``scripts/axle_service_run.bat`` — which loops uvicorn so a crash auto-restarts.
The task itself starts the app at logon; the runner keeps it up. This is the
robust, low-ceremony way to "keep the app running + restart it after a crash or
reboot" on a single Windows box.

The task runs the app at http://localhost:8000 (reachable on the LAN at this PC's
IP). Stop it any time with ``python service_install.py stop`` (or Task Scheduler).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

TASK_NAME = "Axle ERP"
REPO_DIR = Path(__file__).resolve().parent
RUNNER = REPO_DIR / "scripts" / "axle_service_run.bat"


def _schtasks(*args: str) -> int:
    """Run schtasks with the given args; echo the command and return its code."""
    cmd = ["schtasks", *args]
    print("  $", " ".join(cmd))
    return subprocess.call(cmd)


def install() -> int:
    if not RUNNER.exists():
        print(f"[ERROR] runner not found: {RUNNER}")
        return 1
    # Idempotent: remove any prior registration first, then (re)create.
    _schtasks("/Delete", "/TN", TASK_NAME, "/F")
    code = _schtasks(
        "/Create", "/TN", TASK_NAME,
        "/TR", f'"{RUNNER}"',
        "/SC", "ONLOGON",
        "/RL", "HIGHEST",
        "/F",
    )
    if code == 0:
        print(f'\nInstalled scheduled task "{TASK_NAME}".')
        print("  - It starts Axle at logon; the runner auto-restarts uvicorn on crash.")
        print("  - Start it now:  python service_install.py start")
    else:
        print("\n[ERROR] Could not create the task. Run this in an Administrator terminal.")
    return code


def start() -> int:
    return _schtasks("/Run", "/TN", TASK_NAME)


def stop() -> int:
    return _schtasks("/End", "/TN", TASK_NAME)


def status() -> int:
    return _schtasks("/Query", "/TN", TASK_NAME, "/V", "/FO", "LIST")


def remove() -> int:
    return _schtasks("/Delete", "/TN", TASK_NAME, "/F")


_COMMANDS = {
    "install": install,
    "start": start,
    "stop": stop,
    "status": status,
    "remove": remove,
}


def main(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0].lower() not in _COMMANDS:
        print(__doc__)
        return 0 if not argv else 2
    return _COMMANDS[argv[0].lower()]()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

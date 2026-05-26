"""
Windows Service installer for JAKS Inventory.

Usage (run as Administrator):
  python service_install.py install   -- install the service
  python service_install.py start     -- start the service
  python service_install.py stop      -- stop the service
  python service_install.py remove    -- remove the service
  python service_install.py restart   -- restart the service

The service starts automatically on Windows boot and serves the app at:
  http://localhost:8000
"""
import sys
import os
import threading
from pathlib import Path

# Ensure we run from the rebuild directory so relative paths work
os.chdir(Path(__file__).resolve().parent)

try:
    import win32serviceutil
    import win32service
    import win32event
    import servicemanager
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False
    print("pywin32 not installed — run: pip install pywin32")

import uvicorn


class JAKSInventoryService(win32serviceutil.ServiceFramework):
    _svc_name_ = "JAKSInventory"
    _svc_display_name_ = "JAKS Inventory Server"
    _svc_description_ = "JAKS Inventory local web server (http://localhost:8000)"

    def __init__(self, args):
        win32serviceutil.ServiceFramework.__init__(self, args)
        self._stop_event = win32event.CreateEvent(None, 0, 0, None)
        self._server = None

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        if self._server:
            self._server.should_exit = True
        win32event.SetEvent(self._stop_event)

    def SvcDoRun(self):
        servicemanager.LogMsg(
            servicemanager.EVENTLOG_INFORMATION_TYPE,
            servicemanager.PYS_SERVICE_STARTED,
            (self._svc_name_, ""),
        )
        os.chdir(Path(__file__).resolve().parent)
        config = uvicorn.Config(
            "app.main:app",
            host="0.0.0.0",
            port=8000,
            log_level="info",
        )
        self._server = uvicorn.Server(config)
        thread = threading.Thread(target=self._server.run, daemon=True)
        thread.start()
        win32event.WaitForSingleObject(self._stop_event, win32event.INFINITE)
        thread.join(timeout=15)


if __name__ == "__main__":
    if not HAS_WIN32:
        sys.exit(1)

    if len(sys.argv) == 1:
        print(__doc__)
        sys.exit(0)

    cmd = sys.argv[1].lower()
    if cmd in ("install", "update"):
        win32serviceutil.InstallService(
            JAKSInventoryService._svc_reg_class_,
            JAKSInventoryService._svc_name_,
            JAKSInventoryService._svc_display_name_,
            startType=win32service.SERVICE_AUTO_START,
            description=JAKSInventoryService._svc_description_,
            exeName=sys.executable,
            exeArgs=f'"{Path(__file__).resolve()}"',
        )
        print(f"Service '{JAKSInventoryService._svc_display_name_}' installed.")
        print("Run: python service_install.py start")
    elif cmd == "start":
        win32serviceutil.StartService(JAKSInventoryService._svc_name_)
        print("Service started. Open http://localhost:8000")
    elif cmd == "stop":
        win32serviceutil.StopService(JAKSInventoryService._svc_name_)
        print("Service stopped.")
    elif cmd == "restart":
        win32serviceutil.RestartService(JAKSInventoryService._svc_name_)
        print("Service restarted.")
    elif cmd == "remove":
        win32serviceutil.RemoveService(JAKSInventoryService._svc_name_)
        print("Service removed.")
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)

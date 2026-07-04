"""Local launcher. Run: python run.py

Reload is OFF by default — uvicorn --reload is documented to STALL on Windows
(routes 404, zombie port 8000). Opt in for active development with JAKS_RELOAD=1.
For the shop box, use "Start JAKS ERP.bat" (loads the Fernet key + production env)
or install auto-start via `python service_install.py install`.
"""
import os
import uvicorn

if __name__ == "__main__":
    reload = os.getenv("JAKS_RELOAD") == "1"
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=reload,
        reload_dirs=["app"] if reload else None,
    )

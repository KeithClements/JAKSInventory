from __future__ import annotations

import logging
import logging.handlers
import sys
import threading
import traceback
from pathlib import Path
from types import TracebackType

from dotenv import load_dotenv

# Load .env before any other imports that read env vars
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from PySide6.QtWidgets import QApplication, QMessageBox

from .ui.main_window import InventoryMainWindow


# Resolve the log directory once so the excepthook can include the path in
# its dialog without re-deriving it.
_LOG_DIR = Path(__file__).resolve().parent.parent / "output" / "logs"
_LOG_FILE = _LOG_DIR / "jaks_inventory.log"

log = logging.getLogger(__name__)


def _configure_logging() -> None:
    """Configure root logging: stderr at INFO + rotating file at DEBUG.

    Called before any UI imports run their module-level code so that
    warnings emitted during app construction are captured.
    """
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    # Idempotent: don't double-attach handlers if main() runs twice
    # (e.g. inside a test harness).
    if getattr(root, "_jaks_configured", False):
        return
    root.setLevel(logging.DEBUG)

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    stream = logging.StreamHandler(sys.stderr)
    stream.setLevel(logging.INFO)
    stream.setFormatter(fmt)
    root.addHandler(stream)

    file_handler = logging.handlers.RotatingFileHandler(
        _LOG_FILE,
        maxBytes=2 * 1024 * 1024,  # 2 MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    # Quiet third-party noise.
    for noisy in ("PIL", "urllib3", "playwright", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    root._jaks_configured = True  # type: ignore[attr-defined]


# Module-level state for excepthook dedupe.
_last_excepthook_ts: float = 0.0


def _install_excepthooks(app: QApplication) -> None:
    """Install sys.excepthook + threading.excepthook to surface crashes.

    GUI-thread exceptions get a QMessageBox; worker-thread exceptions are
    logged only (Qt dialogs from non-GUI threads are unsafe).
    """
    import time

    def _gui_excepthook(
        exc_type: type[BaseException],
        exc_value: BaseException,
        exc_tb: TracebackType | None,
    ) -> None:
        global _last_excepthook_ts
        log.exception(
            "Uncaught exception", exc_info=(exc_type, exc_value, exc_tb)
        )

        # Suppress duplicate dialogs within 2 s — a repeating Qt slot
        # would otherwise spam modal popups.
        now = time.monotonic()
        if now - _last_excepthook_ts < 2.0:
            return
        _last_excepthook_ts = now

        tb_text = "".join(
            traceback.format_exception(exc_type, exc_value, exc_tb)
        )
        first_line = f"{exc_type.__name__}: {exc_value}".splitlines()[0]
        try:
            box = QMessageBox()
            box.setIcon(QMessageBox.Icon.Critical)
            box.setWindowTitle("Unexpected error")
            box.setText(first_line)
            box.setInformativeText(
                f"Details have been written to:\n{_LOG_FILE}"
            )
            ok_btn = box.addButton(QMessageBox.StandardButton.Ok)
            copy_btn = box.addButton(
                "Copy traceback", QMessageBox.ButtonRole.ActionRole
            )
            box.exec()
            if box.clickedButton() is copy_btn:
                clip = QApplication.clipboard()
                if clip is not None:
                    clip.setText(tb_text)
        except Exception:  # pragma: no cover - dialog itself failed
            log.exception("Failed to show excepthook dialog")

    def _thread_excepthook(args: threading.ExceptHookArgs) -> None:
        log.error(
            "Uncaught exception in thread %s",
            args.thread.name if args.thread else "<unknown>",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    sys.excepthook = _gui_excepthook
    threading.excepthook = _thread_excepthook


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)

    _configure_logging()
    log.info("Starting jaks_inventory")

    app = QApplication(argv)
    _install_excepthooks(app)

    # Apply persisted UI theme (light/dark) before any window is shown.
    try:
        from .ui.theme import apply_theme
        apply_theme(app)
    except Exception as exc:
        log.warning("Theme apply skipped: %s", exc)

    # P0.2 — self-heal any qty_reserved drift before showing the UI so
    # availability numbers downstream are trustworthy. Failure here must
    # never block the app launch.
    try:
        from db import reconcile_qty_reserved
        recon = reconcile_qty_reserved(auto_fix=True)
        if recon.get("fixed"):
            log.info(
                "Reconciled %d qty_reserved drift(s) on startup (total |delta|=%d)",
                recon["fixed"], recon.get("total_drift", 0),
            )
    except Exception as exc:
        log.warning("qty_reserved reconcile skipped: %s", exc)

    win = InventoryMainWindow()
    win.show()
    return app.exec()

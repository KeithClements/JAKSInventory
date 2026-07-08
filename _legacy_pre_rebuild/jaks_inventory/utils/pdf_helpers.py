"""Helpers for working with PDF generation in the GUI.

These wrap a generator function with a friendly retry / "Save as Copy" prompt
when the destination PDF is locked (e.g. open in Adobe Reader on Windows).
"""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime
from typing import Callable, Optional

from PySide6.QtWidgets import QMessageBox, QWidget


def open_with_default_viewer(path: str) -> None:
    """Open *path* with the OS default application."""
    if sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def safe_generate_pdf(
    generator: Callable[..., str],
    *args,
    parent: Optional[QWidget] = None,
    open_after: bool = True,
    **kwargs,
) -> Optional[str]:
    """Run *generator* and handle PermissionError with a friendly retry dialog.

    On PermissionError (file locked by another app), prompt the user with:
      - Retry: try again at the original path
      - Save as Copy: append a timestamp to the filename and try once more
      - Cancel: abort
    Returns the resulting path on success, or None on cancel/failure.
    """
    while True:
        try:
            path = generator(*args, **kwargs)
            if open_after and path:
                try:
                    open_with_default_viewer(path)
                except Exception:
                    # Opening the viewer is best-effort
                    pass
            return path
        except PermissionError as exc:
            # Try to extract the locked filename from the exception
            locked = getattr(exc, "filename", None) or str(exc)
            box = QMessageBox(parent)
            box.setIcon(QMessageBox.Icon.Warning)
            box.setWindowTitle("PDF File Locked")
            box.setText(
                "The PDF file is currently open in another program "
                "(usually a PDF viewer) and cannot be overwritten."
            )
            box.setInformativeText(
                f"File:\n{locked}\n\n"
                "Close it and click Retry, or click Save as Copy to write a new "
                "timestamped file instead."
            )
            retry_btn = box.addButton("Retry", QMessageBox.ButtonRole.AcceptRole)
            copy_btn = box.addButton("Save as Copy", QMessageBox.ButtonRole.ActionRole)
            cancel_btn = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
            box.setDefaultButton(retry_btn)
            box.exec()
            clicked = box.clickedButton()

            if clicked is retry_btn:
                continue
            if clicked is copy_btn:
                # Force a fresh output_path on the next attempt
                ts = datetime.now().strftime("%Y%m%d-%H%M%S")
                if isinstance(locked, str) and os.path.isabs(locked) and locked.lower().endswith(".pdf"):
                    base, ext = os.path.splitext(locked)
                    new_path = f"{base}_{ts}{ext}"
                    kwargs["output_path"] = new_path
                else:
                    # Let the generator pick its default dir but suffix the name
                    kwargs["output_path"] = None
                continue
            # cancel
            return None
        except Exception as exc:
            QMessageBox.critical(
                parent,
                "PDF Error",
                f"Failed to generate PDF:\n{exc}",
            )
            return None

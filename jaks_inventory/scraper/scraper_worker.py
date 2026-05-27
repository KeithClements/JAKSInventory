"""QThread-based scraper worker for background execution in the UI.

Emits Qt signals for progress, completion, and errors so the calling screen
can update widgets safely from the main thread.

Usage example (from a screen):
    worker = ScraperWorker("xref", sku="C15103-010")
    worker.progress.connect(self._on_scrape_progress)
    worker.finished.connect(self._on_scrape_done)
    worker.error.connect(self._on_scrape_error)
    worker.start()
"""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from .scraper_service import (
    scrape_xrefs_for_product,
    scrape_competitor_prices_for_product,
    scrape_pai_images_for_product,
)


class ScraperWorker(QThread):
    """Background thread that runs a scrape job.

    Parameters
    ----------
    job_type : ``"xref"`` | ``"pricing"`` | ``"images"`` | ``"both"``
        ``"both"`` runs xref + pricing + PAI images sequentially.
    search_value : str
        The part number to search competitor sites for (vendor SKU, OEM part#,
        or user override).  **Never** the internal JAKS-XXXX SKU.
    alt_terms : list[str] | None
        Additional part numbers to try if the primary search_value returns
        no results for a given source (e.g. OEM part # alongside vendor SKU).
    product_id : int | None
        Optional product id to link results.
    """

    progress = Signal(int, int, str)       # current, total, message
    finished = Signal(dict)                # summary dict from service
    error = Signal(str)                    # error message

    def __init__(
        self,
        job_type: str,
        search_value: str,
        *,
        alt_terms: list[str] | None = None,
        product_id: int | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._job_type = job_type
        self._search_value = search_value
        self._alt_terms = alt_terms
        self._product_id = product_id

    def run(self) -> None:
        try:
            print(f"[ScraperWorker] Starting {self._job_type} for '{self._search_value}' alt={self._alt_terms} (pid={self._product_id})")
            results: dict = {}
            if self._job_type in ("xref", "both"):
                r = scrape_xrefs_for_product(
                    self._search_value,
                    product_id=self._product_id,
                    on_progress=self._emit_progress,
                    alt_terms=self._alt_terms,
                )
                results["xref"] = r
            if self._job_type in ("pricing", "both"):
                r = scrape_competitor_prices_for_product(
                    self._search_value,
                    product_id=self._product_id,
                    on_progress=self._emit_progress,
                    alt_terms=self._alt_terms,
                )
                results["pricing"] = r
            if self._job_type in ("images", "both"):
                try:
                    r = scrape_pai_images_for_product(
                        self._search_value,
                        product_id=self._product_id,
                        on_progress=self._emit_progress,
                        alt_terms=self._alt_terms,
                    )
                except Exception as img_exc:
                    # Non-fatal — PAI is optional. Keep main scrape result.
                    r = {
                        "searched": 0, "found": 0, "downloaded": 0,
                        "errors": [f"PAI images: {img_exc}"],
                    }
                    print(f"[ScraperWorker] PAI images error (non-fatal): {img_exc}")
                results["images"] = r
            print(f"[ScraperWorker] Done: {results}")
            self.finished.emit(results)
        except Exception as exc:
            print(f"[ScraperWorker] ERROR: {exc}")
            self.error.emit(str(exc))

    # ------------------------------------------------------------------

    def _emit_progress(self, current: int, total: int, message: str) -> None:
        self.progress.emit(current, total, message)

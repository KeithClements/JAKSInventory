from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import threading
from urllib.parse import urljoin


@dataclass(frozen=True)
class EstimateResult:
    count: int
    urls: list[str]
    hhp_numbers: list[str]
    items: list[dict[str, Any]]


def _extract_hhp_number_from_url(url: str) -> str:
    """Best-effort HHP number from product URL slug.

    Discovery is meant to be non-destructive; this returns a hint only.
    """

    try:
        from phases.scraper import extract_primary_part_from_url

        return str(extract_primary_part_from_url(url) or "").strip()
    except Exception:
        return ""


def estimate_category(
    start_url: str,
    *,
    new_only: bool = False,
    cancel_event: threading.Event | None = None,
    signals: Any | None = None,
) -> EstimateResult:
    """Phase 1: paginate category listing and return all product URLs.

    Returns:
      - count
      - urls (deduped)
      - hhp_numbers (best-effort derived from URL)
      - items (dicts persisted-friendly; include stage=1)
    """

    import requests
    from bs4 import BeautifulSoup

    start_url = str(start_url or "").strip()
    if not start_url:
        return EstimateResult(count=0, urls=[], hhp_numbers=[], items=[])

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }

    def _log(msg: str) -> None:
        try:
            if signals is not None and hasattr(signals, "log"):
                signals.log.emit(msg)
        except Exception:
            pass

    items: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    url: str | None = start_url
    page_num = 1
    while url:
        if cancel_event is not None and cancel_event.is_set():
            _log("Estimate cancelled")
            break

        _log(f"Fetching page {page_num}: {url}")
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code != 200:
            raise RuntimeError(f"Bad response: {resp.status_code}")

        soup = BeautifulSoup(resp.text, "html.parser")
        link_els = soup.select("a[href*='/product/']")
        for a in link_els:
            href = (a.get("href") or "").strip()
            if not href or "/product/" not in href:
                continue
            abs_url = href if href.startswith("http") else urljoin(url, href)
            if new_only and ("reman" in abs_url.lower() or "remanufactured" in abs_url.lower()):
                continue
            if abs_url in seen_urls:
                continue
            seen_urls.add(abs_url)

            hhp = _extract_hhp_number_from_url(abs_url)
            items.append({"url": abs_url, "part": "", "hhp_number": hhp, "stage": 1})

        next_link = soup.select_one(".woocommerce-pagination a.next")
        if next_link and next_link.get("href"):
            url = str(next_link.get("href"))
            page_num += 1
        else:
            url = None

    urls = [str(i.get("url") or "").strip() for i in items if str(i.get("url") or "").strip()]
    hhp_numbers = [str(i.get("hhp_number") or "").strip() for i in items]
    return EstimateResult(count=len(urls), urls=urls, hhp_numbers=hhp_numbers, items=items)

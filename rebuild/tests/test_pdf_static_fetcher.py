"""
tests/test_pdf_static_fetcher.py
================================
Regression for the emailed/downloaded-PDF "missing logo + missing local
thumbnail" bug.

WeasyPrint must read the app's own ``/static`` assets straight from disk during
PDF generation. Without a custom ``url_fetcher`` it resolves ``/static/…`` srcs
to an HTTP request back to the SAME single-process uvicorn server — which is
blocked synchronously inside ``write_pdf()`` on the event loop — so the company
logo and any locally-uploaded product thumbnail silently drop. External images
(e.g. the PAI CDN) hit a different host and were unaffected, which is exactly
why only the local assets went missing in the field.
"""
import pytest

from app.services import document_render as dr


def test_local_static_url_resolves_to_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(dr, "_STATIC_ROOT", tmp_path)
    f = tmp_path / "uploads" / "logo.png"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"\x89PNG\r\n\x1a\nDATA")
    # base_url makes a relative "/static/…" src absolute; every host form maps.
    for url in (
        "/static/uploads/logo.png",
        "http://127.0.0.1:8000/static/uploads/logo.png",
        "http://192.168.1.5:8000/static/uploads/logo.png",
    ):
        assert dr._local_static_path(url) == f.resolve()


def test_external_traversal_and_missing_fall_through(tmp_path, monkeypatch):
    monkeypatch.setattr(dr, "_STATIC_ROOT", tmp_path)
    (tmp_path / "uploads").mkdir()
    # External CDN image -> not ours, default fetcher handles it.
    assert dr._local_static_path(
        "https://cache.paiindustries.com/x/340097_01_XL.jpg") is None
    # data: URI -> not ours.
    assert dr._local_static_path("data:image/png;base64,iVBOR") is None
    # ../ must not escape the static root.
    assert dr._local_static_path("http://h/static/../../etc/passwd") is None
    # A /static path with no file on disk returns None (never raises).
    assert dr._local_static_path("/static/uploads/nope.png") is None


def test_fetcher_reads_local_bytes_with_mime(tmp_path, monkeypatch):
    monkeypatch.setattr(dr, "_STATIC_ROOT", tmp_path)
    f = tmp_path / "uploads" / "company_logo.png"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"\x89PNGbytes")
    res = dr.static_url_fetcher(
        "http://127.0.0.1:8000/static/uploads/company_logo.png")
    assert res["string"] == b"\x89PNGbytes"
    assert res["mime_type"] == "image/png"


def test_fetcher_pins_woff2_mime(tmp_path, monkeypatch):
    # mimetypes doesn't reliably know woff2; the fetcher must pin it so
    # self-hosted print fonts embed with the right type.
    monkeypatch.setattr(dr, "_STATIC_ROOT", tmp_path)
    f = tmp_path / "fonts" / "barlow.woff2"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"wOF2data")
    res = dr.static_url_fetcher("/static/fonts/barlow.woff2")
    assert res["mime_type"] == "font/woff2"


def test_fetcher_delegates_external_to_default(monkeypatch):
    # This is the only test that forces `import weasyprint`, which loads its
    # native GTK stack (libgobject/pango/cairo). A bare CI runner (windows-latest)
    # doesn't ship those, so the import raises OSError there — NOT ImportError,
    # so pytest.importorskip can't catch it. Skip gracefully when the native libs
    # are absent; this still runs on the shop's deploy box where GTK is installed.
    try:
        import weasyprint  # noqa: F401
    except OSError as exc:  # native GTK libs unavailable (e.g. bare CI runner)
        pytest.skip(f"WeasyPrint native libraries unavailable: {exc}")

    seen = {}

    def fake_default(url, *a, **k):
        seen["url"] = url
        return {"string": b"", "mime_type": "image/jpeg"}

    monkeypatch.setattr("weasyprint.default_url_fetcher", fake_default)
    url = "https://cache.paiindustries.com/x/340097_01_XL.jpg"
    res = dr.static_url_fetcher(url)
    assert seen["url"] == url
    assert res["mime_type"] == "image/jpeg"

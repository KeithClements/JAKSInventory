"""
tests/test_csrf_fetch_wrapper.py
================================
Pins the global fetch() CSRF wrapper in base.html.

A hand-written fetch() POST (e.g. the quote-header autosave) is neither an HTMX
request nor a native <form> submit, so it would carry no CSRF token and silently
403 in a real session. base.html wraps window.fetch to stamp X-CSRF-Token on
same-origin unsafe-method calls. This is a source pin (the behavior is JS); it
guards the wrapper's presence and the specific autosave that depends on it.
"""
import pathlib

_TPL = pathlib.Path(__file__).resolve().parents[1] / "app" / "templates"
_BASE = (_TPL / "base.html").read_text(encoding="utf-8")


def test_base_wraps_window_fetch():
    assert "window.fetch = function" in _BASE, (
        "The global fetch() CSRF wrapper was removed from base.html — raw fetch() "
        "POSTs will silently 403 in a real session."
    )


def test_wrapper_stamps_csrf_header_on_unsafe_methods():
    # The wrapper must add X-CSRF-Token and must gate on method + same-origin.
    assert 'X-CSRF-Token' in _BASE
    assert 'method !== "GET"' in _BASE
    # never touches GET/cross-origin
    assert 'sameOrigin' in _BASE


def test_quote_autosave_uses_plain_fetch_that_the_wrapper_covers():
    # If the autosave ever switches away from a plain fetch to /autosave, this
    # pin should be revisited — but as written it relies on the wrapper.
    ws = (_TPL / "quotes" / "workspace.html").read_text(encoding="utf-8")
    assert "/autosave'" in ws or "/autosave\"" in ws
    assert "fetch(" in ws

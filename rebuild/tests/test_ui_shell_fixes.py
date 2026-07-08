"""
tests/test_ui_shell_fixes.py
============================
Template-source pin tests for UI-shell fixes (Lane F), in the style of
tests/test_ui_lint.py: read the template files, assert the load-bearing
markup exists. No app import, no HTTP — these guard against regressions
where a refactor silently drops a class or attribute.

Covered fixes:
  1. Preview dock responsive offset — macros/preview_dock.html must be
     full-width below lg (left-0) and only clear the sidebar at lg+
     (lg:left-14 / lg:left-sidebar). The lg:left-* rules live in
     base.html's inline <style> because compiled app.css has no lg:left-*
     variants (either source satisfies the CSS-availability pin).
  2. Slide-over a11y — the global Log Call and Quick-Create slide-overs
     in base.html carry dialog semantics (role="dialog" aria-modal="true"
     + aria-label), an x-trap focus trap, first-field focus (autofocus /
     htmx:after-swap handler), and Escape-close wiring.
"""
from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
TEMPLATES_DIR = REPO_ROOT / "app" / "templates"
BASE_HTML = TEMPLATES_DIR / "base.html"
PREVIEW_DOCK = TEMPLATES_DIR / "macros" / "preview_dock.html"
APP_CSS = REPO_ROOT / "app" / "static" / "css" / "app.css"


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def _tags_containing(source: str, needle: str) -> list[str]:
    """Return every full opening tag (``<div ...>``, possibly multi-line)
    whose attribute text contains ``needle``."""
    return [m.group(0) for m in re.finditer(r"<[a-zA-Z][^>]*>", source, re.DOTALL)
            if needle in m.group(0)]


# ---------------------------------------------------------------------------
# 1. Preview dock responsive offset
# ---------------------------------------------------------------------------

class TestPreviewDockResponsive:
    def test_dock_full_width_below_lg(self):
        """The dock's static class list must include left-0 so it spans the
        viewport where the sidebar is hidden/overlaid (<lg)."""
        src = _read(PREVIEW_DOCK)
        dock_tags = _tags_containing(src, 'x-show="previewId !== null"')
        assert dock_tags, "preview_dock.html: dock root tag not found"
        tag = dock_tags[0]
        assert re.search(r'class="[^"]*\bleft-0\b', tag), (
            "preview_dock.html: dock must carry left-0 in its static class "
            "list (full-width below lg)"
        )

    def test_dock_sidebar_offset_is_lg_scoped(self):
        """The Alpine offset binding must use lg:-scoped classes only —
        a bare left-14/left-sidebar re-introduces the <1024px overlap bug."""
        src = _read(PREVIEW_DOCK)
        dock_tags = _tags_containing(src, 'x-show="previewId !== null"')
        tag = dock_tags[0]
        binding = re.search(r':class="([^"]*)"', tag)
        assert binding, "preview_dock.html: dock :class offset binding missing"
        expr = binding.group(1)
        assert "lg:left-14" in expr and "lg:left-sidebar" in expr, (
            f"dock offset binding must use lg:left-14 / lg:left-sidebar, got: {expr}"
        )
        assert not re.search(r"'left-(?:14|sidebar)'", expr), (
            f"dock offset binding must not apply an unscoped left offset: {expr}"
        )

    def test_lg_left_rules_exist_in_css(self):
        """lg:left-14 / lg:left-sidebar must be real CSS somewhere the page
        loads — compiled app.css (post build:css) or base.html's inline
        <style> (current state). Purged Tailwind means unverified class
        names silently do nothing."""
        css_sources = _read(BASE_HTML) + _read(APP_CSS)
        for cls in (r"\.lg\\:left-14", r"\.lg\\:left-sidebar"):
            assert re.search(cls + r"\s*\{", css_sources), (
                f"CSS rule for {cls} not found in base.html inline style or app.css"
            )
        # The rules must be lg-scoped: inside a min-width media query.
        inline = _read(BASE_HTML)
        m = re.search(
            r"@media \(min-width: 1024px\)\s*\{[^}]*\.lg\\:left-14[^}]*\}", inline
        )
        in_compiled = re.search(r"\.lg\\:left-14", _read(APP_CSS))
        assert m or in_compiled, (
            "lg:left-14 must be defined inside a min-width:1024px media query"
        )

    def test_base_utilities_still_compiled(self):
        """The static/left-0 and lg-mirrored values must stay in compiled CSS —
        the inline rules mirror .left-14 (3.5rem) / .left-sidebar (232px)."""
        css = _read(APP_CSS)
        assert ".left-0{left:0}" in css.replace(" ", "")
        assert re.search(r"\.left-14\{left:3\.5rem\}", css.replace(" ", ""))
        assert re.search(r"\.left-sidebar\{left:232px\}", css.replace(" ", ""))


# ---------------------------------------------------------------------------
# 2. Slide-over a11y (base.html)
# ---------------------------------------------------------------------------

def _panel_tag(source: str, trap_expr: str) -> str:
    """Locate the slide-over panel by its x-trap binding (unique per panel)."""
    tags = _tags_containing(source, f'x-trap.noscroll="{trap_expr}"')
    assert tags, f"base.html: no element carries x-trap.noscroll=\"{trap_expr}\""
    return tags[0]


class TestSlideOverA11y:
    def test_log_call_panel_dialog_semantics(self):
        src = _read(BASE_HTML)
        tag = _panel_tag(src, "logCallOpen")
        assert 'role="dialog"' in tag
        assert 'aria-modal="true"' in tag
        assert 'aria-label="Log a Call"' in tag
        assert 'x-show="logCallOpen"' in tag, "trap must sit on the shown panel"

    def test_create_slide_panel_dialog_semantics(self):
        src = _read(BASE_HTML)
        tag = _panel_tag(src, "createSlideOpen")
        assert 'role="dialog"' in tag
        assert 'aria-modal="true"' in tag
        # Title is dynamic — aria-label is Alpine-bound with a fallback.
        assert ':aria-label="createSlideTitle' in tag
        assert 'x-show="createSlideOpen"' in tag

    def test_log_call_first_field_autofocus(self):
        """x-trap initialFocus honors [autofocus]; the customer search input
        (first field) must carry it so focus lands there, not on the close X."""
        src = _read(BASE_HTML)
        input_tags = _tags_containing(src, 'name="_customer_display"')
        assert input_tags, "base.html: Log Call customer search input not found"
        assert re.search(r"(^|\s)autofocus(\s|>|/)", input_tags[0]), (
            "Log Call customer search input must carry autofocus"
        )

    def test_create_slide_focus_after_swap(self):
        """Quick-create content arrives via HTMX after the trap engages — the
        body-level after-swap handler must focus the first real field.

        NOTE: the <body> opening tag contains `=>` arrows inside its x-data,
        so tag extraction can't be used here — match the attribute value
        (double-quoted, no inner double quotes) directly instead."""
        src = _read(BASE_HTML)
        m = re.search(r'@htmx:after-swap\.window="([^"]*)"', src)
        assert m, "base.html: @htmx:after-swap.window handler missing"
        handler = m.group(1)
        assert "create-slide-content" in handler
        assert "input:not([type=hidden]), select, textarea" in handler
        assert ".focus()" in handler

    def test_escape_closes_both_slide_overs(self):
        """Escape-close is body-level (x-trap has escapeDeactivates:false)."""
        src = _read(BASE_HTML)
        handlers = re.findall(r'@keydown\.escape\.window="([^"]*)"', src)
        body_esc = [h for h in handlers if "logCallOpen = false" in h]
        assert body_esc, "base.html: body escape handler must close Log Call"
        assert "closeCreateSlide()" in body_esc[0], (
            "body escape handler must route quick-create through "
            "closeCreateSlide() (dirty-guard)"
        )

    def test_close_buttons_labeled(self):
        """Icon-only close buttons in both slide-over headers need aria-label."""
        src = _read(BASE_HTML)
        log_close = _tags_containing(src, '@click="logCallOpen = false"')
        labeled = [t for t in log_close if 'aria-label="Close"' in t and t.startswith("<button")]
        assert labeled, "Log Call header close button must carry aria-label"
        create_close = _tags_containing(src, '@click="closeCreateSlide()"')
        labeled = [t for t in create_close if 'aria-label="Close"' in t and t.startswith("<button")]
        assert labeled, "Quick-create header close button must carry aria-label"

    def test_focus_plugin_loaded_before_alpine(self):
        """x-trap needs @alpinejs/focus registered before Alpine boots."""
        src = _read(BASE_HTML)
        focus_at = src.find("alpine-focus.3.14.9.min.js")
        alpine_at = src.find("alpine.3.14.9.min.js")
        assert 0 < focus_at < alpine_at, (
            "alpine-focus must be script-loaded before alpine in base.html"
        )

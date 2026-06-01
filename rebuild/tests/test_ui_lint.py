"""
tests/test_ui_lint.py
=====================
CI lint gate -- enforces the JAKS UI class-token allowlist.

Source of truth:
  .claude/skills/jaks-ui-governance/references/class-tokens.md  (17 sections)
  JAKS_UI_Change_Plan.md  (sections 2, 2A, 3, 4)

Rules (all hard failures unless noted):
  Rule 1  [§1]  No tbl-* classes  (tbl-td, tbl-th, tbl-row, tbl-head, tbl-th-r, tbl-td-r)
  Rule 2  [§2]  Operational list screens must carry all 6 structural markers
  Rule 3  [§3]  border-l-4 must NOT appear on <tr>; only on first <td>
  Rule 4  [§3]  Stripe colors outside the §3 permitted set
  Rule 5  [§4]  Color utility classes outside the permitted families  [advisory]
  Rule 6  [§10] Inline x-transition:* / x-transition.* (use motion.html macros)
  Rule 7  [CSS] base.html must link compiled /static/css/app.css; no CDN fallback

Every failure emits  file:line  so UI-Builder can fix screen by screen.

Expected initial state after Tailwind cutover (2026-05-30):
  Rule 1: ~721 violations (L1 backlog -- listed, not fixed here)
  Rule 2: violations on all un-upgraded list.html screens
  Rule 3: 0 violations (clean)
  Rule 4: 6 violations (wrong shades in PO templates)
  Rule 5: ~79 advisory violations
  Rule 6: ~54 violations (inline x-transition, needs motion.html macros)
  Rule 7: 0 violations (compiled CSS live, CDN removed)

Do NOT fix templates in this file. Report only.
"""
from __future__ import annotations

import pathlib
import re
from typing import NamedTuple

import pytest


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
TEMPLATES_DIR = REPO_ROOT / "app" / "templates"
MACROS_DIR = TEMPLATES_DIR / "macros"
ADMIN_DIR = TEMPLATES_DIR / "admin"

# ---------------------------------------------------------------------------
# Exclusion scope
# ---------------------------------------------------------------------------

# Templates excluded from governed-screen scanning.
#
#  base.html — the CSS component stylesheet defines the legacy .tbl-* classes
#              (e.g. ".tbl-head { @apply bg-gray-50; }"). Those *definitions*
#              are not template usages; scanning base.html inflates the §1
#              tbl-* count with false positives and hides the real L1 backlog.
#
#  admin/    — internal debug / tooling screens (smoke test dashboard, backup
#              UI). They are not governed L2 screens and should not set off the
#              color-allowlist or stripe-color rules.
_SCREEN_EXCLUDES: frozenset[pathlib.Path] = frozenset(
    [TEMPLATES_DIR / "base.html"]
    + (list(ADMIN_DIR.glob("*.html")) if ADMIN_DIR.exists() else [])
)


def all_templates() -> list[pathlib.Path]:
    return sorted(TEMPLATES_DIR.rglob("*.html"))


def screen_templates() -> list[pathlib.Path]:
    """Governed screen templates only — excludes CSS-definition files and admin tooling.

    Use this (not all_templates) for rules that measure L2+ screen compliance:
    §1 tbl-*, §3 border-l-4, §4 stripe/color, §10 x-transition.
    """
    return [p for p in all_templates() if p not in _SCREEN_EXCLUDES]


def non_macro_templates() -> list[pathlib.Path]:
    """Screen templates excluding macros/ and lint-excluded paths."""
    return [p for p in screen_templates() if MACROS_DIR not in p.parents]


# ---------------------------------------------------------------------------
# Violation record
# ---------------------------------------------------------------------------

class Violation(NamedTuple):
    path: pathlib.Path
    lineno: int   # 1-based; 0 = file-level finding
    text: str
    detail: str

    def __str__(self) -> str:
        rel = self.path.relative_to(REPO_ROOT)
        loc = f":{self.lineno}" if self.lineno else ""
        snippet = f"\n    >>> {self.text.strip()}" if self.text.strip() else ""
        return f"  {rel}{loc}  {self.detail}{snippet}"


def _fmt(violations: list[Violation]) -> str:
    return "\n".join(str(v) for v in violations)


def _read(path: pathlib.Path) -> list[str]:
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


# ---------------------------------------------------------------------------
# Rule 1 -- §1: No tbl-* classes
# ---------------------------------------------------------------------------

_TBL_CLASSES = re.compile(r"\b(tbl-td|tbl-th|tbl-row|tbl-head|tbl-th-r|tbl-td-r)\b")


def test_no_tbl_classes():
    """[§1] No tbl-* legacy table classes anywhere in templates.

    These are banned in L2+ screens. L1 screens not yet upgraded are still
    flagged -- this is the documented backlog (~781 hits initially).

    Replacements (class-tokens.md §1):
      tbl-td   -> px-4 py-4 align-middle  on every <td>
      tbl-th   -> px-4 py-3 text-left text-xs font-semibold text-gray-500
                  uppercase tracking-wide whitespace-nowrap
      tbl-row  -> cursor-pointer hover:bg-gray-50/80 transition-colors group
      tbl-head -> <thead class="bg-gray-50">
      tbl-th-r -> explicit right-align classes
      tbl-td-r -> explicit right-align classes
    """
    violations: list[Violation] = []
    for path in screen_templates():   # base.html (CSS defs) and admin/ excluded
        for lineno, line in enumerate(_read(path), 1):
            for m in _TBL_CLASSES.finditer(line):
                violations.append(Violation(path, lineno, line,
                                            f"[§1] banned class: {m.group()}"))

    assert not violations, (
        f"\n[Rule 1 -- tbl-*] {len(violations)} violation(s):\n{_fmt(violations)}"
    )


# ---------------------------------------------------------------------------
# Rule 2 -- §2: Operational list screens must carry all structural markers
# ---------------------------------------------------------------------------

_QUEUE_BOARD_NAMES = {"receiving_queue.html", "match_queue.html"}


def _list_screens() -> list[pathlib.Path]:
    return [p for p in all_templates()
            if p.name == "list.html" and p.name not in _QUEUE_BOARD_NAMES]


# (name, pattern, description, queue_board_exempt)
_STRUCTURAL_MARKERS: list[tuple[str, re.Pattern[str], str, bool]] = [
    (
        "pb-52",
        re.compile(r"\bpb-52\b"),
        "outer x-data wrapper needs pb-52 (dock clearance)",
        False,
    ),
    (
        "divide-y divide-gray-100",
        re.compile(
            r"\bdivide-y\b.*\bdivide-gray-100\b"
            r"|\bdivide-gray-100\b.*\bdivide-y\b",
            re.DOTALL,
        ),
        "<tbody> must carry both divide-y AND divide-gray-100",
        False,
    ),
    (
        "overflow-x-auto",
        re.compile(r"\boverflow-x-auto\b"),
        "table must be wrapped in an overflow-x-auto scrolling container",
        False,
    ),
    (
        "min-w-[...]",
        re.compile(r"\bmin-w-\["),
        "<table> must carry min-w-[Npx] to prevent column collapse",
        False,
    ),
    (
        "preview dock wiring",
        re.compile(
            r'x-show=["\']previewId\s*!==\s*null["\']'   # explicit Alpine
            r"|preview_dock_shell\s*\("                    # macro call
            r"|togglePreview\s*\(",                        # factory wired
        ),
        'preview dock must be controlled by x-show="previewId !== null" '
        "(or preview_dock_shell macro / togglePreview factory)",
        True,  # queue boards exempt
    ),
    (
        "fixed bottom-0 left-64",
        re.compile(
            r"fixed\s+bottom-0\s+left-64"
            r"|fixed\b[^>]*\bbottom-0\b[^>]*\bleft-64\b"
            r"|preview_dock_shell\s*\(",                   # macro emits this
        ),
        "preview dock container must use fixed bottom-0 left-64 (or preview_dock_shell macro)",
        True,  # queue boards exempt
    ),
]


def test_list_screens_structural_markers():
    """[§2] Every operational list.html must carry all 6 structural markers.

    Exempt (§2A -- queue boards): receiving_queue.html, match_queue.html
    L1 screens not yet upgraded will fail -- intended pressure per rollout order.
    See JAKS_UI_Change_Plan.md §6 for upgrade sequence.
    """
    violations: list[Violation] = []
    for path in _list_screens():
        content = path.read_text(encoding="utf-8", errors="replace")
        is_queue = path.name in _QUEUE_BOARD_NAMES
        for name, pattern, description, queue_exempt in _STRUCTURAL_MARKERS:
            if queue_exempt and is_queue:
                continue
            if not pattern.search(content):
                violations.append(Violation(path, 0, "",
                                            f"[§2] missing '{name}': {description}"))

    assert not violations, (
        f"\n[Rule 2 -- structural markers] {len(violations)} violation(s):\n{_fmt(violations)}"
        "\n\nSee JAKS_UI_Change_Plan.md §6 for upgrade order."
    )


# ---------------------------------------------------------------------------
# Rule 3 -- §3: border-l-4 on <tr> (must be on first <td>)
# ---------------------------------------------------------------------------

_TR_BORDER_L4_RE = re.compile(r"<tr\b[^>]*\bborder-l-4\b")


def test_border_l4_on_td_not_tr():
    """[§3] border-l-4 must appear on the first <td>, never on <tr>.

    Correct:  <td class="border-l-4 border-l-red-400 px-4 py-4 align-middle">
    Wrong:    <tr class="border-l-4 border-l-red-400 ...">
    """
    violations: list[Violation] = []
    for path in screen_templates():   # admin/ excluded
        for lineno, line in enumerate(_read(path), 1):
            if _TR_BORDER_L4_RE.search(line):
                violations.append(Violation(path, lineno, line,
                                            "[§3] border-l-4 on <tr> -- move to first <td>"))

    assert not violations, (
        f"\n[Rule 3 -- border-l-4 on <tr>] {len(violations)} violation(s):\n{_fmt(violations)}"
    )


# ---------------------------------------------------------------------------
# Rule 4 -- §3: Only the 5 permitted stripe colors allowed
# ---------------------------------------------------------------------------

_PERMITTED_STRIPE_COLORS = {
    "border-l-red-400",
    "border-l-amber-400",
    "border-l-blue-300",
    "border-l-green-400",
    "border-l-transparent",
}

# Captures border-l-{color} or border-l-{color}-{shade}.
# Stops cleanly at word boundary -- never swallows Jinja2 syntax ({%, quotes).
# Width tokens (border-l-4, border-l-2) don't start with [a-z], so never matched.
_BORDER_L_COLOR_RE = re.compile(r"\bborder-l-([a-z]+(?:-\d+)?)\b")


def test_stripe_colors_permitted():
    """[§3] Only the 5 approved stripe colors may appear with border-l-*.

    Permitted: border-l-red-400, border-l-amber-400, border-l-blue-300,
               border-l-green-400, border-l-transparent
    Any other shade / color requires UI Architect approval.
    """
    violations: list[Violation] = []
    for path in screen_templates():   # admin/ excluded
        for lineno, line in enumerate(_read(path), 1):
            for m in _BORDER_L_COLOR_RE.finditer(line):
                token = "border-l-" + m.group(1)
                if token not in _PERMITTED_STRIPE_COLORS:
                    violations.append(Violation(
                        path, lineno, line,
                        f"[§3] non-permitted stripe: {token!r} "
                        f"(allowed: {', '.join(sorted(_PERMITTED_STRIPE_COLORS))})",
                    ))

    assert not violations, (
        f"\n[Rule 4 -- stripe colors] {len(violations)} violation(s):\n{_fmt(violations)}"
    )


# ---------------------------------------------------------------------------
# Rule 5 -- §4: Color utility classes outside permitted families  [advisory]
# ---------------------------------------------------------------------------

# §4 permitted families:
#   brand, red, amber, green, blue, sky, purple, orange, gray, slate, stone, white
# stone-* added 2026-05-29 (sidebar / subtle page backgrounds).
#
# Forbidden: everything else in the Tailwind named-color palette.
_FORBIDDEN_COLORS = [
    "pink", "yellow", "teal", "cyan", "indigo", "violet",
    "fuchsia", "rose", "lime", "emerald", "zinc", "neutral",
    "warmgray", "coolgray", "truegray", "bluegray",
]
_FORBIDDEN_ALT = "|".join(_FORBIDDEN_COLORS)
_FORBIDDEN_COLOR_RE = re.compile(
    r"\b(?:bg|text|border|ring|from|to|via|fill|stroke|outline|"
    r"placeholder|caret|accent|decoration)"
    r"-(?:" + _FORBIDDEN_ALT + r")\b"
)


def test_color_classes_within_allowlist():
    """[§4 advisory] Color utility classes must use only §4-permitted families.

    Permitted: brand / red / amber / green / blue / sky / purple / orange /
               gray / slate / stone / white / black / transparent
    Forbidden: pink, yellow, teal, cyan, indigo, violet, fuchsia, rose, lime,
               emerald, zinc, neutral (and other unnamed Tailwind colors).

    Marked advisory per class-tokens.md §4 -- coordinate with Architect before fixing.
    stone-* permitted since 2026-05-29 for sidebar/page tone use.
    """
    violations: list[Violation] = []
    for path in screen_templates():   # admin/ excluded
        for lineno, line in enumerate(_read(path), 1):
            stripped = line.strip()
            if stripped.startswith("{#") or stripped.startswith("<!--"):
                continue
            for m in _FORBIDDEN_COLOR_RE.finditer(line):
                violations.append(Violation(
                    path, lineno, line,
                    f"[§4 advisory] color outside allowlist: {m.group()!r}",
                ))

    assert not violations, (
        f"\n[Rule 5 -- §4 colors] {len(violations)} advisory violation(s):\n"
        f"{_fmt(violations)}\n\n"
        "Advisory: coordinate with Architect before fixing.\n"
        "stone-* is permitted; only the forbidden list above is blocked."
    )


# ---------------------------------------------------------------------------
# Rule 6 -- §10: No inline x-transition:* / x-transition.* (use motion.html)
# ---------------------------------------------------------------------------

_XTRANSITION_RE = re.compile(r"\bx-transition[:.][a-z-]")


def test_no_inline_x_transition():
    """[§10] x-transition:* and x-transition.* must NOT be written inline.

    Use the motion.html macro library (class-tokens.md §10):
      {{ motion.slide_right() }}    -- slide-over panels (translate-x)
      {{ motion.backdrop_fade() }}  -- backdrop overlays (opacity)
      {{ motion.slide_up() }}       -- preview dock (translate-y + opacity)
      {{ motion.dropdown_scale() }} -- notification dropdown / popovers
      {{ motion.modal_scale() }}    -- modals / Ctrl+K overlay

    Example:
      {% from "macros/motion.html" import slide_right, backdrop_fade %}
      <div x-show="open" {{ slide_right() }}>...</div>

    macros/ directory is exempt -- motion.html defines the transitions there.
    """
    violations: list[Violation] = []
    for path in non_macro_templates():
        for lineno, line in enumerate(_read(path), 1):
            if _XTRANSITION_RE.search(line):
                violations.append(Violation(
                    path, lineno, line,
                    "[§10] inline x-transition -- use motion.html macro instead",
                ))

    assert not violations, (
        f"\n[Rule 6 -- inline x-transition] {len(violations)} violation(s):\n{_fmt(violations)}"
    )


# ---------------------------------------------------------------------------
# Rule 7 -- Compiled CSS guard: no CDN Tailwind in base.html
# ---------------------------------------------------------------------------

_BASE_HTML = TEMPLATES_DIR / "base.html"

# The compiled stylesheet href we require (leading slash, no query string)
_COMPILED_CSS_RE = re.compile(r'href=["\']/?static/css/app\.css["\']')
# The CDN script that must NOT appear after the cutover
_CDN_TAILWIND_RE = re.compile(r'cdn\.tailwindcss\.com')
# Also ban the Play CDN <script> form used during development
_CDN_SCRIPT_RE = re.compile(r'<script[^>]+cdn\.tailwindcss\.com')


def test_compiled_css_no_cdn():
    """[CSS] base.html must link the compiled stylesheet and must NOT reference the CDN.

    After the Tailwind CLI cutover (2026-05-30):
      REQUIRED: <link rel="stylesheet" href="/static/css/app.css">
      FORBIDDEN: any reference to cdn.tailwindcss.com

    This test acts as a regression guard: if the CDN script ever reappears
    (e.g. a developer copies an old base.html snippet) the gate fails
    immediately before the broken build ships.

    The compiled file must exist on disk as well -- a dangling <link> is as bad
    as the CDN fallback because the app will render unstyled.
    """
    if not _BASE_HTML.exists():
        pytest.fail(f"base.html not found at expected path: {_BASE_HTML}")

    content = _BASE_HTML.read_text(encoding="utf-8")
    lines = content.splitlines()

    failures: list[str] = []

    # 1. Compiled <link> must be present
    if not _COMPILED_CSS_RE.search(content):
        failures.append(
            "  MISSING: <link rel=\"stylesheet\" href=\"/static/css/app.css\"> "
            "not found in base.html"
        )

    # 2. No CDN reference anywhere in base.html
    for lineno, line in enumerate(lines, 1):
        if _CDN_TAILWIND_RE.search(line):
            failures.append(
                f"  FORBIDDEN cdn.tailwindcss.com at base.html:{lineno}\n"
                f"    >>> {line.strip()}"
            )

    # 3. The compiled CSS file must exist on disk
    css_path = REPO_ROOT / "app" / "static" / "css" / "app.css"
    if not css_path.exists():
        failures.append(
            f"  MISSING FILE: app/static/css/app.css does not exist on disk.\n"
            f"  Run: npx tailwindcss -i app/static/css/input.css "
            f"-o app/static/css/app.css --minify"
        )
    elif css_path.stat().st_size < 10_000:
        # A real compiled Tailwind build is typically 50-200 KB minified.
        # Under 10 KB almost certainly means an empty / stub file.
        failures.append(
            f"  SUSPICIOUS: app/static/css/app.css is only "
            f"{css_path.stat().st_size} bytes -- rebuild may be needed.\n"
            f"  Run: npx tailwindcss -i app/static/css/input.css "
            f"-o app/static/css/app.css --minify"
        )

    assert not failures, (
        "\n[Rule 7 -- compiled CSS guard]\n" + "\n".join(failures)
    )


# ---------------------------------------------------------------------------
# Summary -- always passes, prints per-rule counts in CI log
# ---------------------------------------------------------------------------

def test_lint_summary(capsys):
    """Print a per-rule violation count. Always passes -- use above tests for failures."""
    counts: dict[str, int] = {}

    counts["[§1]  tbl-* classes"] = sum(
        1 for p in screen_templates() for ln in _read(p)
        for _ in _TBL_CLASSES.finditer(ln)
    )
    counts["[§2]  list-screen markers missing"] = sum(
        1 for p in _list_screens()
        for name, pat, _, qe in _STRUCTURAL_MARKERS
        if not (qe and p.name in _QUEUE_BOARD_NAMES)
        and not pat.search(p.read_text(encoding="utf-8", errors="replace"))
    )
    counts["[§3]  border-l-4 on <tr>"] = sum(
        1 for p in screen_templates() for ln in _read(p)
        if _TR_BORDER_L4_RE.search(ln)
    )
    counts["[§3]  non-permitted stripe colors"] = sum(
        1 for p in screen_templates() for ln in _read(p)
        for m in _BORDER_L_COLOR_RE.finditer(ln)
        if ("border-l-" + m.group(1)) not in _PERMITTED_STRIPE_COLORS
    )
    counts["[§4]  colors outside allowlist (advisory)"] = sum(
        1 for p in screen_templates() for ln in _read(p)
        if not ln.strip().startswith(("{#", "<!--"))
        for _ in _FORBIDDEN_COLOR_RE.finditer(ln)
    )
    counts["[§10] inline x-transition"] = sum(
        1 for p in non_macro_templates() for ln in _read(p)
        if _XTRANSITION_RE.search(ln)
    )

    # Rule 7 -- compiled CSS guard (simple pass/fail)
    base_content = _BASE_HTML.read_text(encoding="utf-8") if _BASE_HTML.exists() else ""
    css_ok = (
        _COMPILED_CSS_RE.search(base_content)
        and not _CDN_TAILWIND_RE.search(base_content)
        and (REPO_ROOT / "app" / "static" / "css" / "app.css").exists()
    )
    counts["[CSS] compiled CSS live / no CDN"] = 0 if css_ok else 1

    with capsys.disabled():
        print("\n\n=== UI Lint Backlog ===")
        for rule, n in counts.items():
            bar = "OK" if n == 0 else f"{n:>4} violations"
            print(f"  {rule:<45}  {bar}")
        print()


# ---------------------------------------------------------------------------
# Bonus: every template must be valid UTF-8
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "path", all_templates(), ids=lambda p: str(p.relative_to(REPO_ROOT))
)
def test_template_is_readable(path: pathlib.Path):
    """Every template must be decodable as UTF-8."""
    try:
        path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        pytest.fail(f"{path.relative_to(REPO_ROOT)}: UTF-8 decode error -- {exc}")

# Moved from root audit_products.py (kept as compatibility shim)

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class Finding:
    file: str
    sku: str
    handle: str
    title: str
    category: str
    manufacturer: str
    kind: str
    detail: str


KIT_WORDS = {
    "kit",
    "inframe",
    "rebuild",
    "overhaul",
    "conversion",
}

REBUILD_PART_WORDS = {
    "piston",
    "pistons",
    "liner",
    "liners",
    "ring",
    "rings",
    "gasket",
    "gaskets",
    "bearing",
    "bearings",
    "inframe",
    "overhaul",
}


def _is_turbo(title: str) -> bool:
    t = (title or "").lower()
    return "turbo" in t or "turbocharger" in t


def _is_kit_like(title: str) -> bool:
    t = (title or "").lower()
    return any(w in t for w in KIT_WORDS)


def _kit_contents_looks_like_rebuild(kit_contents: list[str]) -> bool:
    joined = " ".join([str(x or "") for x in kit_contents]).lower()
    return any(w in joined for w in REBUILD_PART_WORDS)


def _get_list(obj: Any, key: str) -> list:
    v = obj.get(key, []) if isinstance(obj, dict) else []
    return v if isinstance(v, list) else []


def _get_str(obj: Any, key: str) -> str:
    v = obj.get(key, "") if isinstance(obj, dict) else ""
    return str(v or "")


def audit_file(path: Path) -> tuple[list[Finding], dict[str, int]]:
    findings: list[Finding] = []
    counts = {
        "products": 0,
        "turbo_with_rebuild_contents": 0,
        "non_kit_with_contents": 0,
        "cat_c15_prefix_mismatch": 0,
        "xrefs_suspiciously_small": 0,
    }

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError:
        data = json.loads(path.read_text(encoding="utf-8-sig"))

    if not isinstance(data, dict):
        raise RuntimeError(f"Expected dict at top-level: {path}")

    for sku, prod in data.items():
        if not isinstance(prod, dict):
            continue
        counts["products"] += 1

        title = _get_str(prod, "title")
        handle = _get_str(prod, "handle")
        category = _get_str(prod, "category")
        manufacturer = _get_str(prod, "manufacturer") or _get_str(prod, "brand")

        kit_contents = [str(x or "") for x in _get_list(prod, "kit_contents")]
        xrefs = [str(x or "") for x in _get_list(prod, "xrefs")]
        cpl_list = [str(x or "") for x in _get_list(prod, "cpl_list")]
        cpl_prefix = _get_str(prod, "cpl_prefix")
        engine = _get_str(prod, "engine").upper()

        # 1) Turbo should not have rebuild kit parts in contents
        if _is_turbo(title) and kit_contents and _kit_contents_looks_like_rebuild(kit_contents):
            counts["turbo_with_rebuild_contents"] += 1
            findings.append(
                Finding(
                    file=str(path.name),
                    sku=str(sku),
                    handle=handle,
                    title=title,
                    category=category,
                    manufacturer=manufacturer,
                    kind="turbo_with_rebuild_contents",
                    detail=f"kit_contents={kit_contents}",
                )
            )

        # 2) Non-kit titles should usually not have kit contents (best-effort heuristic)
        if kit_contents and (not _is_kit_like(title)) and (not _is_turbo(title)):
            counts["non_kit_with_contents"] += 1
            findings.append(
                Finding(
                    file=str(path.name),
                    sku=str(sku),
                    handle=handle,
                    title=title,
                    category=category,
                    manufacturer=manufacturer,
                    kind="non_kit_with_contents",
                    detail=f"kit_contents={kit_contents}",
                )
            )

        # 3) CAT C15 prefix mismatch (if scraped list exists)
        if (manufacturer or "").lower() == "caterpillar" and engine.startswith("C15") and cpl_list:
            expected = cpl_list[0]
            if cpl_prefix and expected and cpl_prefix != expected:
                counts["cat_c15_prefix_mismatch"] += 1
                findings.append(
                    Finding(
                        file=str(path.name),
                        sku=str(sku),
                        handle=handle,
                        title=title,
                        category=category,
                        manufacturer=manufacturer,
                        kind="cat_c15_prefix_mismatch",
                        detail=f"cpl_prefix={cpl_prefix} expected={expected} cpl_list={cpl_list}",
                    )
                )

        # 4) Turbo xrefs suspiciously small (heuristic)
        if _is_turbo(title):
            xref_count = len([x for x in xrefs if x.strip()])
            if xref_count > 0 and xref_count < 5:
                counts["xrefs_suspiciously_small"] += 1
                findings.append(
                    Finding(
                        file=str(path.name),
                        sku=str(sku),
                        handle=handle,
                        title=title,
                        category=category,
                        manufacturer=manufacturer,
                        kind="xrefs_suspiciously_small",
                        detail=f"xrefs_count={xref_count} xrefs={xrefs}",
                    )
                )

    return findings, counts


def write_csv(findings: list[Finding], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["file", "sku", "handle", "title", "category", "manufacturer", "kind", "detail"])
        for it in findings:
            w.writerow([it.file, it.sku, it.handle, it.title, it.category, it.manufacturer, it.kind, it.detail])


def main() -> int:
    root = Path.cwd()
    candidates = [
        root / "data" / "products.json",
        root / "data" / "products_updated.json",
        root / "products.json",
        root / "products_updated.json",
        root / "output" / "products_updated.json",
    ]
    candidates = [p for p in candidates if p.exists()]
    if not candidates:
        print("No products*.json files found.")
        return 2

    all_findings: list[Finding] = []
    total_counts: dict[str, int] = {}

    for p in candidates:
        findings, counts = audit_file(p)
        all_findings.extend(findings)
        for k, v in counts.items():
            total_counts[k] = total_counts.get(k, 0) + int(v)

    out_csv = root / "output" / "audit_report.csv"
    write_csv(all_findings, out_csv)
    print(f"Wrote {len(all_findings)} findings → {out_csv}")
    print("Counts:")
    for k in sorted(total_counts.keys()):
        print(f"  {k}: {total_counts[k]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

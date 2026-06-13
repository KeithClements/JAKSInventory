"""Smart Import / Review Queue — analyze an incoming feed and STAGE each row as a
reviewable ImportCandidate. The scraper never writes the catalog directly.

Phase A: analyze_feed() (ingest -> flag each row against the 7 questions) + review
actions. Applying approved candidates to the catalog is Phase C.

Reuses the proven ProductImportService.parse_shopify_csv parser and the
ClassificationService (category + engine + needs_review) so the analysis matches
what the direct importer would have done — it just stages it for review first.
"""
from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import func

from app.constants import (
    CrossRefType, ImportBatchStatus, ImportDisposition, Permission,
    ScrapedItemReviewStatus,
)
from app.models.import_review import (
    ImportBatch, ImportCandidate, ImportMappingTemplate, ImportPendingUpload,
)
from app.models.product import (
    Product, ProductCostHistory, ProductImage, ProductCategory, CrossReference,
    ProductVendorSource,
)
from app.models.vendor import Vendor
from app.services.base import BaseService
from app.services.classification_service import ClassificationService
from app.services.product_import_service import (
    ProductImportService, _split_two, _to_float, _norm,
    _PAI_VENDOR_CODE, _PAI_VENDOR_NAME, _vendor_code_from_sku,
)


class ImportReviewService(BaseService):
    """Stage a feed into reviewable candidates (the 7-check analyzer)."""

    # Large-import safety (prevents the whole-app lock-up on big feeds):
    #   _COMMIT_EVERY — commit + expunge per N rows so a large import never holds
    #     the SQLite write-lock or all rows in memory long enough to freeze the app.
    #   _MAX_ROWS — reject absurd inputs outright (well above a full PAI catalog).
    _COMMIT_EVERY = 1000
    _MAX_ROWS = 100_000

    # ── Ingest + analyze ──────────────────────────────────────────────────────
    def analyze_feed(self, text: str, *, source_app: str = "", filename: str = "",
                     label: str = "", limit: int | None = None,
                     dry_run: bool = False, skip_unchanged: bool = True) -> ImportBatch:
        """Parse a Shopify-export feed and stage each product row as an
        ImportCandidate tagged against the 7 review questions. Returns the
        ImportBatch (committed unless dry_run).

        Synchronous — used by tests and small feeds. Large interactive uploads use
        create_pending_batch() + run_background_staging() so the request returns
        immediately and the queue page fills in as rows are analyzed."""
        rows = self._parse_rows(text, limit)
        batch = self._new_batch(rows, label=label, source_app=source_app, filename=filename)
        self._stage_rows(batch, rows, dry_run=dry_run, skip_unchanged=skip_unchanged)
        return batch

    def create_pending_batch(self, text: str, *, source_app: str = "",
                             filename: str = "", label: str = "") -> tuple[int, list]:
        """Fast + synchronous: parse the feed and create the batch shell (total set,
        no candidates yet), then commit. Returns ``(batch_id, rows)`` so the slow
        per-row analysis can run in a background task on a fresh session. Raises
        ValueError if the feed is over the safety cap."""
        rows = self._parse_rows(text)
        batch = self._new_batch(rows, label=label, source_app=source_app, filename=filename)
        self.db.commit()
        return batch.id, rows

    def create_pending_batch_from_rows(self, rows: list, *, source_app: str = "",
                                       filename: str = "", label: str = "") -> int:
        """R3 mapped-CSV path: same contract as create_pending_batch(), but the
        rows were already parsed through a user-confirmed column mapping
        (ProductImportService.parse_mapped_csv). Feeding the SAME _new_batch +
        _stage_rows pipeline means every downstream guard (vendor digit, dedupe,
        DUPLICATE handling, category gating) applies unchanged."""
        if len(rows) > self._MAX_ROWS:
            raise ValueError(
                f"This feed has {len(rows):,} rows, over the {self._MAX_ROWS:,}-row "
                "safety cap. Split it into smaller batches.")
        batch = self._new_batch(rows, label=label, source_app=source_app, filename=filename)
        self.db.commit()
        return batch.id

    # ── R3: saved per-vendor mapping templates ────────────────────────────────
    def find_template_for_source(self, source_label: str) -> ImportMappingTemplate | None:
        """Most recently updated template whose source_label matches (case-
        insensitive). Used to pre-fill the mapping screen for a repeat vendor."""
        label = (source_label or "").strip().lower()
        if not label:
            return None
        return (self.db.query(ImportMappingTemplate)
                .filter(func.lower(ImportMappingTemplate.source_label) == label)
                .order_by(ImportMappingTemplate.updated_at.desc(),
                          ImportMappingTemplate.id.desc())
                .first())

    def save_mapping_template(self, name: str, source_label: str,
                              mapping: dict) -> ImportMappingTemplate:
        """Create or update (upsert by case-insensitive name) a saved mapping."""
        name = (name or "").strip()[:200]
        source_label = (source_label or "").strip()[:100]
        if not name:
            raise ValueError("Template name is required")
        tpl = (self.db.query(ImportMappingTemplate)
               .filter(func.lower(ImportMappingTemplate.name) == name.lower())
               .first())
        if tpl is None:
            tpl = ImportMappingTemplate(name=name)
            self.db.add(tpl)
        tpl.source_label = source_label
        tpl.mapping_json = json.dumps(mapping or {})
        tpl.updated_at = datetime.utcnow()
        self.db.commit()
        return tpl

    def delete_mapping_template(self, template_id: int) -> bool:
        tpl = self.db.get(ImportMappingTemplate, template_id)
        if tpl is None:
            return False
        self.db.delete(tpl)
        self.db.commit()
        return True

    def discard_pending_upload(self, upload_id: int) -> None:
        up = self.db.get(ImportPendingUpload, upload_id)
        if up is not None:
            self.db.delete(up)
            self.db.commit()

    # ── internals shared by the sync + background paths ───────────────────────
    def _parse_rows(self, text: str, limit: int | None = None) -> list:
        pis = ProductImportService(self.db, self.current_user_id)
        if pis.detect_format(text) == "jaks":
            rows = pis.parse_jaks_export_csv(text)
        else:
            rows = pis.parse_shopify_csv(text)
        if limit:
            rows = rows[:limit]
        if len(rows) > self._MAX_ROWS:
            raise ValueError(
                f"This feed has {len(rows):,} rows, over the {self._MAX_ROWS:,}-row "
                "safety cap. Split it into smaller batches.")
        return rows

    def _new_batch(self, rows: list, *, label: str = "", source_app: str = "",
                   filename: str = "") -> ImportBatch:
        batch = ImportBatch(
            label=label or filename or source_app or "Import",
            source_app=source_app, filename=filename,
            status=ImportBatchStatus.STAGED,
            created_by_id=self.current_user_id, total=len(rows),
        )
        self.db.add(batch)
        self.db.flush()
        return batch

    def _stage_rows(self, batch: ImportBatch, rows: list, *, dry_run: bool = False,
                    auto_accept_confident: bool = False,
                    skip_unchanged: bool = True) -> None:
        """Analyze each row into an ImportCandidate, committing + expunging per chunk
        so a large feed never holds the SQLite write-lock or the whole feed in memory
        long enough to freeze the app. (dry_run stays in one transaction so the final
        rollback undoes everything.)

        skip_unchanged (R4 delta-refresh): a re-run of the full 13k-part scraper
        export typically changes a fraction of the catalog — UPDATE rows whose
        incoming values match the product exactly are tallied on the batch
        (unchanged_count) and never staged, so the queue holds only real work."""
        # one-pass lookups
        # Match the feed SKU against BOTH product.sku (manual products) AND
        # ProductVendorSource.vendor_sku (imported products mint an assembled
        # product.sku and park the original feed SKU on the vendor source — so a
        # product.sku-only match would wrongly read every imported part as NEW).
        sku_to_id: dict[str, int] = {}
        for pid, s in self.db.query(Product.id, Product.sku).all():
            if s:
                sku_to_id.setdefault(_norm(s), pid)
        for pid, vs in self.db.query(ProductVendorSource.product_id,
                                     ProductVendorSource.vendor_sku).all():
            if vs:
                sku_to_id.setdefault(_norm(vs), pid)
        # xref_index: first-owner lookup (cross-ref matching for NEW rows).
        # xref_sets: per-product normalized ref sets (R4 — "new OEM refs?" diff).
        xref_index: dict[str, int] = {}
        xref_sets: dict[int, set[str]] = {}
        for pid, num in self.db.query(CrossReference.product_id, CrossReference.ref_number).all():
            key = _norm(num)
            if key:
                xref_index.setdefault(key, pid)
                xref_sets.setdefault(pid, set()).add(key)
        img_counts = dict(self.db.query(ProductImage.product_id, func.count(ProductImage.id))
                          .group_by(ProductImage.product_id).all())
        # R4 — current values for the field-level diff: (price_override,
        # compare_at_price, manufacturer) per product + the PAI vendor cost.
        prod_info = {pid: (po, ca, (mfg or ""))
                     for pid, po, ca, mfg in self.db.query(
                         Product.id, Product.price_override,
                         Product.compare_at_price, Product.manufacturer).all()}
        price_by_id = {pid: v[0] for pid, v in prod_info.items()}
        # Vendor-source costs keyed by (product_id, VENDOR_CODE) — the feed SKU
        # prefix (JAKS-PAI-… / JAKS-IMB-…) names which vendor's "Our Cost" a
        # row carries, so the diff must compare against THAT vendor's source.
        # Key presence == "this product has a source for that vendor".
        vendor_cost_map: dict[tuple[int, str], float] = {}
        for vpid, vcost, vcode, vname in (
                self.db.query(ProductVendorSource.product_id,
                              ProductVendorSource.vendor_cost,
                              Vendor.vendor_code, Vendor.name)
                .join(Vendor, Vendor.id == ProductVendorSource.vendor_id).all()):
            code = (vcode or "").strip().upper()
            if not code and vname == _PAI_VENDOR_NAME:
                code = _PAI_VENDOR_CODE
            if code:
                vendor_cost_map[(vpid, code)] = vcost or 0.0
        cat_names = {(n or "").strip().lower(): cid
                     for cid, n in self.db.query(ProductCategory.id, ProductCategory.name).all()}
        classifier = ClassificationService(self.db)

        seen: set[str] = set()
        pending: list[ImportCandidate] = []
        for p in rows:
            cand = self._analyze_row(p, batch.id, sku_to_id, xref_index, img_counts,
                                     price_by_id, cat_names, classifier, seen,
                                     auto_accept_confident=auto_accept_confident,
                                     prod_info=prod_info,
                                     vendor_cost_map=vendor_cost_map,
                                     xref_sets=xref_sets,
                                     skip_unchanged=skip_unchanged)
            if cand is None:           # R4 — identical to the catalog: not staged
                batch.unchanged_count += 1
                continue
            self.db.add(cand)
            self._tally(batch, cand)
            pending.append(cand)
            if not dry_run and len(pending) >= self._COMMIT_EVERY:
                self.db.commit()
                for c in pending:
                    self.db.expunge(c)
                pending.clear()

        if dry_run:
            self.db.rollback()
        else:
            self.db.commit()

    def _analyze_row(self, p, batch_id, sku_to_id, xref_index, img_counts,
                     price_by_id, cat_names, classifier, seen,
                     auto_accept_confident: bool = False,
                     prod_info: dict | None = None,
                     vendor_cost_map: dict | None = None,
                     xref_sets: dict | None = None,
                     skip_unchanged: bool = False) -> ImportCandidate | None:
        """Returns None (R4) when the row is a clean UPDATE of an existing product
        and every incoming value matches the catalog — nothing to review."""
        prod_info = prod_info or {}
        vendor_cost_map = vendor_cost_map or {}
        xref_sets = xref_sets or {}
        sku = (p.get("sku") or "").strip()
        k = _norm(sku)
        flags: list[str] = []

        # 1 & 2 — new? / duplicate-in-feed? / update (SKU already exists)?
        if k and k in seen:
            disposition = ImportDisposition.DUPLICATE
            flags.append("duplicate in feed")
        elif k and k in sku_to_id:
            disposition = ImportDisposition.UPDATE
            flags.append("existing SKU")
        else:
            disposition = ImportDisposition.NEW
            flags.append("new product")
        if k:
            seen.add(k)
        matched_pid = sku_to_id.get(k)

        # 3 — cross-reference match (only when the SKU itself is new)
        if disposition == ImportDisposition.NEW:
            for it in (p.get("oem") or []):
                pid = xref_index.get(_norm(_split_two(it)[1]))
                if pid:
                    disposition = ImportDisposition.CROSS_REF
                    matched_pid = pid
                    flags.append("cross-ref match")
                    break

        # 4 — classification (category + engine + needs_review)
        cls = classifier.classify(
            title=p.get("title", ""), tags=p.get("tags", ""),
            app_makes=[_split_two(a)[0] for a in (p.get("apps") or [])],
            app_models=[_split_two(a)[1] for a in (p.get("apps") or [])],
        )
        # category OK if the classifier matched one OR the feed Type maps to an
        # existing category by name; otherwise it needs mapping.
        category_id = cls.get("category_id") or cat_names.get((p.get("type") or "").strip().lower())
        category_issue = not category_id
        if category_issue:
            flags.append("category needs mapping")

        # 5 — new image(s)?
        incoming = len(p.get("images") or [])
        existing = img_counts.get(matched_pid, 0) if matched_pid else 0
        has_new_images = incoming > existing
        if has_new_images:
            flags.append(f"+{incoming - existing} image(s)")

        # 6 — new / changed price?
        new_price = _to_float(p.get("price"))
        old_price = price_by_id.get(matched_pid) if matched_pid else None
        if matched_pid:
            price_changed = new_price is not None and (
                old_price is None or abs((old_price or 0.0) - new_price) >= 0.005)
        else:
            price_changed = new_price is not None
        if price_changed:
            flags.append("price change" if matched_pid else "price")

        # 6b — R4 field-level diff for UPDATE rows: what exactly would change?
        # Drives the preview dock's Current → Incoming table AND the
        # skip-unchanged decision. Each entry: {"field", "old", "new"}.
        diff: list[dict] = []
        new_refs_count = 0
        if matched_pid:
            cur_price, cur_compare, cur_mfg = prod_info.get(
                matched_pid, (old_price, None, ""))
            if price_changed:
                diff.append({"field": "sell price",
                             "old": cur_price, "new": new_price})
            new_compare = _to_float(p.get("compare_at"))
            if new_compare is not None and (
                    cur_compare is None
                    or abs((cur_compare or 0.0) - new_compare) >= 0.005):
                diff.append({"field": "compare-at price",
                             "old": cur_compare, "new": new_compare})
            new_cost = _to_float(p.get("cost"))
            if new_cost is not None and new_cost > 0:
                # The SKU prefix names the vendor whose dealer price this is
                # (JAKS-IMB-… → IMB; no prefix = legacy PAI). Diff only when
                # the product HAS that vendor's source — apply can't write a
                # cost anywhere else, and comparing against another vendor's
                # cost would stage phantom changes.
                vcode = _vendor_code_from_sku(sku) or _PAI_VENDOR_CODE
                key = (matched_pid, vcode)
                if key in vendor_cost_map and abs(vendor_cost_map[key] - new_cost) >= 0.005:
                    diff.append({"field": f"our cost ({vcode})",
                                 "old": vendor_cost_map[key], "new": new_cost})
            new_mfg = (p.get("manufacturer") or "").strip()
            if new_mfg and new_mfg.lower() != (cur_mfg or "").strip().lower():
                diff.append({"field": "manufacturer",
                             "old": cur_mfg or None, "new": new_mfg})
            existing_ref_set = xref_sets.get(matched_pid, set())
            new_refs_count = sum(
                1 for it in (p.get("oem") or [])
                if _norm(_split_two(it)[1])
                and _norm(_split_two(it)[1]) not in existing_ref_set)
            if new_refs_count:
                diff.append({"field": "OEM refs",
                             "old": len(existing_ref_set),
                             "new": f"+{new_refs_count} new"})
            if has_new_images:
                diff.append({"field": "images",
                             "old": existing, "new": incoming})
            if any(d["field"] != "sell price" for d in diff):
                flags.append("field changes")

        # R4 skip-unchanged: a clean UPDATE with ZERO differences is pure noise —
        # nothing to apply, nothing to review. NEW / CROSS_REF / DUPLICATE rows
        # are never skipped (creation and dup-detection still need eyes).
        if (skip_unchanged and disposition == ImportDisposition.UPDATE
                and matched_pid and not diff):
            return None

        # 7 — needs review? (low-confidence classification OR an uncertain cross-ref)
        needs_review = bool(cls.get("needs_review")) or disposition == ImportDisposition.CROSS_REF
        if needs_review:
            flags.append("needs review")

        # Auto-accept the confident rows (owner-chosen "auto-approve confident, review
        # only flagged"). Confident = NOT flagged AND a clean NEW/UPDATE (never a
        # duplicate or an uncertain cross-ref). They still wait for the explicit
        # "Apply Approved" click before anything is written to the catalog.
        confident = (not needs_review) and disposition in (
            ImportDisposition.NEW, ImportDisposition.UPDATE)
        review_status = (ScrapedItemReviewStatus.ACCEPTED
                         if (auto_accept_confident and confident)
                         else ScrapedItemReviewStatus.PENDING)

        return ImportCandidate(
            batch_id=batch_id, sku=sku[:100], title=(p.get("title") or "")[:500],
            category_name=(p.get("type") or "")[:200],
            raw_json=json.dumps(p, default=str),
            disposition=disposition, matched_product_id=matched_pid,
            incoming_image_count=incoming, has_new_images=has_new_images,
            price_changed=price_changed, old_price=old_price, new_price=new_price,
            diff_json=(json.dumps(diff) if diff else ""),
            resolved_category_id=category_id, category_issue=category_issue,
            engine_manufacturer=cls.get("engine_manufacturer") or "",
            engine_model=cls.get("engine_model") or "",
            needs_review=needs_review, flags=", ".join(flags)[:300],
            review_status=review_status,
        )

    @staticmethod
    def _tally(batch: ImportBatch, cand: ImportCandidate) -> None:
        if cand.disposition == ImportDisposition.NEW:
            batch.new_count += 1
        elif cand.disposition == ImportDisposition.UPDATE:
            batch.update_count += 1
        elif cand.disposition == ImportDisposition.CROSS_REF:
            batch.cross_ref_count += 1
        if cand.needs_review:
            batch.needs_review_count += 1
        if cand.review_status == ScrapedItemReviewStatus.ACCEPTED:
            batch.approved_count += 1   # auto-accepted confident rows count as approved

    # ── Review actions ────────────────────────────────────────────────────────
    def set_review_status(self, candidate_id: int, status: str, *,
                          notes: str = "") -> ImportCandidate:
        cand = self.db.get(ImportCandidate, candidate_id)
        if cand is None:
            raise ValueError(f"ImportCandidate {candidate_id} not found")
        cand.review_status = status
        cand.reviewed_by_id = self.current_user_id
        cand.reviewed_at = datetime.utcnow()
        if notes:
            cand.review_notes = notes
        self.db.flush()   # make the status change visible to the count below (autoflush=False safety)
        cand.batch.approved_count = self.db.query(ImportCandidate).filter(
            ImportCandidate.batch_id == cand.batch_id,
            ImportCandidate.review_status == ScrapedItemReviewStatus.ACCEPTED,
        ).count()
        self.db.commit()
        return cand

    def bulk_set_status(self, batch_id: int, target: str, *, scope: str = "all") -> int:
        """Set review_status on MANY candidates at once — no per-row checkbox
        selection. Only touches PENDING rows (never overrides a manual accept/reject).

        scope:
          'confident' — PENDING rows that are NOT flagged needs_review
          'flagged'   — PENDING rows that ARE flagged needs_review
          'all'       — every PENDING row

        One bulk UPDATE (fast at 13k rows). Returns the count changed and refreshes
        the batch's approved tally."""
        q = self.db.query(ImportCandidate).filter(
            ImportCandidate.batch_id == batch_id,
            ImportCandidate.review_status == ScrapedItemReviewStatus.PENDING,
        )
        if scope == "confident":
            q = q.filter(ImportCandidate.needs_review == False)   # noqa: E712
        elif scope == "flagged":
            q = q.filter(ImportCandidate.needs_review == True)    # noqa: E712
        n = q.update({
            ImportCandidate.review_status: target,
            ImportCandidate.reviewed_by_id: self.current_user_id,
            ImportCandidate.reviewed_at: datetime.utcnow(),
        }, synchronize_session=False)
        batch = self.db.get(ImportBatch, batch_id)
        if batch is not None:
            batch.approved_count = self.db.query(func.count(ImportCandidate.id)).filter(
                ImportCandidate.batch_id == batch_id,
                ImportCandidate.review_status == ScrapedItemReviewStatus.ACCEPTED,
            ).scalar() or 0
        self.db.commit()
        return n

    def update_candidate(self, candidate_id: int, updates: dict) -> ImportCandidate:
        """Let a reviewer correct fields on a candidate before approving.
        Allowed: new_price, resolved_category_id, engine_manufacturer, review_notes, title.
        Setting resolved_category_id also clears category_issue + needs_review so the
        row no longer shows as flagged once the reviewer has manually fixed it."""
        cand = self.db.get(ImportCandidate, candidate_id)
        if cand is None:
            raise ValueError(f"Candidate {candidate_id} not found")
        _allowed = {"new_price", "resolved_category_id", "engine_manufacturer",
                    "review_notes", "title"}
        for k, v in updates.items():
            if k in _allowed:
                setattr(cand, k, v)
        if updates.get("resolved_category_id"):
            cand.category_issue = False
            cand.needs_review = False   # reviewer explicitly set category — no longer uncertain
        if "new_price" in updates:
            val = updates["new_price"]
            cand.new_price = val
            cand.price_changed = True   # still track that reviewer touched the price
        self.db.commit()
        return cand

    def list_candidates(self, batch_id: int, *, review_status: str | None = None,
                        disposition: str | None = None) -> list[ImportCandidate]:
        q = self.db.query(ImportCandidate).filter(ImportCandidate.batch_id == batch_id)
        if review_status:
            q = q.filter(ImportCandidate.review_status == review_status)
        if disposition:
            q = q.filter(ImportCandidate.disposition == disposition)
        return q.order_by(ImportCandidate.id).all()

    # ── Apply (Phase C) ───────────────────────────────────────────────────────
    def apply_approved(self, batch_id: int, only_ids: list[int] | None = None) -> dict:
        """Apply every ACCEPTED, not-yet-applied candidate in a batch to the catalog
        through the ERP's OWN create/update path. Idempotent (skips applied ones).
        ``only_ids`` narrows the run to specific candidates (the per-row and
        selected-rows "Approve & Add to Catalog" actions); rows must still be
        ACCEPTED + unapplied to be written.

          NEW            -> create via the locked full_import path (rows=[p]), which
                            mints the JAKS SKU + vendor source + cross-refs + apps +
                            images, and is itself idempotent by vendor_sku.
          UPDATE/CROSS_REF-> surgically enrich the matched product: add only NEW
                            images + cross-refs, set category only if missing. NEVER
                            clobbers title/price, NEVER writes product.cost (moving-
                            avg COGS is owner-locked to PO receipt).

        Does NOT publish to Shopify/eBay (that is Phase D). Partial-success safe:
        one bad candidate is logged and skipped, never aborts the batch."""
        # Applying writes to the live product catalog (creates/updates products,
        # cross-refs, images) — gate it like other sensitive actions. Admin-only
        # by default (see _ROLE_PERMISSIONS); a None user_id is a system/background
        # job and is allowed (assert_can short-circuits on current_user_id is None).
        self.assert_can(Permission.APPLY_IMPORT)
        from app.services.product_service import ProductService  # local: avoid cycle

        batch = self.db.get(ImportBatch, batch_id)
        if batch is None:
            raise ValueError(f"ImportBatch {batch_id} not found")
        batch.status = ImportBatchStatus.APPLYING
        self.db.commit()

        # DUPLICATE (dup-in-feed) rows have nothing to apply: matched_product_id
        # is None, so they'd fall into the update branch, no-op, never get an
        # applied_product_id, and wedge the batch in STAGED forever (R1-16).
        # The first occurrence of the SKU is the row that creates/updates.
        base_filters = (
            ImportCandidate.batch_id == batch_id,
            ImportCandidate.review_status == ScrapedItemReviewStatus.ACCEPTED,
            ImportCandidate.applied_product_id.is_(None),
        )
        cand_q = self.db.query(ImportCandidate).filter(
            *base_filters,
            ImportCandidate.disposition != ImportDisposition.DUPLICATE,
        )
        dup_q = self.db.query(ImportCandidate).filter(
            *base_filters,
            ImportCandidate.disposition == ImportDisposition.DUPLICATE,
        )
        if only_ids:
            cand_q = cand_q.filter(ImportCandidate.id.in_(only_ids))
            dup_q = dup_q.filter(ImportCandidate.id.in_(only_ids))
        cands = cand_q.order_by(ImportCandidate.id).all()

        pis = ProductImportService(self.db, self.current_user_id)
        psvc = ProductService(self.db, self.current_user_id)
        # R4 queue hygiene: DUPLICATE rows are pure noise once the apply run
        # starts (the first occurrence of the SKU does the work) — sweep them
        # out of the queue instead of leaving dead rows behind.
        dups = dup_q.all()
        summary = {"applied": 0, "created": 0, "updated": 0,
                   "skipped_duplicates": len(dups),
                   "images_added": 0, "cross_refs_added": 0, "errors": []}
        for d in dups:
            self.db.delete(d)
        self.db.commit()

        # ── Split: NEW rows go through ONE bulk full_import call. The previous
        # per-row full_import calls each re-queried every vendor_sku in the
        # catalog (plus categories + sequence counters) — O(n²) at catalog
        # scale; a 13k-row full-catalog apply effectively hung the request.
        # Hold (id, sku, parsed_row) — NOT ORM objects: full_import's chunked
        # commits expunge the whole session, which would detach the candidate
        # (and batch) instances mid-run on feeds > 500 rows.
        new_cands: list[tuple] = []   # (candidate_id, sku, parsed_row)
        upd_cands: list[tuple] = []
        for c in cands:
            try:
                p = json.loads(c.raw_json) if c.raw_json else {}
            except (ValueError, TypeError) as exc:
                summary["errors"].append(f"candidate {c.id} ({c.sku}): bad raw_json: {exc}")
                continue
            if c.disposition == ImportDisposition.NEW and not c.matched_product_id:
                new_cands.append((c.id, c.sku, p))
            else:
                upd_cands.append((c.id, c.sku, p))

        def _finish(c, product_id: int, p: dict, *, created_now: bool) -> None:
            """Shared post-apply: stamp the candidate, re-apply reviewer
            corrections (authoritative — _apply paths build from raw_json, so a
            reviewer's price/category/engine edit must be re-applied or it would
            silently vanish), clear needs_review, tally, retire the row."""
            c.applied_product_id = product_id
            c.applied_at = datetime.utcnow()
            summary["applied"] += 1
            prod_obj = self.db.get(Product, product_id)
            if prod_obj:
                prod_obj.needs_review = False
                # Price: the candidate's price is competitor-matched (and
                # possibly reviewer-corrected) — it always wins.
                if c.new_price is not None and c.new_price > 0:
                    prod_obj.price_override = c.new_price
                # Category: authoritative on create, fill-if-blank on update.
                if c.resolved_category_id and (created_now or not prod_obj.category_id):
                    prod_obj.category_id = c.resolved_category_id
                # Engine make: same rule.
                if c.engine_manufacturer and (created_now or not prod_obj.engine_manufacturer):
                    prod_obj.engine_manufacturer = c.engine_manufacturer
                # AI-generated description/tags fill blanks only.
                if not prod_obj.description:
                    ai_desc = (p.get("_ai_description") or "").strip()
                    if ai_desc:
                        prod_obj.description = ai_desc[:2000]
                if not prod_obj.search_keywords:
                    ai_tags = (p.get("_ai_tags") or "").strip()
                    if ai_tags:
                        prod_obj.search_keywords = ai_tags[:500]
            # R4 queue hygiene: an applied candidate's job is done — its data
            # now lives on the product. Tally on the batch header (history) and
            # remove the row so the queue only holds work that still needs eyes.
            batch.applied_count += 1
            self.db.delete(c)

        # ── NEW candidates → one bulk create via the locked full_import path
        # (mints JAKS SKUs + vendor sources + cross-refs + apps + images, and is
        # idempotent by vendor_sku — re-running after a partial apply is safe).
        if new_cands:
            try:
                res = pis.full_import(rows=[p for _, _, p in new_cands],
                                      dry_run=False, import_images=True)
            except Exception as exc:  # noqa: BLE001 — surface, never 500 the queue
                self.db.rollback()
                res = {"error": f"bulk create failed: {exc}"}
            if res.get("error"):
                # e.g. the feed's vendor doesn't exist / has no SKU digit set.
                # Nothing was written (full_import is atomic on this check) and
                # the candidates stay ACCEPTED + unapplied, so fixing the vendor
                # and clicking Apply again just works.
                summary["errors"].append(res["error"])
            else:
                summary["created"] = res.get("created", 0)
                # full_import's chunked commits expunged the session — re-bind
                # the batch row before _finish touches applied_count.
                batch = self.db.get(ImportBatch, batch_id)
                # Resolve the created products by the parked feed SKU, chunked
                # (vendor_sku first — product.sku is the minted JAKS SKU now).
                keys = [_norm(p.get("sku") or "") for _, _, p in new_cands]
                want = sorted({k for k in keys if k})
                by_sku: dict[str, int] = {}
                for i in range(0, len(want), 500):
                    chunk = want[i:i + 500]
                    for vsku, pid in (self.db.query(
                            func.lower(ProductVendorSource.vendor_sku),
                            ProductVendorSource.product_id)
                            .filter(func.lower(ProductVendorSource.vendor_sku)
                                    .in_(chunk)).all()):
                        by_sku.setdefault(vsku, pid)
                    for psku, pid in (self.db.query(func.lower(Product.sku), Product.id)
                                      .filter(func.lower(Product.sku).in_(chunk)).all()):
                        by_sku.setdefault(psku, pid)
                done = 0
                for (cid, csku, p), k in zip(new_cands, keys):
                    try:
                        c = self.db.get(ImportCandidate, cid)  # fresh post-expunge
                        if c is None:
                            continue
                        product_id = by_sku.get(k)
                        if product_id:
                            _finish(c, product_id, p, created_now=True)
                        else:
                            summary["errors"].append(
                                f"candidate {cid} ({csku}): created product "
                                "not found by feed SKU")
                        done += 1
                        if done % 500 == 0:
                            self.db.commit()
                    except Exception as exc:  # noqa: BLE001 — never abort the batch
                        self.db.rollback()
                        batch = self.db.get(ImportBatch, batch_id)
                        summary["errors"].append(f"candidate {cid} ({csku}): {exc}")
                self.db.commit()

        # ── UPDATE / CROSS_REF candidates → surgical per-product enrichment
        for cid, csku, p in upd_cands:
            try:
                c = self.db.get(ImportCandidate, cid)
                if c is None:
                    continue
                product_id = c.matched_product_id
                got = self._apply_update(psvc, product_id, p, c)
                summary["images_added"] += got["images"]
                summary["cross_refs_added"] += got["cross_refs"]
                summary["updated"] += 1
                if product_id:
                    _finish(c, product_id, p, created_now=False)
                self.db.commit()
            except Exception as exc:  # noqa: BLE001 — one bad row never aborts the batch
                self.db.rollback()
                batch = self.db.get(ImportBatch, batch_id)
                summary["errors"].append(f"candidate {cid} ({csku}): {exc}")

        self._finalize_batch(
            batch_id,
            applied_or_skipped=summary["applied"] + summary["skipped_duplicates"])
        return summary

    def _apply_update(self, psvc, product_id, p, c) -> dict:
        """Enrich an existing matched product from the approved import candidate.

        Always updates: price_override (imported prices are deliberately set to beat
        competitors and must always win), compare_at_price, manufacturer
        (canonicalized), and the PAI vendor source's vendor_cost (+ cost history)
        when the feed carries them; description/tags if currently blank,
        category if currently unset.
        Also adds: new images, new cross-refs/OEM refs.
        Never touches: title, product.cost (moving-avg COGS — set only on PO receipt)."""
        got = {"images": 0, "cross_refs": 0}
        product = self.db.get(Product, product_id) if product_id else None
        if product is None:
            return got

        # ── Price — the import price is our competitor-matched sell price; always apply
        if c.new_price is not None and c.new_price > 0:
            product.price_override = c.new_price
            got["price_updated"] = True

        # ── R4 — compare-at, manufacturer, PAI cost (mirrors sell-mode semantics)
        new_compare = _to_float(p.get("compare_at"))
        if new_compare is not None and new_compare > 0:
            product.compare_at_price = new_compare

        new_mfg = (p.get("manufacturer") or "").strip()
        if new_mfg:
            # Canonicalize against the product-form dropdown list (case-
            # insensitive); unknown makes pass through verbatim.
            from app.routers.products import MANUFACTURERS as _MFG_CANON
            product.manufacturer = {m.lower(): m for m in _MFG_CANON}.get(
                new_mfg.lower(), new_mfg)
        elif not (product.manufacturer or "").strip():
            # Owner rule: a make named in the imported description (OEM refs /
            # applications / title / tags) fills a BLANK manufacturer — never
            # overwrites one a human already set.
            from app.services.product_import_service import derive_manufacturer
            derived = derive_manufacturer(p)
            if derived:
                product.manufacturer = derived

        new_cost = _to_float(p.get("cost"))
        if new_cost is not None and new_cost > 0:
            # The feed SKU prefix names the vendor whose dealer price this is
            # (JAKS-PAI-… / JAKS-IMB-…; no prefix = legacy PAI). Write to THAT
            # vendor's source only — never re-route to another vendor.
            vcode = _vendor_code_from_sku(p.get("sku") or "") or _PAI_VENDOR_CODE
            vq = self.db.query(Vendor).filter(func.upper(Vendor.vendor_code) == vcode)
            if vcode == _PAI_VENDOR_CODE:
                vq = self.db.query(Vendor).filter(
                    (func.upper(Vendor.vendor_code) == vcode)
                    | (Vendor.name == _PAI_VENDOR_NAME))
            vendor = vq.first()
            src = None
            if vendor is not None:
                src = self.db.query(ProductVendorSource).filter(
                    ProductVendorSource.product_id == product_id,
                    ProductVendorSource.vendor_id == vendor.id).first()
            if src is not None and abs((src.vendor_cost or 0.0) - new_cost) >= 0.005:
                old_cost = src.vendor_cost or 0.0
                src.vendor_cost = new_cost
                src.last_cost_updated_at = datetime.utcnow()
                self.db.add(ProductCostHistory(
                    product_id=product_id, vendor_id=vendor.id,
                    old_cost=old_cost, new_cost=new_cost,
                    changed_by_id=self.current_user_id,
                    notes=f"Smart Import refresh ({vcode} cost)",
                ))
                got["cost_updated"] = True

        # ── Category — set if currently blank, or if AI/human resolved it
        if c.resolved_category_id and (product.category_id is None or c.category_issue is False):
            product.category_id = c.resolved_category_id
            product.needs_review = False

        # ── Description / tags — fill from AI suggestions if product has none
        if not product.description or not product.search_keywords:
            try:
                raw = json.loads(c.raw_json) if c.raw_json else {}
                if not product.description:
                    ai_desc = (raw.get("_ai_description") or "").strip()
                    if ai_desc:
                        product.description = ai_desc[:2000]
                if not product.search_keywords:
                    ai_tags = (raw.get("_ai_tags") or "").strip()
                    if ai_tags:
                        product.search_keywords = ai_tags[:500]
            except (ValueError, TypeError):
                pass
        existing_refs = {_norm(r.ref_number) for r in self.db.query(CrossReference)
                         .filter(CrossReference.product_id == product_id).all()}
        for it in (p.get("oem") or []):
            brand, num = _split_two(it)
            if num and _norm(num) not in existing_refs:
                psvc.add_cross_reference(product_id, CrossRefType.OEM, num, brand=brand)
                existing_refs.add(_norm(num))
                got["cross_refs"] += 1
        existing_imgs = {_norm(i.file_path) for i in self.db.query(ProductImage)
                         .filter(ProductImage.product_id == product_id).all()}
        for im in (p.get("images") or []):
            url = (im.get("url") or "")[:500]
            if url and _norm(url) not in existing_imgs:
                psvc.add_product_image(product_id, url, source="pai",
                                       alt_text=(im.get("alt") or "")[:300])
                existing_imgs.add(_norm(url))
                got["images"] += 1
        # A clean (unwatermarked ATL) image supersedes the watermarked PAI image as
        # the primary — keeps the watermarked one as a non-primary fallback.
        psvc.supersede_primary_with_clean(product_id)
        return got

    def _finalize_batch(self, batch_id: int, *, applied_or_skipped: int = 0) -> None:
        """R4: applied candidates (and swept DUPLICATE rows) are DELETED by
        apply_approved — batch.applied_count is accumulated there, never
        recounted here. ``applied_or_skipped`` carries this run's activity so a
        run that only swept duplicates still finalizes the batch."""
        batch = self.db.get(ImportBatch, batch_id)
        if batch is None:
            return
        remaining = self.db.query(ImportCandidate).filter(
            ImportCandidate.batch_id == batch_id,
            ImportCandidate.review_status == ScrapedItemReviewStatus.ACCEPTED,
            ImportCandidate.applied_product_id.is_(None),
            ImportCandidate.disposition != ImportDisposition.DUPLICATE,
        ).count()
        if remaining == 0 and (batch.applied_count > 0 or applied_or_skipped > 0):
            batch.status = ImportBatchStatus.APPLIED
            batch.applied_at = datetime.utcnow()
        else:
            batch.status = ImportBatchStatus.STAGED   # errors left some unapplied — allow retry
        self.db.commit()

    def delete_batch(self, batch_id: int) -> bool:
        """R4 queue hygiene: remove a batch and every remaining candidate
        (old/test uploads, fully-applied history nobody needs). Applied product
        data is untouched — candidates are staging rows, the catalog is the
        record. Gated like apply (writes to the staging tables)."""
        self.assert_can(Permission.APPLY_IMPORT)
        batch = self.db.get(ImportBatch, batch_id)
        if batch is None:
            return False
        self.db.delete(batch)   # cascade="all, delete-orphan" sweeps candidates
        self.db.commit()
        return True


# Stored in ImportBatch.notes when background staging fails, so the queue page can
# stop polling and show an error instead of spinning forever.
IMPORT_ERROR_PREFIX = "IMPORT_ERROR:"


def run_background_staging(batch_id: int, rows: list, user_id: int | None,
                           skip_unchanged: bool = True) -> None:
    """Stage already-parsed rows into an existing batch on a FRESH DB session.

    A FastAPI BackgroundTask runs after the response is sent — by which point the
    request's session is closed — so we open our own session and close it here.
    """
    from app.database import SessionLocal  # local import: avoids an import cycle
    db = SessionLocal()
    try:
        batch = db.get(ImportBatch, batch_id)
        if batch is None:
            return
        ImportReviewService(db, user_id)._stage_rows(batch, rows, dry_run=False,
                                                     auto_accept_confident=True,
                                                     skip_unchanged=skip_unchanged)
    except Exception as exc:  # noqa: BLE001 — record the failure, let the UI stop polling
        db.rollback()
        b = db.get(ImportBatch, batch_id)
        if b is not None:
            b.notes = f"{IMPORT_ERROR_PREFIX} {exc}"[:300]
            db.commit()
    finally:
        db.close()

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
from app.models.import_review import ImportBatch, ImportCandidate
from app.models.product import (
    Product, ProductImage, ProductCategory, CrossReference, ProductVendorSource,
)
from app.services.base import BaseService
from app.services.classification_service import ClassificationService
from app.services.product_import_service import (
    ProductImportService, _split_two, _to_float, _norm,
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
                     dry_run: bool = False) -> ImportBatch:
        """Parse a Shopify-export feed and stage each product row as an
        ImportCandidate tagged against the 7 review questions. Returns the
        ImportBatch (committed unless dry_run).

        Synchronous — used by tests and small feeds. Large interactive uploads use
        create_pending_batch() + run_background_staging() so the request returns
        immediately and the queue page fills in as rows are analyzed."""
        rows = self._parse_rows(text, limit)
        batch = self._new_batch(rows, label=label, source_app=source_app, filename=filename)
        self._stage_rows(batch, rows, dry_run=dry_run)
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
                    auto_accept_confident: bool = False) -> None:
        """Analyze each row into an ImportCandidate, committing + expunging per chunk
        so a large feed never holds the SQLite write-lock or the whole feed in memory
        long enough to freeze the app. (dry_run stays in one transaction so the final
        rollback undoes everything.)"""
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
        xref_index: dict[str, int] = {}
        for pid, num in self.db.query(CrossReference.product_id, CrossReference.ref_number).all():
            key = _norm(num)
            if key:
                xref_index.setdefault(key, pid)
        img_counts = dict(self.db.query(ProductImage.product_id, func.count(ProductImage.id))
                          .group_by(ProductImage.product_id).all())
        price_by_id = dict(self.db.query(Product.id, Product.price_override).all())
        cat_names = {(n or "").strip().lower(): cid
                     for cid, n in self.db.query(ProductCategory.id, ProductCategory.name).all()}
        classifier = ClassificationService(self.db)

        seen: set[str] = set()
        pending: list[ImportCandidate] = []
        for p in rows:
            cand = self._analyze_row(p, batch.id, sku_to_id, xref_index, img_counts,
                                     price_by_id, cat_names, classifier, seen,
                                     auto_accept_confident=auto_accept_confident)
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
                     auto_accept_confident: bool = False) -> ImportCandidate:
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

    def list_candidates(self, batch_id: int, *, review_status: str | None = None,
                        disposition: str | None = None) -> list[ImportCandidate]:
        q = self.db.query(ImportCandidate).filter(ImportCandidate.batch_id == batch_id)
        if review_status:
            q = q.filter(ImportCandidate.review_status == review_status)
        if disposition:
            q = q.filter(ImportCandidate.disposition == disposition)
        return q.order_by(ImportCandidate.id).all()

    # ── Apply (Phase C) ───────────────────────────────────────────────────────
    def apply_approved(self, batch_id: int) -> dict:
        """Apply every ACCEPTED, not-yet-applied candidate in a batch to the catalog
        through the ERP's OWN create/update path. Idempotent (skips applied ones).

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

        cands = self.db.query(ImportCandidate).filter(
            ImportCandidate.batch_id == batch_id,
            ImportCandidate.review_status == ScrapedItemReviewStatus.ACCEPTED,
            ImportCandidate.applied_product_id.is_(None),
        ).order_by(ImportCandidate.id).all()

        pis = ProductImportService(self.db, self.current_user_id)
        psvc = ProductService(self.db, self.current_user_id)
        summary = {"applied": 0, "created": 0, "updated": 0,
                   "images_added": 0, "cross_refs_added": 0, "errors": []}

        for c in cands:
            try:
                p = json.loads(c.raw_json) if c.raw_json else {}
                if c.disposition == ImportDisposition.NEW and not c.matched_product_id:
                    product_id = self._apply_new(pis, p)
                    summary["created"] += 1
                else:
                    product_id = c.matched_product_id
                    got = self._apply_update(psvc, product_id, p, c)
                    summary["images_added"] += got["images"]
                    summary["cross_refs_added"] += got["cross_refs"]
                    summary["updated"] += 1
                if product_id:
                    c.applied_product_id = product_id
                    c.applied_at = datetime.utcnow()
                    summary["applied"] += 1
                self.db.commit()
            except Exception as exc:  # noqa: BLE001 — one bad row never aborts the batch
                self.db.rollback()
                summary["errors"].append(f"candidate {c.id} ({c.sku}): {exc}")

        self._finalize_batch(batch_id)
        return summary

    def _apply_new(self, pis, p) -> int | None:
        """Create a product from a parsed row via the locked full_import path, then
        resolve its id (the feed SKU is parked on the new product's vendor source)."""
        pis.full_import(rows=[p], dry_run=False, import_images=True)
        k = _norm(p.get("sku") or "")
        if not k:
            return None
        vs = self.db.query(ProductVendorSource).filter(
            func.lower(ProductVendorSource.vendor_sku) == k).first()
        if vs:
            return vs.product_id
        prod = self.db.query(Product).filter(func.lower(Product.sku) == k).first()
        return prod.id if prod else None

    def _apply_update(self, psvc, product_id, p, c) -> dict:
        """Surgically enrich an existing matched product — add only NEW images +
        cross-refs, set category only if missing. Never touches title/price/cost."""
        got = {"images": 0, "cross_refs": 0}
        product = self.db.get(Product, product_id) if product_id else None
        if product is None:
            return got
        if product.category_id is None and c.resolved_category_id:
            psvc.update_product(product_id, {"category_id": c.resolved_category_id})
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
        return got

    def _finalize_batch(self, batch_id: int) -> None:
        batch = self.db.get(ImportBatch, batch_id)
        if batch is None:
            return
        batch.applied_count = self.db.query(ImportCandidate).filter(
            ImportCandidate.batch_id == batch_id,
            ImportCandidate.applied_product_id.isnot(None),
        ).count()
        remaining = self.db.query(ImportCandidate).filter(
            ImportCandidate.batch_id == batch_id,
            ImportCandidate.review_status == ScrapedItemReviewStatus.ACCEPTED,
            ImportCandidate.applied_product_id.is_(None),
        ).count()
        if remaining == 0 and batch.applied_count > 0:
            batch.status = ImportBatchStatus.APPLIED
            batch.applied_at = datetime.utcnow()
        else:
            batch.status = ImportBatchStatus.STAGED   # errors left some unapplied — allow retry
        self.db.commit()


# Stored in ImportBatch.notes when background staging fails, so the queue page can
# stop polling and show an error instead of spinning forever.
IMPORT_ERROR_PREFIX = "IMPORT_ERROR:"


def run_background_staging(batch_id: int, rows: list, user_id: int | None) -> None:
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
                                                     auto_accept_confident=True)
    except Exception as exc:  # noqa: BLE001 — record the failure, let the UI stop polling
        db.rollback()
        b = db.get(ImportBatch, batch_id)
        if b is not None:
            b.notes = f"{IMPORT_ERROR_PREFIX} {exc}"[:300]
            db.commit()
    finally:
        db.close()

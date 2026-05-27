"""QuickBooks Online API client.

Handles OAuth authentication and API calls to QBO.
Falls back to mock data when credentials not configured.
"""

from __future__ import annotations

import os
import json
import time
import logging
from datetime import date
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# QBO credentials from environment
QBO_CLIENT_ID = os.environ.get("QBO_CLIENT_ID", "")
QBO_CLIENT_SECRET = os.environ.get("QBO_CLIENT_SECRET", "")
QBO_REALM_ID = os.environ.get("QBO_REALM_ID", "")
QBO_REFRESH_TOKEN = os.environ.get("QBO_REFRESH_TOKEN", "")
QBO_ENVIRONMENT = os.environ.get("QBO_ENVIRONMENT", "sandbox")  # or 'production'

# Token cache file
TOKEN_CACHE = Path(__file__).parent / ".qbo_tokens.json"


class QBOClient:
    """QuickBooks Online API client."""

    def __init__(self):
        self._client = None
        self._connected = False
        # Local override: set to True if the SDK fails to import mid-run
        # (forces the instance into mock mode regardless of config).
        self._mock_override = False
        # Cooldown: timestamp of last failed connect attempt.
        # After a failure we skip retries for _CONNECT_COOLDOWN seconds.
        self._last_connect_failure: float = 0.0
        _CONNECT_COOLDOWN = 60  # seconds between retry attempts after failure
        self._connect_cooldown = _CONNECT_COOLDOWN
        from qbo import config as _qbo_config
        self._config = _qbo_config

        if self._config.is_mock_mode():
            print("[QBO] Running in mock mode (no real API calls)")

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def is_mock_mode(self) -> bool:
        # Re-evaluate each access; settings may change at runtime.
        return self._mock_override or self._config.is_mock_mode()

    # Internal alias kept for existing code paths that used ``_use_mock``.
    @property
    def _use_mock(self) -> bool:
        return self.is_mock_mode

    @_use_mock.setter
    def _use_mock(self, value: bool) -> None:
        # Only a True value is meaningful — callers use this to degrade
        # to mock when credentials/SDK are missing.
        if value:
            self._mock_override = True
    
    def connect(self) -> bool:
        """
        Establish connection to QBO.
        Returns True if connected (or mock mode), False on error.
        """
        if self._use_mock:
            self._connected = True
            return True

        # If we already failed recently, don't hammer the network.
        now = time.monotonic()
        if not self._connected and self._last_connect_failure:
            elapsed = now - self._last_connect_failure
            if elapsed < self._connect_cooldown:
                return False

        try:
            from quickbooks import QuickBooks
            from intuitlib.client import AuthClient

            # Read credentials dynamically so they pick up values set after
            # module import (e.g. from .env loaded in main.py).
            client_id     = os.environ.get("QBO_CLIENT_ID", QBO_CLIENT_ID)
            client_secret = os.environ.get("QBO_CLIENT_SECRET", QBO_CLIENT_SECRET)
            realm_id      = os.environ.get("QBO_REALM_ID", QBO_REALM_ID)
            environment   = os.environ.get("QBO_ENVIRONMENT", QBO_ENVIRONMENT)
            refresh_token_env = os.environ.get("QBO_REFRESH_TOKEN", QBO_REFRESH_TOKEN)

            # Set up auth client
            auth_client = AuthClient(
                client_id=client_id,
                client_secret=client_secret,
                environment=environment,
                redirect_uri="http://localhost:8765/callback",
            )

            # Load cached tokens if available
            if TOKEN_CACHE.exists():
                with open(TOKEN_CACHE) as f:
                    tokens = json.load(f)
                    auth_client.refresh_token = tokens.get("refresh_token", refresh_token_env)
            else:
                auth_client.refresh_token = refresh_token_env

            # Refresh access token
            auth_client.refresh()

            # Save updated tokens
            with open(TOKEN_CACHE, "w") as f:
                json.dump({
                    "refresh_token": auth_client.refresh_token,
                    "access_token": auth_client.access_token,
                }, f)

            # Create QBO client
            self._client = QuickBooks(
                auth_client=auth_client,
                refresh_token=auth_client.refresh_token,
                company_id=realm_id,
            )

            self._connected = True
            return True

        except ImportError:
            print("[QBO] SDK not installed. Run: pip install intuit-oauth quickbooks-python")
            self._use_mock = True
            self._connected = True
            return True
        except Exception as e:
            print(f"[QBO] Connection error: {e}")
            self._last_connect_failure = time.monotonic()
            return False
    
    def fetch_all_items(self) -> list[dict]:
        """
        Fetch all inventory items from QBO.
        Returns list of dicts with: id, name, sku, type, qty_on_hand, cost, price
        """
        if self._use_mock:
            return self._get_mock_items()
        
        if not self._connected:
            if not self.connect():
                return []
        
        try:
            from quickbooks.objects.item import Item
            from qbo._throttle import throttle
            throttle()
            items = Item.all(qb=self._client, max_results=1000)
            
            result = []
            for item in items:
                result.append({
                    "id": str(item.Id),
                    "name": item.Name or "",
                    "sku": item.Sku or "",
                    "type": item.Type or "Inventory",
                    "qty_on_hand": float(item.QtyOnHand or 0),
                    "cost": float(item.PurchaseCost or 0),
                    "price": float(item.UnitPrice or 0),
                    "description": item.Description or "",
                    "active": item.Active,
                })
            
            return result
            
        except Exception as e:
            print(f"[QBO] Error fetching items: {e}")
            return []
    
    def search_by_sku(self, sku: str) -> dict | None:
        """Find a single item by SKU."""
        if self._use_mock:
            for item in self._get_mock_items():
                if item["sku"].upper() == sku.upper():
                    return item
            return None
        
        if not self._connected:
            if not self.connect():
                return None
        
        try:
            from quickbooks.objects.item import Item
            from qbo._throttle import throttle
            throttle()
            items = Item.filter(Sku=sku, qb=self._client)
            if items:
                item = items[0]
                return {
                    "id": str(item.Id),
                    "name": item.Name or "",
                    "sku": item.Sku or "",
                    "type": item.Type or "Inventory",
                    "qty_on_hand": float(item.QtyOnHand or 0),
                    "cost": float(item.PurchaseCost or 0),
                    "price": float(item.UnitPrice or 0),
                }
            return None
            
        except Exception as e:
            print(f"[QBO] Error searching SKU {sku}: {e}")
            return None

    # ------------------------------------------------------------------
    # Account auto-provisioning for picky sandbox companies
    # ------------------------------------------------------------------
    _JAKS_INCOME_NAME = "JAKS Sales of Product Income"
    _JAKS_COGS_NAME = "JAKS Cost of Goods Sold"
    _JAKS_ASSET_NAME = "JAKS Inventory Asset"

    def _ensure_jaks_item_accounts(self, item_type: str) -> dict | None:
        """Create (or look up) JAKS-dedicated Income/COGS/Asset accounts.

        Used when the seed accounts in the connected QBO company are
        visible but rejected as references during Item creation (a
        known sandbox quirk). The fresh accounts are guaranteed to be
        valid references because we create them in the same realm.

        On success the chosen IDs are persisted via
        ``qbo.config.set_account_mapping`` so subsequent calls skip
        the recovery path.
        """
        try:
            from quickbooks.objects.account import Account
        except Exception:
            return None

        def _find_or_create(name: str, account_type: str, sub_type: str):
            try:
                existing = Account.where(
                    f"Name = '{name}'", qb=self._client
                )
                if existing:
                    return existing[0]
            except Exception:
                pass
            try:
                acct = Account()
                acct.Name = name
                acct.AccountType = account_type
                acct.AccountSubType = sub_type
                acct.save(qb=self._client)
                return acct
            except Exception as exc:
                print(f"[QBO] Could not provision '{name}': {exc}")
                return None

        income = _find_or_create(
            self._JAKS_INCOME_NAME, "Income", "SalesOfProductIncome"
        )
        expense = _find_or_create(
            self._JAKS_COGS_NAME,
            "Cost of Goods Sold",
            "SuppliesMaterialsCogs",
        )
        asset = None
        if item_type == "Inventory":
            asset = _find_or_create(
                self._JAKS_ASSET_NAME, "Other Current Asset", "Inventory"
            )

        if not income or not expense or (item_type == "Inventory" and not asset):
            return None

        # Persist for next time so the scan is skipped.
        try:
            from qbo.config import set_account_mapping
            mapping = {
                "sales_income": str(income.Id),
                "cogs": str(expense.Id),
            }
            if asset is not None:
                mapping["inventory_asset"] = str(asset.Id)
            set_account_mapping(mapping)
        except Exception:
            pass

        return {"income": income, "expense": expense, "asset": asset}

    def _get_mock_items(self) -> list[dict]:
        """Return mock QBO items for testing without credentials."""
        # Simulate some existing QBO inventory
        return [
            {
                "id": "1001",
                "name": "Caterpillar C15 Turbocharger",
                "sku": "JAKS-10R1273",
                "type": "Inventory",
                "qty_on_hand": 2,
                "cost": 850.00,
                "price": 1299.99,
                "description": "Reman turbocharger for Cat C15",
                "active": True,
            },
            {
                "id": "1002",
                "name": "Cat 3406E Fuel Injector",
                "sku": "JAKS-1278216",
                "type": "Inventory",
                "qty_on_hand": 5,
                "cost": 125.00,
                "price": 225.00,
                "description": "New fuel injector",
                "active": True,
            },
            {
                "id": "1003",
                "name": "Cummins ISX Water Pump",
                "sku": "JAKS-4089908",
                "type": "Inventory",
                "qty_on_hand": 1,
                "cost": 275.00,
                "price": 425.00,
                "description": "Water pump for ISX",
                "active": True,
            },
            {
                "id": "1004",
                "name": "Detroit DD15 Oil Cooler",
                "sku": "JAKS-A4721801065",
                "type": "Inventory",
                "qty_on_hand": 0,
                "cost": 450.00,
                "price": 695.00,
                "description": "OEM oil cooler",
                "active": True,
            },
            {
                "id": "1005",
                "name": "International DT466 Turbo",
                "sku": "JAKS-1825632C91",
                "type": "Inventory",
                "qty_on_hand": 3,
                "cost": 625.00,
                "price": 975.00,
                "description": "Remanufactured turbocharger",
                "active": True,
            },
        ]


    def create_item(
        self,
        sku: str,
        name: str,
        price: float,
        cost: float = 0.0,
        qty_on_hand: float = 0.0,
        description: str = "",
        *,
        income_account_id: str | None = None,
        expense_account_id: str | None = None,
        asset_account_id: str | None = None,
        item_type: str = "Inventory",
    ) -> dict | None:
        """
        Create a new inventory item in QBO.
        
        Args:
            sku: Product SKU
            name: Product name/title
            price: Selling price
            cost: Cost price (optional)
            qty_on_hand: Initial quantity (optional)
            description: Item description (optional)
        
        Returns:
            Created item dict or None on error
        """
        if self._use_mock:
            # Simulate creation in mock mode
            mock_id = str(9000 + len(self._get_mock_items()))
            return {
                "id": mock_id,
                "name": name,
                "sku": sku,
                "type": "Inventory",
                "qty_on_hand": qty_on_hand,
                "cost": cost,
                "price": price,
                "description": description,
                "active": True,
            }
        
        if not self._connected:
            if not self.connect():
                return None
        
        try:
            from quickbooks.objects.item import Item
            from quickbooks.objects.account import Account

            income_account = expense_account = asset_account = None

            # Prefer explicit IDs from settings (single fetch per account)
            if income_account_id:
                try:
                    income_account = Account.get(income_account_id, qb=self._client)
                except Exception as e:
                    print(f"[QBO] income_account_id={income_account_id} lookup failed: {e}")
            if expense_account_id:
                try:
                    expense_account = Account.get(expense_account_id, qb=self._client)
                except Exception as e:
                    print(f"[QBO] expense_account_id={expense_account_id} lookup failed: {e}")
            if asset_account_id and item_type == "Inventory":
                try:
                    asset_account = Account.get(asset_account_id, qb=self._client)
                except Exception as e:
                    print(f"[QBO] asset_account_id={asset_account_id} lookup failed: {e}")

            # Fallback: scan all accounts when IDs not provided
            need_scan = (not income_account) or (not expense_account) or (
                item_type == "Inventory" and not asset_account
            )
            if need_scan:
                all_accounts = Account.all(qb=self._client)
                # First pass: prefer AccountSubType matches
                for acc in all_accounts:
                    subtype = getattr(acc, 'AccountSubType', '') or ''
                    if not income_account and subtype == 'SalesOfProductIncome':
                        income_account = acc
                    elif not expense_account and subtype == 'CostOfGoodsSold':
                        expense_account = acc
                    elif not asset_account and subtype == 'Inventory':
                        asset_account = acc
                # Second pass: fall back to AccountType when subtype
                # wasn't set in the sandbox / company file.
                for acc in all_accounts:
                    atype = getattr(acc, 'AccountType', '') or ''
                    if not income_account and atype == 'Income':
                        income_account = acc
                    elif not expense_account and atype == 'Cost of Goods Sold':
                        expense_account = acc
                    elif not asset_account and atype == 'Other Current Asset':
                        asset_account = acc

            # Service items don't need an asset account
            if item_type == "Service":
                missing = (not income_account) or (not expense_account)
            else:
                missing = (not income_account) or (not expense_account) or (not asset_account)

            if missing:
                print("[QBO] Missing required accounts for item create")
                print(f"  type={item_type} income={income_account} expense={expense_account} asset={asset_account}")
                return None

            # Create item
            item = Item()
            item.Name = name[:100]  # QBO limit
            item.Sku = sku
            item.Type = item_type
            if item_type == "Inventory":
                item.TrackQtyOnHand = True
                item.QtyOnHand = qty_on_hand
                item.InvStartDate = date.today().isoformat()
            item.UnitPrice = price
            item.PurchaseCost = cost
            item.Description = description[:4000] if description else ""

            # Set account references
            item.IncomeAccountRef = income_account.to_ref()
            item.ExpenseAccountRef = expense_account.to_ref()
            if item_type == "Inventory":
                item.AssetAccountRef = asset_account.to_ref()
            
            # Save
            from qbo._throttle import throttle
            throttle()
            try:
                item.save(qb=self._client)
            except Exception as save_err:
                # Some QBO sandbox companies expose seed accounts that are
                # visible via the API but cannot be referenced from new
                # Items (the API responds "Invalid Reference Id : Accounts
                # element id NN not found"). When that happens, transparently
                # provision JAKS-dedicated accounts of the right subtype,
                # persist them so future syncs skip the scan, and retry.
                err_text = str(save_err)
                if "Invalid Reference Id" in err_text and "Accounts element" in err_text:
                    print(f"[QBO] Item create rejected ({err_text.strip()}); "
                          "auto-provisioning JAKS accounts and retrying.")
                    fresh = self._ensure_jaks_item_accounts(item_type)
                    if fresh:
                        item.IncomeAccountRef = fresh["income"].to_ref()
                        item.ExpenseAccountRef = fresh["expense"].to_ref()
                        if item_type == "Inventory":
                            item.AssetAccountRef = fresh["asset"].to_ref()
                        throttle()
                        item.save(qb=self._client)
                    else:
                        raise
                else:
                    raise
            
            return {
                "id": str(item.Id),
                "name": item.Name,
                "sku": item.Sku,
                "type": item.Type,
                "qty_on_hand": float(item.QtyOnHand or 0),
                "cost": float(item.PurchaseCost or 0),
                "price": float(item.UnitPrice or 0),
            }
            
        except Exception as e:
            err_text = str(e)
            # 6240 = "Duplicate Name Exists Error" — QBO already has an
            # Item with this name (often surfaces as `Id=NN`). Rather
            # than spamming the log, look the existing one up by SKU,
            # then by name, and return it so the caller can link the
            # local row to the existing QBO Id and move on.
            if "Duplicate Name" in err_text or "6240" in err_text:
                existing = None
                if sku:
                    try:
                        existing = self.search_by_sku(sku)
                    except Exception:
                        existing = None
                if not existing and name:
                    try:
                        from quickbooks.objects.item import Item
                        from qbo._throttle import throttle
                        throttle()
                        hits = Item.filter(Name=name[:100], qb=self._client)
                        if hits:
                            it = hits[0]
                            existing = {
                                "id": str(it.Id),
                                "name": it.Name or "",
                                "sku": it.Sku or "",
                                "type": it.Type or "Inventory",
                                "qty_on_hand": float(it.QtyOnHand or 0),
                                "cost": float(it.PurchaseCost or 0),
                                "price": float(it.UnitPrice or 0),
                            }
                    except Exception:
                        existing = None
                if existing:
                    existing["matched_existing"] = True
                    return existing
                print(f"[QBO] Duplicate name for '{name}' (sku={sku}) but lookup failed; skipping.")
                return None
            print(f"[QBO] Error creating item: {e}")
            return None
    
    def update_item(
        self,
        item_id: str,
        *,
        name: str | None = None,
        sku: str | None = None,
        price: float | None = None,
        cost: float | None = None,
        qty_on_hand: float | None = None,
        description: str | None = None,
    ) -> dict | None:
        """
        Update an existing QBO item.
        
        Args:
            item_id: QBO item ID
            name: New name (optional)
            price: New price (optional)
            cost: New cost (optional)
            qty_on_hand: New quantity (optional)
            description: New description (optional)
        
        Returns:
            Updated item dict or None on error
        """
        if self._use_mock:
            return {"id": item_id, "updated": True}
        
        if not self._connected:
            if not self.connect():
                return None
        
        try:
            from quickbooks.objects.item import Item
            from qbo._throttle import throttle
            
            # Fetch existing item
            throttle()
            item = Item.get(item_id, qb=self._client)
            if not item:
                print(f"[QBO] Item {item_id} not found")
                return None
            
            # Update fields
            if name is not None:
                item.Name = name[:100]
            if sku is not None:
                item.Sku = sku[:100]
            if price is not None:
                item.UnitPrice = price
            if cost is not None:
                item.PurchaseCost = cost
            if qty_on_hand is not None:
                item.QtyOnHand = qty_on_hand
            if description is not None:
                item.Description = description[:4000]
            
            # Save
            throttle()
            item.save(qb=self._client)
            
            return {
                "id": str(item.Id),
                "name": item.Name,
                "sku": item.Sku,
                "qty_on_hand": float(item.QtyOnHand or 0),
                "cost": float(item.PurchaseCost or 0),
                "price": float(item.UnitPrice or 0),
            }
            
        except Exception as e:
            print(f"[QBO] Error updating item {item_id}: {e}")
            return None
    
    def adjust_quantity(
        self,
        item_id: str,
        new_quantity: float,
        *,
        reason: str = "",
        adjust_account_id: str | None = None,
    ) -> dict | None:
        """
        Adjust quantity on hand for an item via a QBO InventoryAdjustment.

        Posts a proper ``InventoryAdjustment`` transaction so QBO maintains a
        full audit trail and books the GL impact against the configured
        shrinkage/COGS account. Falls back to a direct ``Item.QtyOnHand``
        update only if the InventoryAdjustment SDK module is unavailable.

        Args:
            item_id: QBO Item Id to adjust.
            new_quantity: Target on-hand quantity (absolute, not delta).
            reason: Optional memo (e.g., "Cycle count", "Shrinkage").
            adjust_account_id: QBO Account Id to book the offset against.
                Defaults to the mapped ``cogs`` account.
        """
        if not self.connect():
            return None

        # Resolve the adjustment account (defaults to COGS).
        if not adjust_account_id:
            try:
                from qbo.config import get_account_mapping
                adjust_account_id = (get_account_mapping().get("cogs") or "").strip() or None
            except Exception:
                adjust_account_id = None

        # Look up current qty so we can post a delta-style adjustment.
        try:
            from quickbooks.objects.item import Item
            from quickbooks.objects.inventoryadjustment import (
                InventoryAdjustment,
                InventoryAdjustmentLine,
                InventoryAdjustmentLineDetail,
            )
            from quickbooks.objects.account import Account
            from datetime import date
            from qbo._throttle import throttle

            throttle()
            item_obj = Item.get(item_id, qb=self._client)
            current_qty = float(getattr(item_obj, "QtyOnHand", 0) or 0)
            delta = float(new_quantity) - current_qty
            if abs(delta) < 1e-9:
                logger.info(f"adjust_quantity: no-op (item={item_id} qty={new_quantity})")
                return {"id": None, "action": "noop"}

            adj = InventoryAdjustment()
            adj.TxnDate = date.today().isoformat()
            if reason:
                adj.PrivateNote = reason[:4000]
            if adjust_account_id:
                acct = Account.get(adjust_account_id, qb=self._client)
                adj.AdjustAccountRef = acct.to_ref()

            detail = InventoryAdjustmentLineDetail()
            detail.ItemRef = item_obj.to_ref()
            detail.QtyDiff = delta

            line = InventoryAdjustmentLine()
            line.InventoryAdjustmentLineDetail = detail
            adj.Line = [line]

            # Phase 9: wrap the save in idempotent_write so retries do not
            # double-post the same adjustment to QBO. Key is
            # (item_id, qty_delta, txn_date) — same triple → same QBO row.
            from qbo._retry import idempotent_write
            txn_date_iso = adj.TxnDate
            local_ref = f"{item_id}:{delta:+.4f}:{txn_date_iso}"

            def _save() -> str:
                throttle()
                adj.save(qb=self._client)
                return str(adj.Id)

            outcome = idempotent_write(
                entity_type="inventory_adjustment",
                local_ref=local_ref,
                fn=_save,
            )
            if not outcome.get("success"):
                logger.warning(
                    "InventoryAdjustment idempotent_write failed: %s",
                    outcome.get("error"),
                )
                return None
            qbo_adj_id = str(outcome.get("qbo_id") or "")

            # Record the crosswalk row (Phase 9). Best-effort — never
            # block the caller if the bookkeeping write fails.
            try:
                from db.database import get_db
                with get_db() as conn:
                    conn.execute(
                        "INSERT OR IGNORE INTO qbo_inventory_adjustments "
                        "(sku, qbo_item_id, qbo_adj_id, qty_diff, txn_date, "
                        " idempotency_key, source, notes) "
                        "VALUES (?, ?, ?, ?, ?, ?, 'local', ?)",
                        (
                            getattr(item_obj, "Sku", None),
                            str(item_id),
                            qbo_adj_id,
                            float(delta),
                            txn_date_iso,
                            outcome.get("idempotency_key"),
                            (reason or "")[:500],
                        ),
                    )
                    conn.commit()
            except Exception as exc:
                logger.debug("crosswalk insert ignored: %s", exc)

            return {
                "id": qbo_adj_id,
                "action": outcome.get("action", "adjusted"),
                "delta": delta,
                "idempotency_key": outcome.get("idempotency_key"),
            }
        except ImportError:
            # SDK missing InventoryAdjustment module — fall back to direct update.
            logger.warning(
                "InventoryAdjustment not available in quickbooks SDK; "
                "falling back to direct Item.QtyOnHand update"
            )
            return self.update_item(item_id, qty_on_hand=new_quantity)
        except Exception as exc:
            logger.exception(f"InventoryAdjustment failed for item {item_id}: {exc}")
            return None

    # ------------------------------------------------------------------
    # Phase 10: invoice delivery — Send + PDF download
    # ------------------------------------------------------------------
    def send_invoice(self, qbo_invoice_id: str,
                     *, send_to: str | None = None) -> dict | None:
        """Send a QBO Invoice to the customer (POST /invoice/{id}/send).

        Uses the QBO SDK's ``Invoice.send`` operation; falls back to a
        raw API call against the same endpoint if the SDK version
        lacks the helper. Returns ``{"id": qbo_id, "delivered": True}``
        on success or ``None`` on failure.
        """
        if not self.connect():
            return None
        if self._use_mock:
            logger.info(f"send_invoice (mock): {qbo_invoice_id} -> {send_to}")
            return {"id": qbo_invoice_id, "delivered": True, "is_mock": True}
        try:
            from quickbooks.objects.invoice import Invoice
            from qbo._throttle import throttle

            throttle()
            inv = Invoice.get(int(qbo_invoice_id), qb=self._client)
            # SDK >= 0.9 exposes .send(); older versions need a manual POST.
            if hasattr(inv, "send"):
                kwargs: dict[str, Any] = {"qb": self._client}
                if send_to:
                    kwargs["send_to"] = send_to
                inv.send(**kwargs)
            else:  # pragma: no cover - fallback for older SDKs
                endpoint = (
                    f"{self._client.api_url}/company/{self._client.company_id}"
                    f"/invoice/{qbo_invoice_id}/send"
                )
                if send_to:
                    endpoint += f"?sendTo={send_to}"
                self._client.session.post(endpoint)
            return {"id": qbo_invoice_id, "delivered": True}
        except Exception as exc:
            logger.exception(f"send_invoice failed: {exc}")
            return None

    def get_invoice_pdf(self, qbo_invoice_id: str) -> bytes | None:
        """Download a QBO Invoice as PDF bytes.

        Returns the raw PDF body or ``None`` on failure. Mock mode
        returns a tiny placeholder so callers can verify wiring.
        """
        if not self.connect():
            return None
        if self._use_mock:
            return b"%PDF-1.4\n%mock\n"
        try:
            from quickbooks.objects.invoice import Invoice
            from qbo._throttle import throttle

            throttle()
            inv = Invoice.get(int(qbo_invoice_id), qb=self._client)
            if hasattr(inv, "download_pdf"):
                return inv.download_pdf(qb=self._client)
            # Manual fallback for older SDK builds.
            endpoint = (
                f"{self._client.api_url}/company/{self._client.company_id}"
                f"/invoice/{qbo_invoice_id}/pdf"
            )
            resp = self._client.session.get(
                endpoint, headers={"Accept": "application/pdf"},
            )
            return resp.content if getattr(resp, "ok", False) else None
        except Exception as exc:
            logger.exception(f"get_invoice_pdf failed: {exc}")
            return None


# Singleton instance
_qbo_client: QBOClient | None = None


def get_qbo_client() -> QBOClient:
    """Get or create the QBO client singleton."""
    global _qbo_client
    if _qbo_client is None:
        _qbo_client = QBOClient()
    return _qbo_client


def reset_qbo_client() -> None:
    """Drop the cached singleton so the next ``get_qbo_client()`` picks
    up freshly-written tokens / env vars (used after OAuth Connect /
    Disconnect)."""
    global _qbo_client
    _qbo_client = None

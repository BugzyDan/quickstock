"""
Sync API views for bidirectional data synchronization.

This module provides REST API endpoints for:
- Pushing local changes to the server
- Pulling remote changes from the server
- Bidirectional sync
- Sync status and history
"""

import hashlib
import re
from decimal import Decimal, InvalidOperation
from datetime import datetime, timedelta, timezone as datetime_timezone
from typing import Any, Dict, List, Tuple
from django.contrib.auth import get_user_model
from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, transaction
from .utils import get_effective_owner
from django.http import JsonResponse
from django.contrib.auth import authenticate
from rest_framework.authtoken.models import Token
from django.utils import timezone as django_timezone
from django.views.decorators.http import require_http_methods
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.core.exceptions import FieldError, ValidationError
import logging
from django.contrib.auth.models import User
from rest_framework.permissions import AllowAny
from django.db.models import Q
from quickstock.auth import SubscriptionTokenAuthentication as TokenAuthentication
from inventory import _get_tax_rate_for_location
from .checkout_contract import (
    CHECKOUT_CONTRACT_VERSION,
    MONEY_QUANTUM,
    CheckoutContractError,
    parse_checkout_contract,
)

# Models are imported from the relative path based on context
from inventory.storage import load_seed_products_for_owner
from inventory.models import (
    AuditLog,
    Brand,
    CashShift,
    Category,
    Customer,
    Item,
    Location,
    Sale,
    SaleItem,
    StockRecord,
    Supplier,
    UserProfile,
)
from inventory.sales import SaleWorkflowError, finalize_sale
User = get_user_model()



logger = logging.getLogger("inventory")


# ==================== Helper Functions ====================

def _api_login_failure_key(request, username: str) -> str:
    """Return a cache-safe key scoped to the client address and username."""
    remote_addr = str(request.META.get("REMOTE_ADDR") or "unknown").strip()
    identity = f"{remote_addr}|{str(username or '').strip().casefold()}"
    return f"api_login_fail:{hashlib.sha256(identity.encode('utf-8')).hexdigest()}"


def _record_api_login_failure(cache_key: str, window_seconds: int) -> None:
    cache.add(cache_key, 0, timeout=window_seconds)
    try:
        cache.incr(cache_key)
    except ValueError:
        cache.set(cache_key, 1, timeout=window_seconds)

def get_user_effective_owner(user):
    """
    Standardizes data ownership. Staff act on behalf of the Business Owner.
    """
    return get_effective_owner(user)

def item_to_sync_dict(item: Item, location_id: int | None = None, location_name: str | None = None) -> Dict[str, Any]:
    """Convert an Item model to a sync-friendly dictionary."""
    quantity = item.quantity
    if location_id:
        stock_record = next(
            (record for record in item.stock_at_locations.all() if record.location_id == location_id),
            None,
        )
        if stock_record is not None:
            quantity = stock_record.quantity
        elif (location_name or "").strip().lower() != "main store":
            quantity = 0

    return {
        "id": item.id,
        "sku": item.sku,
        "barcode": item.barcode or "",
        "name": item.name,
        "price": str(item.price),
        "cost_price": str(item.cost_price),
        "quantity": quantity,
        "category": item.category.name if item.category else None,
        "category_id": item.category_id,
        "is_taxable": item.is_taxable,
        "sync_token": item.sync_token,
        "last_modified": item.last_modified.isoformat() if item.last_modified else None,
        "is_deleted": item.is_deleted,
        "sync_source": item.sync_source,
    }

def sale_to_sync_dict(sale: Sale) -> Dict[str, Any]:
    """Convert a Sale model to a sync-friendly dictionary."""
    items = []
    for sale_item in sale.items.all():
        items.append({
            "id": sale_item.id,
            "item_sku": sale_item.item.sku if sale_item.item else None,
            "item_name": sale_item.item.name if sale_item.item else None,
            "quantity": sale_item.quantity,
            "unit_price": str(sale_item.unit_price),
            "net_amount": str(sale_item.net_amount),
            "tax_amount": str(sale_item.tax_amount),
            "total_price": str(sale_item.total_price),
        })

    return {
        "id": sale.id,
        "receipt_no": sale.receipt_no,
        "timestamp": sale.timestamp.isoformat() if sale.timestamp else None,
        "tender": sale.tender,
        "subtotal": str(sale.subtotal),
        "gct_amount": str(sale.gct_amount),
        "total_price": str(sale.total_price),
        "discount": str(sale.discount),
        "location_id": sale.location_id,
        "cashier_id": sale.cashier_id,
        "sync_token": sale.sync_token,
        "last_modified": sale.last_modified.isoformat() if sale.last_modified else None,
        "is_synced": sale.is_synced,
        "sync_source": sale.sync_source,
        "items": items,
    }

def resolve_conflict(local_item: Dict, remote_item: Dict) -> Dict:
    """
    Resolve conflict between local (incoming push) and remote (server state).
    Strategy: Deterministic Last-Write-Wins (LWW).
    """
    try:
        local_time = datetime.fromisoformat((local_item.get("last_modified") or "").replace("Z", "+00:00"))
        remote_time = datetime.fromisoformat((remote_item.get("last_modified") or "").replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return remote_item

    # Server state wins on timestamp ties for stability
    if remote_time >= local_time:
        return remote_item
    
    return local_item

def _coerce_sync_quantity(value: Any, default: int = 0) -> int:
    if value in (None, ""):
        return default
    try:
        quantity = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Quantity must be a whole number.") from exc
    if quantity < 0:
        raise ValueError("Quantity cannot be negative.")
    return quantity


def _safe_sync_error(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        if hasattr(exc, "message_dict"):
            return "; ".join(
                f"{field}: {', '.join(messages)}"
                for field, messages in exc.message_dict.items()
            )[:500]
        return "; ".join(exc.messages)[:500]
    if isinstance(exc, ValueError):
        return str(exc)[:500] or "Invalid numeric value."
    if isinstance(exc, (InvalidOperation, TypeError)):
        return "Invalid numeric value."
    if isinstance(exc, IntegrityError):
        return "The record conflicts with existing data."
    logger.exception("Unexpected sync entry failure")
    return "The record could not be processed."


def _sync_client_id(value: Any) -> str:
    return str(value or "unknown").strip()[:100] or "unknown"


def _desktop_sale_acknowledgement(sale: Sale, client_reference: str, *, replay: bool = False):
    return JsonResponse(
        {
            "ok": True,
            "success": True,
            "sale_id": sale.id,
            "receipt_no": sale.receipt_no,
            "total_price": str(sale.total_price),
            "amount_paid": str(sale.amount_paid),
            "change_due": str(sale.change_due),
            "client_reference": client_reference,
            "contract_version": CHECKOUT_CONTRACT_VERSION,
            "idempotent_replay": replay,
        }
    )


def _checkout_error(code: str, field: str, message: str, *, status: int = 400):
    return JsonResponse(
        {
            "ok": False,
            "success": False,
            "code": code,
            "field": field,
            "message": message,
            "error": message,
        },
        status=status,
    )


def _desktop_register_snapshot(user):
    shift = CashShift.get_active_shift(user)
    if not shift:
        return None
    return {
        "id": shift.id,
        "location_id": shift.location_id,
        "opened_at": shift.opened_at.isoformat(),
        "is_open": not shift.is_closed and shift.end_time is None,
    }


def _checkout_tax_label(location: Location) -> str:
    return "GCT" if str(location.country_code or "JM").upper() == "JM" else "VAT"


def _record_desktop_sale(request):
    try:
        contract = parse_checkout_contract(request.data)
    except CheckoutContractError as exc:
        return JsonResponse(exc.as_payload(), status=exc.status)

    max_cart_lines = max(1, int(getattr(settings, "POS_CART_MAX_LINES", 200)))
    if len(contract.line_items) > max_cart_lines:
        return _checkout_error(
            "CART_TOO_LARGE",
            "line_items",
            f"A sale may contain at most {max_cart_lines} lines.",
            status=413,
        )

    owner = get_user_effective_owner(request.user)
    profile = UserProfile.for_user(request.user)
    expected_role = "superuser" if request.user.is_superuser else str(profile.role or "").lower()
    if not request.user.is_active or (not request.user.is_superuser and profile.status != "active"):
        return _checkout_error("OPERATOR_INACTIVE", "operator", "Operator account is not active.", status=403)
    if expected_role not in {"superuser", "admin", "manager", "cashier"}:
        return _checkout_error("OPERATOR_UNAUTHORIZED", "operator.role", "Operator is not permitted to sell.", status=403)
    if contract.operator_id != request.user.id or contract.operator_role != expected_role:
        return _checkout_error("OPERATOR_MISMATCH", "operator", "Checkout operator does not match the authenticated user.", status=403)
    if contract.tenant_id != owner.id:
        return _checkout_error("TENANT_MISMATCH", "tenant.id", "Checkout tenant does not match the authenticated workspace.", status=403)
    if contract.payment_method not in dict(Sale.TENDER_CHOICES):
        return _checkout_error("INVALID_PAYMENT_METHOD", "payment.method", "Invalid payment method.")
    if not contract.tax_inclusive:
        return _checkout_error("INVALID_TAX", "tax.inclusive", "Desktop shelf prices must be tax-inclusive.")

    client_reference = contract.reference

    try:
        with transaction.atomic():
            location = Location.objects.select_for_update().filter(
                id=contract.location_id,
                owner=owner,
            ).first()
            if not location:
                return _checkout_error("INVALID_LOCATION", "location.id", "Location not found.", status=404)
            if not request.user.is_superuser and not location.can_be_accessed_by(request.user):
                return _checkout_error(
                    "LOCATION_FORBIDDEN",
                    "location.id",
                    "Operator is not assigned to this location.",
                    status=403,
                )
            if contract.register_location_id != location.id:
                return _checkout_error(
                    "REGISTER_LOCATION_MISMATCH",
                    "register.location_id",
                    "Register does not belong to the checkout location.",
                )

            sale_shift = CashShift.objects.select_for_update().filter(
                id=contract.register_id,
                cashier=request.user,
                location=location,
            ).first()
            if not sale_shift:
                return _checkout_error(
                    "REGISTER_UNAVAILABLE",
                    "register.id",
                    "Register is not assigned to this operator and location.",
                    status=403,
                )
            opened_at = sale_shift.opened_at.astimezone(datetime_timezone.utc)
            if abs((contract.register_opened_at - opened_at).total_seconds()) > 1:
                return _checkout_error(
                    "REGISTER_SESSION_MISMATCH",
                    "register.opened_at",
                    "Register session does not match the server record.",
                    status=409,
                )
            if contract.occurred_at < opened_at:
                return _checkout_error(
                    "REGISTER_CLOSED",
                    "occurred_at",
                    "Checkout occurred before the register was opened.",
                    status=409,
                )
            if sale_shift.end_time and contract.occurred_at > sale_shift.end_time.astimezone(datetime_timezone.utc):
                return _checkout_error(
                    "REGISTER_CLOSED",
                    "occurred_at",
                    "Checkout occurred after the register was closed.",
                    status=409,
                )
            if contract.occurred_at > django_timezone.now().astimezone(datetime_timezone.utc) + timedelta(minutes=5):
                return _checkout_error(
                    "INVALID_TIMESTAMP",
                    "occurred_at",
                    "Checkout timestamp is too far in the future.",
                )

            existing_sale = Sale.objects.filter(owner=owner, sync_token=client_reference).first()
            if existing_sale:
                if existing_sale.cashier_id != request.user.id:
                    return _checkout_error(
                        "CLIENT_REFERENCE_CONFLICT",
                        "offline_client_ref",
                        "Sale reference is already in use.",
                        status=409,
                    )
                return _desktop_sale_acknowledgement(existing_sale, client_reference, replay=True)

            customer = None
            if contract.customer_id:
                customer = Customer.objects.select_for_update().filter(
                    id=contract.customer_id,
                    owner=owner,
                ).first()
                if not customer:
                    return _checkout_error(
                        "INVALID_CUSTOMER",
                        "customer.id",
                        "Customer is not available in this workspace.",
                        status=404,
                    )
                if contract.customer_name and contract.customer_name.casefold() != customer.name.casefold():
                    return _checkout_error(
                        "CUSTOMER_MISMATCH",
                        "customer.name",
                        "Customer name does not match the selected customer.",
                    )
            if (contract.account_credit > 0 or contract.credited_overpayment > 0) and not customer:
                return _checkout_error(
                    "CUSTOMER_REQUIRED",
                    "customer.id",
                    "A tenant customer is required for account credit.",
                )
            if customer and contract.account_credit > customer.credit_balance:
                return _checkout_error(
                    "INSUFFICIENT_CUSTOMER_CREDIT",
                    "payment.account_credit",
                    "Customer account credit is insufficient.",
                )

            requested_ids = [line.product_id for line in contract.line_items]
            items = {
                item.id: item
                for item in Item.objects.select_for_update().filter(owner=owner, id__in=requested_ids)
            }
            stock_records = {
                stock.item_id: stock
                for stock in StockRecord.objects.select_for_update().filter(
                    item_id__in=requested_ids,
                    location=location,
                )
            }
            line_items = []
            gross_total = Decimal("0.00")
            net_subtotal = Decimal("0.00")
            tax_total = Decimal("0.00")
            location_tax_rate = _get_tax_rate_for_location(location).quantize(Decimal("0.0001"))
            expected_tax_label = _checkout_tax_label(location)
            if contract.tax_rate != location_tax_rate or contract.tax_label != expected_tax_label:
                return _checkout_error(
                    "INVALID_TAX",
                    "tax",
                    "Checkout tax does not match the selected location.",
                )

            for index, checkout_line in enumerate(contract.line_items):
                item = items.get(checkout_line.product_id)
                if not item:
                    return _checkout_error(
                        "ITEM_NOT_FOUND",
                        f"line_items.{index}.product_id",
                        f"Item {checkout_line.product_id} not found.",
                        status=404,
                    )
                if str(item.sku or "").upper() != checkout_line.sku:
                    return _checkout_error(
                        "ITEM_IDENTITY_MISMATCH",
                        f"line_items.{index}.sku",
                        "Item ID and SKU do not identify the same product.",
                    )
                if item.is_deleted or item.status != "active":
                    return _checkout_error(
                        "ITEM_UNAVAILABLE",
                        f"line_items.{index}",
                        f"{item.name} is archived or unavailable for sale.",
                    )
                if checkout_line.is_deleted or checkout_line.status != "active":
                    return _checkout_error(
                        "STALE_ITEM_STATE",
                        f"line_items.{index}",
                        f"Desktop item state for {item.name} is not active.",
                    )
                if checkout_line.unit_price != item.price.quantize(MONEY_QUANTUM):
                    return _checkout_error(
                        "STALE_PRICE",
                        f"line_items.{index}.unit_price",
                        f"Price for {item.name} has changed. Synchronize inventory and try again.",
                        status=409,
                    )
                if checkout_line.unit_cost != item.cost_price.quantize(MONEY_QUANTUM):
                    return _checkout_error(
                        "STALE_COST",
                        f"line_items.{index}.unit_cost",
                        f"Cost for {item.name} has changed. Synchronize inventory and try again.",
                        status=409,
                    )
                expected_line_rate = location_tax_rate if item.is_taxable else Decimal("0.0000")
                if checkout_line.is_taxable != item.is_taxable or checkout_line.tax_rate != expected_line_rate:
                    return _checkout_error(
                        "INVALID_TAX",
                        f"line_items.{index}.tax_rate",
                        f"Tax treatment for {item.name} does not match the server record.",
                    )

                quantity = checkout_line.quantity
                stock = stock_records.get(item.id)
                if not stock or stock.quantity < quantity:
                    return _checkout_error(
                        "INSUFFICIENT_STOCK",
                        f"line_items.{index}.quantity",
                        f"Insufficient stock for {item.name}.",
                    )
                if item.price <= 0:
                    return _checkout_error("INVALID_PRICE", f"line_items.{index}.unit_price", f"{item.name} has no valid price.")

                line_gross = (item.price * quantity).quantize(MONEY_QUANTUM)
                if item.is_taxable:
                    line_net = (line_gross / (Decimal("1.0000") + location_tax_rate)).quantize(MONEY_QUANTUM)
                    line_tax = (line_gross - line_net).quantize(MONEY_QUANTUM)
                else:
                    line_net = line_gross
                    line_tax = Decimal("0.00")
                gross_total += line_gross
                net_subtotal += line_net
                tax_total += line_tax
                line_items.append(
                    (item, stock, quantity, item.price, (expected_line_rate * Decimal("100")).quantize(MONEY_QUANTUM))
                )

            gross_total = gross_total.quantize(MONEY_QUANTUM)
            net_subtotal = net_subtotal.quantize(MONEY_QUANTUM)
            tax_total = tax_total.quantize(MONEY_QUANTUM)
            if contract.discount > gross_total:
                return _checkout_error(
                    "INVALID_DISCOUNT",
                    "totals.discount",
                    "Discount cannot exceed the gross sale total.",
                )
            canonical_total = (gross_total - contract.discount).quantize(MONEY_QUANTUM)
            for field, supplied, expected in (
                ("totals.subtotal", contract.subtotal, net_subtotal),
                ("totals.tax", contract.tax_amount, tax_total),
                ("totals.total", contract.total, canonical_total),
            ):
                if supplied != expected:
                    return _checkout_error(
                        "INVALID_TOTAL" if field != "totals.tax" else "INVALID_TAX",
                        field,
                        f"{field} does not match the server-calculated amount.",
                    )
            if (contract.subtotal + contract.tax_amount - contract.discount).quantize(MONEY_QUANTUM) != contract.total:
                return _checkout_error(
                    "INVALID_TOTAL",
                    "totals.total",
                    "Subtotal minus discount plus tax must equal total.",
                )

            funding_total = (contract.amount_tendered + contract.account_credit).quantize(MONEY_QUANTUM)
            if funding_total < canonical_total:
                return _checkout_error(
                    "UNDERPAYMENT",
                    "payment.amount_tendered",
                    "Payment and account credit do not cover the checkout total.",
                )
            overage = (funding_total - canonical_total).quantize(MONEY_QUANTUM)
            expected_change = overage if contract.payment_method == "cash" else Decimal("0.00")
            expected_overpayment_credit = overage if contract.payment_method != "cash" else Decimal("0.00")
            if contract.change_due != expected_change:
                return _checkout_error(
                    "INVALID_CHANGE",
                    "payment.change_due",
                    "Change due does not match the checkout payment.",
                )
            if contract.credited_overpayment != expected_overpayment_credit:
                return _checkout_error(
                    "INVALID_OVERPAYMENT",
                    "payment.credited_overpayment",
                    "Credited overpayment does not match the checkout payment.",
                )
            if expected_overpayment_credit > 0 and not customer:
                return _checkout_error(
                    "CUSTOMER_REQUIRED",
                    "customer.id",
                    "A tenant customer is required to store non-cash overpayment.",
                )

            sale = finalize_sale(
                owner=owner,
                cashier=request.user,
                location=location,
                shift=sale_shift,
                tender=contract.payment_method,
                discount=contract.discount,
                line_items=line_items,
                sync_token=client_reference,
                sync_source="desktop",
                timestamp=contract.occurred_at,
            )
            sale.customer_name = customer.name if customer else contract.customer_name
            sale.amount_paid = funding_total
            sale.change_due = contract.change_due
            sale.save(
                update_fields=["customer_name", "amount_paid", "change_due", "last_modified"],
                skip_validation=True,
            )

            if customer:
                if contract.account_credit > Decimal("0.00"):
                    customer.use_credit(
                        contract.account_credit,
                        movement_type="pos_credit_application",
                        created_by=request.user,
                        sale=sale,
                        reason=f"Applied account credit to POS sale {sale.id}.",
                    )
                if contract.credited_overpayment > Decimal("0.00"):
                    customer.add_credit(
                        contract.credited_overpayment,
                        movement_type="pos_overpayment",
                        created_by=request.user,
                        sale=sale,
                        reason=f"Credited non-cash overpayment from POS sale {sale.id}.",
                    )

            AuditLog.objects.create(
                user=request.user,
                action="sale",
                message="Desktop checkout committed",
                metadata={
                    "sale_id": sale.id,
                    "receipt_no": sale.receipt_no,
                    "client_reference": client_reference,
                    "location_id": location.id,
                    "register_id": sale_shift.id,
                    "total": str(sale.total_price),
                    "contract_version": CHECKOUT_CONTRACT_VERSION,
                },
                sync_type="sales",
                direction="push",
                status="completed",
                completed_at=django_timezone.now(),
            )
    except SaleWorkflowError as exc:
        return _checkout_error("SALE_VALIDATION_FAILED", "checkout", exc.message, status=exc.status)
    except CheckoutContractError as exc:
        return JsonResponse(exc.as_payload(), status=exc.status)
    except ValidationError as exc:
        return _checkout_error("SALE_VALIDATION_FAILED", "checkout", _safe_sync_error(exc))
    except IntegrityError:
        # A concurrent request can pass the first lookup before the unique
        # owner/reference constraint commits. Treat the committed winner as
        # the acknowledgement for this same cashier and reference.
        existing_sale = Sale.objects.filter(owner=owner, sync_token=client_reference).first()
        if existing_sale and existing_sale.cashier_id == request.user.id:
            return _desktop_sale_acknowledgement(existing_sale, client_reference, replay=True)
        if existing_sale:
            return _checkout_error(
                "CLIENT_REFERENCE_CONFLICT",
                "offline_client_ref",
                "Sale reference is already in use.",
                status=409,
            )
        logger.exception("Desktop sale idempotency constraint failed without a committed sale")
        return _checkout_error(
            "IDEMPOTENCY_CONFLICT",
            "offline_client_ref",
            "Sale could not be recorded safely. Retry with the same reference.",
            status=409,
        )

    return _desktop_sale_acknowledgement(sale, client_reference)

def _resolve_inventory_sync_category(item_data: Dict, effective_owner: User) -> Category:
    category_id = item_data.get("category_id")
    if category_id:
        category = Category.objects.filter(id=category_id, owner=effective_owner).first()
        if category:
            return category

    category_name = item_data.get("category") or item_data.get("Category") or "General"
    category, _ = Category.objects.get_or_create(owner=effective_owner, name=category_name)
    return category

def _resolve_inventory_sync_brand(item_data: Dict, effective_owner: User) -> Brand:
    brand_name = item_data.get("brand") or item_data.get("Brand") or "Generic"
    brand, _ = Brand.objects.get_or_create(owner=effective_owner, name=brand_name)
    return brand

def _resolve_inventory_sync_location(item_data: Dict, effective_owner: User) -> Location:
    raw_location_id = item_data.get("location_id") or item_data.get("location")
    if raw_location_id:
        location = Location.objects.filter(id=raw_location_id, owner=effective_owner).first()
        if location:
            return location

    location, _ = Location.objects.get_or_create(
        owner=effective_owner,
        name="Main Store",
        defaults={"country_code": "JM"},
    )
    return location

def _process_inventory_item_sync(item_data: Dict, effective_owner: User) -> Tuple[str, str]:
    """
    Internal handler to process a single inventory item during push.
    Returns (status, error_message). Status can be 'pushed', 'updated', 'skipped', 'failed'.
    """
    try:
        with transaction.atomic():
            if not isinstance(item_data, dict):
                raise ValueError("Inventory entry must be an object.")
            sku = str(item_data.get("sku") or "").strip()
            if not sku:
                raise ValueError("Missing SKU")

            is_deleted = item_data.get("is_deleted", False) is True
            remote_token = item_data.get("sync_token")
            incoming_quantity = _coerce_sync_quantity(
                item_data.get("quantity_on_hand", item_data.get("quantity", item_data.get("Amount", 0)))
            )

            existing_item = Item.objects.filter(sku=sku, owner=effective_owner).first()

            if existing_item:
                local_token = existing_item.sync_token
                if local_token and remote_token and local_token == remote_token:
                    return "skipped", ""

                if is_deleted:
                    existing_item.is_deleted = True
                    existing_item.save()
                    return "updated", ""

                if local_token and remote_token and local_token != remote_token:
                    remote_item = item_to_sync_dict(existing_item)
                    winner = resolve_conflict(item_data, remote_item)
                
                    AuditLog.objects.create(
                        user=effective_owner,
                        action="inventory",
                        severity="warn",
                        message=f"Sync conflict resolved for SKU {sku}. Winner: {'Client' if winner == item_data else 'Server'}",
                        metadata={
                            "sku": sku,
                            "local_modified": item_data.get("last_modified"),
                            "remote_modified": remote_item.get("last_modified")
                        }
                    )

                    if winner == remote_item:
                        return "skipped", "Conflict: Remote wins"

                existing_item.name = item_data.get("name", existing_item.name)
                existing_item.barcode = item_data.get("barcode", existing_item.barcode)
                existing_item.price = Decimal(str(item_data.get("price", existing_item.price)))
                existing_item.cost_price = Decimal(str(item_data.get("cost_price", existing_item.cost_price)))
                existing_item.is_taxable = item_data.get("is_taxable", existing_item.is_taxable)
                existing_item.sync_source = "desktop"
                existing_item.category = _resolve_inventory_sync_category(item_data, effective_owner)

                existing_item.save()
                StockRecord.objects.update_or_create(
                    item=existing_item,
                    location=_resolve_inventory_sync_location(item_data, effective_owner),
                    defaults={"quantity": incoming_quantity},
                )
                existing_item.mark_synced(remote_token)
                return "updated", ""

            if is_deleted:
                return "skipped", ""

            new_item = Item.objects.create(
                sku=sku,
                barcode=item_data.get("barcode") or None,
                name=item_data.get("name", sku),
                price=Decimal(str(item_data.get("price", "0.00"))),
                cost_price=Decimal(str(item_data.get("cost_price", "0.00"))),
                is_taxable=item_data.get("is_taxable", True),
                category=_resolve_inventory_sync_category(item_data, effective_owner),
                brand=_resolve_inventory_sync_brand(item_data, effective_owner),
                owner=effective_owner,
                sync_source="desktop",
            )
            StockRecord.objects.update_or_create(
                item=new_item,
                location=_resolve_inventory_sync_location(item_data, effective_owner),
                defaults={"quantity": incoming_quantity},
            )
            new_item.mark_synced(remote_token)
            return "pushed", ""
    except Exception as exc:
        return "failed", _safe_sync_error(exc)

def _process_sale_entry_sync(sale_data: Dict, effective_owner: User, cashier: User) -> Tuple[str, str]:
    """
    Internal handler to process a single sale during push.
    Maintains 'Ledger of Truth' integrity by preserving original timestamps.
    """
    try:
        with transaction.atomic():
            if not isinstance(sale_data, dict):
                raise ValueError("Sale entry must be an object.")
            receipt_no = int(sale_data.get("receipt_no"))
            if receipt_no <= 0:
                raise ValueError("Receipt number must be greater than zero.")

            if Sale.objects.filter(receipt_no=receipt_no, owner=effective_owner).exists():
                return "skipped", ""

            timestamp_str = sale_data.get("timestamp")
            timestamp = django_timezone.now()
            if timestamp_str:
                try:
                    timestamp = datetime.fromisoformat(str(timestamp_str).replace("Z", "+00:00"))
                    if django_timezone.is_naive(timestamp):
                        timestamp = django_timezone.make_aware(timestamp)
                except (ValueError, TypeError):
                    raise ValueError("Sale timestamp is invalid.")

            location = (
                Location.objects.filter(id=sale_data.get("location_id"), owner=effective_owner).first()
                or Location.objects.filter(owner=effective_owner).first()
            )
            if not location:
                raise ValueError("No business location is available for this sale.")

            tender = str(sale_data.get("tender") or "cash").strip().lower()
            if tender not in dict(Sale.TENDER_CHOICES):
                raise ValueError("Sale tender is invalid.")
            discount = Decimal(str(sale_data.get("discount", "0.00")))
            if discount < 0:
                raise ValueError("Sale discount cannot be negative.")

            sale_items_data = sale_data.get("items", [])
            if not isinstance(sale_items_data, list) or not sale_items_data:
                raise ValueError("A sale must contain at least one item.")

            sku_values = [str(row.get("item_sku") or "").strip() for row in sale_items_data if isinstance(row, dict)]
            item_map = {
                item.sku: item
                for item in Item.objects.filter(owner=effective_owner, sku__in=sku_values)
            }
            prepared_items = []
            gross_total = Decimal("0.00")
            for item_data in sale_items_data:
                if not isinstance(item_data, dict):
                    raise ValueError("Sale item entry must be an object.")
                item_sku = str(item_data.get("item_sku") or "").strip()
                item = item_map.get(item_sku)
                if not item:
                    raise ValueError(f"Unknown item SKU: {item_sku or 'missing'}")
                unit_price = Decimal(str(item_data.get("unit_price", "0.00")))
                quantity = int(item_data.get("quantity", 1))
                if quantity <= 0:
                    raise ValueError("Sale item quantity must be greater than zero.")
                if unit_price < 0:
                    raise ValueError("Sale item price cannot be negative.")
                gross_total += unit_price * quantity
                prepared_items.append((item, quantity, unit_price))

            if discount > gross_total:
                raise ValueError("Sale discount cannot exceed the gross total.")

            sale = Sale.objects.create(
                receipt_no=receipt_no,
                timestamp=timestamp,
                discount=discount,
                tender=tender,
                location=location,
                owner=effective_owner,
                cashier=cashier,
                sync_source="desktop",
            )

            for item, quantity, unit_price in prepared_items:
                SaleItem.objects.create(
                    sale=sale,
                    item=item,
                    quantity=quantity,
                    unit_price=unit_price,
                    unit_cost=item.cost_price or Decimal("0.00"),
                )

            sale.recalculate_totals()
            sale.mark_synced()
            return "pushed", ""
    except Exception as exc:
        return "failed", _safe_sync_error(exc)

# ==================== API Endpoints ====================

@api_view(['POST'])
@permission_classes([AllowAny]) # Allow unauthenticated access for login
def api_login(request):
    """
    API endpoint for user login. Authenticates user and returns an auth token.
    """
    username = request.data.get('username')
    password = request.data.get('password')
    max_failures = max(1, int(getattr(settings, "API_LOGIN_RATE_LIMIT_ATTEMPTS", 10) or 10))
    window_seconds = max(1, int(getattr(settings, "API_LOGIN_RATE_LIMIT_WINDOW", 300) or 300))
    failure_key = _api_login_failure_key(request, username)
    failure_count = int(cache.get(failure_key, 0) or 0)

    if failure_count >= max_failures:
        return Response(
            {
                "ok": False,
                "error": "Too Many Attempts",
                "message": "Too many failed sign-in attempts. Please wait and try again.",
            },
            status=429,
            headers={"Retry-After": str(window_seconds)},
        )

    user = authenticate(request, username=username, password=password)

    if user:
        cache.delete(failure_key)
        # Get or create profile. Set default values if new.
        profile, p_created = UserProfile.objects.get_or_create(user=user)
        if p_created:
            profile.role = 'admin' if user.is_superuser else 'cashier'
            profile.save()
        
        # RESILIENCE: Validate default_location_id before sending to desktop.
        # If stale (e.g. user profile thinks it's 1, but only 3 & 4 exist), auto-correct it.
        owner = get_user_effective_owner(user)
        if profile.default_location_id and not Location.objects.filter(id=profile.default_location_id, owner=owner).exists():
            first_loc = Location.objects.filter(owner=owner).first()
            profile.default_location = first_loc
            profile.save(update_fields=['default_location'])

        plan_owner = profile.get_effective_plan_owner()
        has_desktop_access = (
            user.is_superuser
            or profile.is_pro_active()
            or profile.is_trial_active()
        )
        if not has_desktop_access:
            access_message = "QuickStock PRO subscription required."
            if plan_owner.status != "active":
                access_message = "Account is suspended. Please renew your subscription."
            elif plan_owner.plan == "TRIAL":
                access_message = "Your trial has expired. Please subscribe to continue."
            elif plan_owner.plan == "PRO":
                access_message = "Your subscription expired. Please renew to continue."

            return Response(
                {
                    "ok": False,
                    "error": "Subscription Required",
                    "message": access_message,
                },
                status=403,
            )

        Token.objects.filter(user=user).delete()
        token = Token.objects.create(user=user)

        desktop_role = "superuser" if user.is_superuser else profile.role

        return Response({
            'ok': True,
            'token': token.key,
            'user': {
                'id': user.id,
                'username': user.username, 
                'role': desktop_role,
                'operator_status': profile.status,
                'tenant_id': owner.id,
                'default_location_id': profile.default_location_id,
                'active_register': _desktop_register_snapshot(user),
                'plan': plan_owner.plan,
                'status': plan_owner.status,
                'plan_end': plan_owner.plan_end.isoformat() if plan_owner.plan_end else None,
                'pro_expires': plan_owner.pro_expires.isoformat() if plan_owner.pro_expires else None,
            }
        })
    
    _record_api_login_failure(failure_key, window_seconds)

    # Return ok: False so the Desktop UI shows the specific error message
    return Response({
        'ok': False, 
        'error': 'Invalid Credentials', 
        'message': 'Invalid username or password.'
    }, status=401)

@api_view(['GET'])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def api_profile(request):
    """
    API endpoint to retrieve user profile information.
    """
    user = request.user
    profile, _ = UserProfile.objects.get_or_create(user=user)
    effective_owner = get_user_effective_owner(user)
    plan_owner = profile.get_effective_plan_owner()

    # Calculate license status
    license_status = "active" if (profile.is_pro_active() or profile.is_trial_active()) else "basic"

    # RESILIENCE: Validate default_location_id. If stale, auto-correct.
    # This ensures consistency with the logic applied during api_login.
    if profile.default_location_id and not Location.objects.filter(id=profile.default_location_id, owner=effective_owner).exists():
        first_loc = Location.objects.filter(owner=effective_owner).first()
        profile.default_location = first_loc
        profile.save(update_fields=['default_location'])
    
    desktop_role = "superuser" if user.is_superuser else profile.role

    return Response({
        'ok': True,
        'user': {
            'id': user.id,
            'username': user.username,
            'email': user.email,
            'role': desktop_role,
            'operator_status': profile.status,
            'tenant_id': effective_owner.id,
            'default_location_id': profile.default_location_id,
            'active_register': _desktop_register_snapshot(user),
            'business_name': effective_owner.username if effective_owner else user.username,
            'license_status': license_status,
            'plan': plan_owner.plan,
            'status': plan_owner.status,
            'plan_end': plan_owner.plan_end.isoformat() if plan_owner.plan_end else None,
            'pro_expiry': plan_owner.pro_expires.isoformat() if plan_owner.pro_expires else None,
        }
    })


@api_view(['GET'])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def api_users(request):
    """Return user registry rows for desktop admin and platform superuser tools."""
    requester_profile = UserProfile.for_user(request.user)

    if request.user.is_superuser:
        profiles = (
            UserProfile.objects
            .select_related("user", "parent_admin", "parent_admin__user", "default_location")
            .exclude(user__is_superuser=True)
            .order_by("parent_admin__user__username", "role", "user__username")
        )
        scope = "platform"
    elif requester_profile.role == "admin":
        owner = get_user_effective_owner(request.user)
        owner_profile = UserProfile.for_user(owner)
        profiles = (
            UserProfile.objects
            .select_related("user", "parent_admin", "parent_admin__user", "default_location")
            .filter(Q(user=owner) | Q(parent_admin=owner_profile))
            .order_by("role", "user__username")
        )
        scope = "company"
    else:
        return Response(
            {"ok": False, "message": "Only admins and platform superusers can view user registry data."},
            status=403,
        )

    users = []
    for profile in profiles:
        users.append({
            "id": profile.user_id,
            "profile_id": profile.id,
            "username": profile.user.username,
            "email": profile.user.email,
            "role": profile.role,
            "status": profile.status,
            "plan": profile.plan,
            "is_active": profile.user.is_active,
            "company": profile.parent_admin.user.username if profile.parent_admin else profile.user.username,
            "default_location": profile.default_location.name if profile.default_location else "",
        })

    return Response({
        "ok": True,
        "users": users,
        "count": len(users),
        "scope": scope,
    })

@api_view(['POST'])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def sync_push_inventory(request):
    """
    Push local inventory changes to the server.
    
    Expected POST data:
    {
        "items": [
            {
                "sku": "ITEM-001",
                "name": "Item Name",
                "price": "100.00",
                "quantity": 10,
                "cost_price": "80.00",
                "category": "Category Name",
                "sync_token": "abc123",
                "last_modified": "2024-01-01T00:00:00+00:00",
                "is_deleted": false
            }
        ],
        "client_id": "desktop-uuid",
        "last_sync_token": "previous-sync-token"
    }
    """
    data = request.data
    items_data = data.get("items")
    if items_data is None and data.get("sku"):
        items_data = [data]
    items_data = items_data or []
    client_id = _sync_client_id(data.get("client_id"))

    if not isinstance(items_data, list):
        return JsonResponse({"success": False, "error": "Items must be a list."}, status=400)
    if not items_data:
        return JsonResponse({
            "success": False,
            "error": "No items provided"
        }, status=400)
    max_items = max(1, int(getattr(settings, "API_SYNC_MAX_INVENTORY_ITEMS", 1000)))
    if len(items_data) > max_items:
        return JsonResponse(
            {"success": False, "error": f"A sync batch may contain at most {max_items} items."},
            status=413,
        )

    # Get effective owner
    effective_owner = get_user_effective_owner(request.user)

    # Create sync log
    sync_log = AuditLog.objects.create(
        user=request.user,
        action="inventory",
        message=f"Sync Push: {len(items_data)} items from {client_id}",
        sync_type="inventory",
        direction="push",
        status="in_progress",
    )
    
    # Safely handle extra fields if they exist in the model
    if hasattr(sync_log, 'source'):
        sync_log.source = "desktop"
    if hasattr(sync_log, 'client_id'):
        sync_log.client_id = client_id
    sync_log.save()

    results = {
        "pushed": 0,
        "updated": 0,
        "failed": 0,
        "conflicts": 0,
        "errors": [],
    }

    with transaction.atomic():
        for item_data in items_data:
            status, err = _process_inventory_item_sync(item_data, effective_owner)
            if status == "failed":
                results["failed"] += 1
                results["errors"].append(f"Item {item_data.get('sku')}: {err}")
            elif status == "conflicts":
                results["conflicts"] += 1
                results["updated"] += 1
            else:
                results[status] = results.get(status, 0) + 1

    # Update sync log
    sync_log.items_pushed = results["pushed"] + results["updated"]
    sync_log.items_failed = results["failed"]
    sync_log.conflicts_detected = results["conflicts"]
    sync_log.conflicts_resolved = results["conflicts"]

    if results["failed"] == 0:
        sync_log.status = "completed"
    elif results["pushed"] + results["updated"] > 0:
        sync_log.status = "partial"
    else:
        sync_log.status = "failed"

    sync_log.completed_at = django_timezone.now()
    if results["errors"]:
        sync_log.error_message = "; ".join(results["errors"][:5])
    sync_log.save()

    return JsonResponse({
        "success": results["failed"] == 0,
        "ok": results["failed"] == 0,
        "message": f"Processed {results['pushed'] + results['updated']} items",
        "results": results,
        "sync_id": str(sync_log.sync_id),
    })

@api_view(['GET'])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def sync_pull_inventory(request):
    last_sync = request.GET.get("last_sync")
    client_id = _sync_client_id(request.GET.get("client_id"))
    
    location_id_str = request.GET.get("location_id")
    location_id = None
    if location_id_str:
        try:
            location_id = int(location_id_str)
        except ValueError:
            # Invalid location_id, treat as if no location_id was provided
            pass

    effective_owner = get_user_effective_owner(request.user)

    # FALLBACK: If location_id is provided but doesn't exist for this owner, 
    # fallback to global inventory view. This prevents empty tables in the GUI.
    location = None
    if location_id:
        location = Location.objects.filter(id=location_id, owner=effective_owner).first()
        if not location:
            location_id = None

    current_sync_time = django_timezone.now()

    sync_log = AuditLog.objects.create(
        user=request.user,
        action="inventory",
        message=f"Sync Pull request from {client_id}",
        sync_type="inventory",
        direction="pull",
        status="in_progress",
    )
    
    if hasattr(sync_log, 'source'):
        sync_log.source = "desktop"
    if hasattr(sync_log, 'client_id'):
        sync_log.client_id = client_id
    sync_log.save()
    
    # --- AUTO-IMPORT LOGIC ---
    # If the database is empty, attempt to seed from the shared JSON file.
    if not Item.objects.filter(owner=effective_owner).exists():
        try:
            items_to_seed = load_seed_products_for_owner(effective_owner)
            if items_to_seed:
                if location is None:
                    location, _ = Location.objects.get_or_create(
                        owner=effective_owner,
                        name="Main Store",
                        defaults={"country_code": "JM"},
                    )
                created_count = 0
                for item_data in items_to_seed:
                    sku = item_data.get("sku") or item_data.get("SKU")
                    if not sku:
                        continue

                    category_name = item_data.get("category") or item_data.get("Category") or "General"
                    brand_name = item_data.get("brand") or item_data.get("Brand") or "Generic"
                    category, _ = Category.objects.get_or_create(owner=effective_owner, name=category_name)
                    brand, _ = Brand.objects.get_or_create(owner=effective_owner, name=brand_name)
                    cost_price = Decimal(str(item_data.get("cost_price") or item_data.get("Cost") or 0))
                    price = Decimal(str(item_data.get("selling_price") or item_data.get("price") or item_data.get("Price") or 0))
                    quantity = int(item_data.get("quantity_on_hand") or item_data.get("quantity") or item_data.get("Amount") or 0)
                    item = Item.objects.create(
                        sku=sku,
                        name=item_data.get("name") or item_data.get("Name") or "Unknown",
                        price=price,
                        cost_price=cost_price,
                        brand=brand,
                        category=category,
                        owner=effective_owner,
                        sync_source="server-import"
                    )
                    StockRecord.objects.update_or_create(
                        item=item,
                        location=location,
                        defaults={"quantity": quantity},
                    )
                    created_count += 1
                logger.info(f"Auto-seeded {created_count} items into database.")
        except Exception as e:
            logger.error(f"Auto-seed error: {e}")

    items_query = Item.objects.filter(owner=effective_owner, is_deleted=False)

    if last_sync:
        try:
            sync_time = datetime.fromisoformat(last_sync.replace("Z", "+00:00"))
            items_query = items_query.filter(last_modified__gt=sync_time)
        except (ValueError, TypeError):
            pass

    items = items_query.select_related('category').prefetch_related("stock_at_locations").order_by("last_modified")
    items_data = [
        item_to_sync_dict(
            item,
            location_id=location_id,
            location_name=location.name if location else None,
        )
        for item in items
    ]

    sync_log.items_pulled = len(items_data)
    sync_log.status = "completed"
    sync_log.completed_at = current_sync_time
    sync_log.save()

    return JsonResponse({
        "success": True,
        "ok": True,
        "items": items_data,
        "count": len(items_data),
        # Crucial: Send back the server time so the client knows where to start next time
        "server_time": current_sync_time.isoformat(),
        "sync_id": str(sync_log.sync_id),
    })

@api_view(['POST'])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def sync_push_sales(request):
    """
    Push local sales/transactions to the server.
    
    Expected POST data:
    {
        "sales": [
            {
                "receipt_no": 1001,
                "timestamp": "2024-01-01T12:00:00+00:00",
                "tender": "cash",
                "subtotal": "100.00",
                "gct_amount": "15.00",
                "total_price": "115.00",
                "discount": "0.00",
                "items": [
                    {
                        "item_sku": "ITEM-001",
                        "quantity": 2,
                        "unit_price": "50.00",
                        "total_price": "100.00"
                    }
                ]
            }
        ],
        "client_id": "desktop-uuid"
    }
    """
    data = request.data
    sales_data = data.get("sales", [])
    client_id = _sync_client_id(data.get("client_id"))

    if not isinstance(sales_data, list):
        return JsonResponse({"success": False, "error": "Sales must be a list."}, status=400)
    if not sales_data:
        return JsonResponse({
            "success": False,
            "error": "No sales provided"
        }, status=400)
    max_sales = max(1, int(getattr(settings, "API_SYNC_MAX_SALES", 500)))
    if len(sales_data) > max_sales:
        return JsonResponse(
            {"success": False, "error": f"A sync batch may contain at most {max_sales} sales."},
            status=413,
        )

    # Get effective owner
    effective_owner = get_user_effective_owner(request.user)

    # Create sync log
    sync_log = AuditLog.objects.create(
        user=request.user,
        action="sale",
        message=f"Sync Push: {len(sales_data)} sales from {client_id}",
        sync_type="sales",
        direction="push",
        status="in_progress",
    )
    
    if hasattr(sync_log, 'source'):
        sync_log.source = "desktop"
    if hasattr(sync_log, 'client_id'):
        sync_log.client_id = client_id
    sync_log.save()

    results = {
        "pushed": 0,
        "skipped": 0,
        "failed": 0,
        "errors": [],
    }
    
    with transaction.atomic():
        for sale_data in sales_data:
            status, err = _process_sale_entry_sync(sale_data, effective_owner, request.user)
            if status == "failed":
                results["failed"] += 1
                results["errors"].append(f"Sale {sale_data.get('receipt_no', 'unknown')}: {err}")
            elif status == "pushed":
                results["pushed"] += 1
            elif status == "skipped":
                results["skipped"] += 1

    # Update sync log
    sync_log.items_pushed = results["pushed"]
    sync_log.items_failed = results["failed"]

    if results["failed"] == 0:
        sync_log.status = "completed"
    elif results["pushed"] > 0:
        sync_log.status = "partial"
    else:
        sync_log.status = "failed"

    sync_log.completed_at = django_timezone.now()
    if results["errors"]:
        sync_log.error_message = "; ".join(results["errors"][:5])
    sync_log.save()

    return JsonResponse({
        "success": results["failed"] == 0,
        "ok": results["failed"] == 0,
        "message": f"Processed {results['pushed']} sales",
        "results": results,
        "sync_id": str(sync_log.sync_id),
    })

@api_view(['GET', 'POST'])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def sync_pull_sales(request):
    """
    Pull sales from the server.
    
    Query parameters:
    - last_sync: ISO timestamp of last sync
    - client_id: Client device identifier
    """
    if request.method == "POST":
        return _record_desktop_sale(request)

    last_sync = request.GET.get("last_sync")
    client_id = _sync_client_id(request.GET.get("client_id"))

    # Get effective owner
    effective_owner = get_user_effective_owner(request.user)

    # Create sync log
    sync_log = AuditLog.objects.create(
        user=request.user,
        action="sale",
        message=f"Sync Pull request from {client_id}",
        sync_type="sales",
        direction="pull",
        status="in_progress",
    )
    
    if hasattr(sync_log, 'source'):
        sync_log.source = "desktop"
    if hasattr(sync_log, 'client_id'):
        sync_log.client_id = client_id
    sync_log.save()

    # Build query
    sales_query = Sale.objects.filter(
        owner=effective_owner,
    ).select_related("location", "cashier").prefetch_related("items__item")

    if last_sync:
        try:
            sync_time = datetime.fromisoformat(last_sync.replace("Z", "+00:00"))
            sales_query = sales_query.filter(last_modified__gt=sync_time)
        except (ValueError, TypeError):
            pass

    # Order by last_modified ASC to ensure the client processes history in order
    sales = list(sales_query.order_by("last_modified")[:100])
    sales_data = [sale_to_sync_dict(sale) for sale in sales]

    # Update sync log
    sync_log.items_pulled = len(sales_data)
    sync_log.status = "completed"
    sync_log.completed_at = django_timezone.now()
    sync_log.save()

    return JsonResponse({
        "success": True,
        "ok": True,
        "sales": sales_data,
        "count": len(sales_data),
        "last_sync": django_timezone.now().isoformat(),
        "sync_id": str(sync_log.sync_id),
    })

@api_view(['POST'])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])

def sync_bidirectional(request):
    """
    Perform bidirectional sync for both inventory and sales.
    Processes pushes atomically, and safely evaluates pulls outside the transaction lock.
    """
    data = request.data

    client_id = _sync_client_id(data.get("client_id"))
    inventory_data = data.get("inventory", [])
    sales_data = data.get("sales", [])
    last_sync = data.get("last_sync")

    if not isinstance(inventory_data, list) or not isinstance(sales_data, list):
        return JsonResponse(
            {"success": False, "error": "Inventory and sales must be lists."},
            status=400,
        )
    max_items = max(1, int(getattr(settings, "API_SYNC_MAX_INVENTORY_ITEMS", 1000)))
    max_sales = max(1, int(getattr(settings, "API_SYNC_MAX_SALES", 500)))
    if len(inventory_data) > max_items or len(sales_data) > max_sales:
        return JsonResponse(
            {"success": False, "error": "The sync batch is too large."},
            status=413,
        )

    effective_owner = get_user_effective_owner(request.user)

    # Initialize Sync Log tracking
    sync_log = AuditLog.objects.create(
        user=request.user,
        action="inventory",
        message=f"Bidirectional Sync: {len(inventory_data)} items, {len(sales_data)} sales from {client_id}",
        sync_type="full",
        direction="bidirectional",
        status="in_progress",
    )
    
    if hasattr(sync_log, 'source'):
        sync_log.source = "desktop"
    if hasattr(sync_log, 'client_id'):
        sync_log.client_id = client_id
    sync_log.save()

    results = {
        "inventory": {"pushed": 0, "pulled": 0, "updated": 0, "failed": 0, "conflicts": 0},
        "sales": {"pushed": 0, "pulled": 0, "skipped": 0, "failed": 0},
        "errors": [],
    }

    # ==================== TRANSACTION BOUNDARY: WRITE LOOPS ONLY ====================
    # ==================== TRANSACTION BOUNDARY: WRITE LOOPS ONLY ====================
    try:
        with transaction.atomic():
            # Step 1: Push inventory changes using the unified helper
            for item_data in inventory_data:
                status, err = _process_inventory_item_sync(item_data, effective_owner)
                
                if status == "failed":
                    results["inventory"]["failed"] += 1
                    results["errors"].append(f"Item {item_data.get('sku', 'unknown')}: {err}")
                
                elif status == "skipped":
                    # If the skip happened because a conflict occurred and the remote side won
                    if err and "Conflict" in err:
                        results["inventory"]["conflicts"] += 1
                    # Otherwise, it was skipped cleanly because tokens matched (already up to date)
                
                elif status == "updated":
                    results["inventory"]["updated"] += 1
                    
                elif status == "pushed":
                    results["inventory"]["pushed"] += 1

            # Step 2: Push sales through the same validated, atomic handler used
            # by the dedicated sales endpoint.
            for sale_data in sales_data:
                status, err = _process_sale_entry_sync(sale_data, effective_owner, request.user)
                if status == "failed":
                    results["sales"]["failed"] += 1
                    receipt_label = sale_data.get("receipt_no", "unknown") if isinstance(sale_data, dict) else "unknown"
                    results["errors"].append(f"Sale {receipt_label}: {err}")
                elif status == "pushed":
                    results["sales"]["pushed"] += 1
                elif status == "skipped":
                    results["sales"]["skipped"] += 1

    except Exception as e:
        # Emergency handling for severe write block exceptions
        logger.exception("Desktop sync write transaction failed")
        sync_log.status = "failed"
        sync_log.error_message = f"Write Transaction Failure: {str(e)}"
        sync_log.completed_at = django_timezone.now()
        sync_log.save()
        return JsonResponse(
            {"success": False, "error": "The sync could not be saved. Please retry."},
            status=500,
        )

    # ==================== READ LOOPS: SAFE OUTSIDE TRANSACTION ====================
    try:
        # Step 3: Pull inventory changes
        inventory_query = Item.objects.filter(owner=effective_owner, is_deleted=False)
        if last_sync:
            try:
                sync_time = datetime.fromisoformat(last_sync.replace("Z", "+00:00"))
                inventory_query = inventory_query.filter(last_modified__gt=sync_time)
            except (ValueError, TypeError):
                pass

        pulled_items = list(
            inventory_query
            .select_related('category')
            .prefetch_related("stock_at_locations")
            .order_by("last_modified")
        )
        results["inventory"]["pulled"] = len(pulled_items)

        # Step 4: Pull sales changes
        sales_query = Sale.objects.filter(owner=effective_owner, is_synced=True)
        if last_sync:
            try:
                sync_time = datetime.fromisoformat(last_sync.replace("Z", "+00:00"))
                sales_query = sales_query.filter(last_modified__gt=sync_time)
            except (ValueError, TypeError):
                pass
        
        # RESILIENCE: Prevent echo effect by excluding what this client just uploaded
        if client_id and client_id != "unknown":
            sales_query = sales_query.exclude(sync_source="desktop")

        # CRITICAL: Order by last_modified (ASC) instead of timestamp descending.
        # This guarantees the client processes the historical timeline in the correct order.
        pulled_sales = list(
            sales_query
            .select_related("location", "cashier")
            .prefetch_related("items__item")
            .order_by("last_modified")[:100]
        )
        results["sales"]["pulled"] = len(pulled_sales)

        # Safely convert models into transfer dictionaries
        response_inventory = [item_to_sync_dict(item) for item in pulled_items]
        response_sales = [sale_to_sync_dict(sale) for sale in pulled_sales]

        # Calculate states and finalize log
        total_failed = results["inventory"]["failed"] + results["sales"]["failed"]
        total_pushed = results["inventory"]["pushed"] + results["inventory"]["updated"] + results["sales"]["pushed"]
        
        if total_failed == 0:
            sync_log.status = "completed"
        elif total_pushed > 0:
            sync_log.status = "partial"
        else:
            sync_log.status = "failed"

        sync_log.items_pushed = total_pushed
        sync_log.items_pulled = results["inventory"]["pulled"] + results["sales"]["pulled"]
        sync_log.completed_at = django_timezone.now()
        
        if results["errors"]:
            sync_log.error_message = "; ".join(results["errors"][:10])
        sync_log.save()

        return JsonResponse({
            "success": total_failed == 0,
            "ok": total_failed == 0,
            "sync_id": str(sync_log.sync_id),
            "server_time": sync_log.completed_at.isoformat(),
            "inventory": {
                "results": results["inventory"],
                "pulled": response_inventory
            },
            "sales": {
                "results": results["sales"],
                "pulled": response_sales
            }
        })

    except Exception as e:
        logger.exception("Desktop sync response compilation failed")
        sync_log.status = "failed"
        sync_log.error_message = f"Read Serialization Failure: {str(e)}"
        sync_log.completed_at = django_timezone.now()
        sync_log.save()
        return JsonResponse(
            {"success": False, "error": "The sync response could not be prepared. Please retry."},
            status=500,
        )
    
@api_view(['GET'])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def sync_status(request):
    """
    Get sync status and history for the current user.
    """
    effective_owner = get_user_effective_owner(request.user)

    # Get last sync logs
    last_inventory_sync = AuditLog.objects.filter(
        user=request.user,
        sync_type__in=["inventory", "full"],
        status="completed",
    ).order_by("-completed_at").first()

    last_sales_sync = AuditLog.objects.filter(
        user=request.user,
        sync_type__in=["sales", "full"],
        status="completed",
    ).order_by("-completed_at").first()

    # Get pending sync count
    pending_count = AuditLog.objects.filter(
        user=request.user,
        status="pending",
    ).count()

    # Get items needing sync
    items_needing_sync = Item.objects.filter(
        owner=effective_owner,
        sync_token__isnull=True,
    ).count()

    # Get unsynced sales
    unsynced_sales = Sale.objects.filter(
        owner=effective_owner,
        is_synced=False,
    ).count()

    # Recent sync history
    recent_syncs = AuditLog.objects.filter(
        user=request.user,
    ).order_by("-created_at")[:10].values(
        "sync_id", "sync_type", "direction", "status",
        "created_at", "completed_at", "items_pushed", "items_pulled"
    )

    return JsonResponse({
        "success": True,
        "status": {
            "last_inventory_sync": last_inventory_sync.completed_at.isoformat() if last_inventory_sync else None,
            "last_sales_sync": last_sales_sync.completed_at.isoformat() if last_sales_sync else None,
            "pending_syncs": pending_count,
            "items_needing_sync": items_needing_sync,
            "unsynced_sales": unsynced_sales,
        },
        "recent_syncs": list(recent_syncs),
    })



def health_check(request):
    from inventory.health import get_runtime_health
    health = get_runtime_health()
    status_code = 503 if health["status"] == "degraded" else 200
    return JsonResponse(health, status=status_code)


@api_view(['GET'])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def sync_reference_data(request):
    """
    Get reference data (categories, locations, suppliers) for the user.
    """
    effective_owner = get_user_effective_owner(request.user)

    active_cat_ids = Item.objects.filter(owner=effective_owner, category__isnull=False).values_list('category_id', flat=True).distinct()
    categories = list(Category.objects.filter(id__in=active_cat_ids).values("id", "name").order_by("name"))

    locations = list(Location.objects.filter(
        owner=effective_owner
    ).values("id", "name", "address", "is_warehouse", "country_code").order_by("name"))

    suppliers = list(Supplier.objects.filter(
        owner=effective_owner
    ).values("id", "name", "contact_name", "phone", "email").order_by("name"))

    customers = list(Customer.objects.filter(
        owner=effective_owner
    ).values("id", "owner_id", "name", "phone", "email", "credit_balance").order_by("name"))

    return JsonResponse({
        "success": True,
        "ok": True,
        "categories": categories,
        "locations": locations,
        "suppliers": suppliers,
        "customers": customers,
        "active_register": _desktop_register_snapshot(request.user),
        "timestamp": django_timezone.now().isoformat(),
    })

from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from . import _get_tax_rate_for_location
from .models import Sale, SaleItem, StockRecord, CashShift, UserProfile


class SaleWorkflowError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


def finalize_sale(
    *,
    owner,
    cashier,
    location,
    tender,
    discount=Decimal("0.00"),
    line_items,
    shift=None,
    timestamp=None,
    sync_token=None,
    sync_source="web",
):
    """
    Persist a sale from already-validated, locked stock rows.

    line_items is an iterable of `(item, stock_record, quantity, unit_price)` or
    `(item, stock_record, quantity, unit_price, tax_rate_percent)`.
    """
    if shift and shift.location_id != location.id:
        raise SaleWorkflowError("Register shift does not match the selected sale location.", status=403)

    with transaction.atomic():
        if shift:
            shift = CashShift.objects.select_for_update().select_related("location").get(pk=shift.pk)
            if shift.is_closed:
                raise SaleWorkflowError("Sales cannot be posted to a closed shift.", status=409)
            if shift.cashier_id != cashier.id:
                raise SaleWorkflowError("A sale must be posted by the shift operator.", status=403)
            if shift.location_id != location.id:
                raise SaleWorkflowError("Register shift does not match the selected sale location.", status=403)
            if shift.owner_id and shift.owner_id != owner.id:
                raise SaleWorkflowError("Register shift belongs to another tenant.", status=403)
            if getattr(shift.location, "is_archived", False):
                raise SaleWorkflowError("Archived locations cannot accept sales.", status=403)
        last_sale = (
            Sale.objects.select_for_update()
            .filter(owner=owner)
            .order_by("-receipt_no")
            .first()
        )
        next_receipt_no = 1
        if last_sale and last_sale.receipt_no:
            next_receipt_no = last_sale.receipt_no + 1

        cashier_profile = getattr(cashier, "profile", None) or UserProfile.for_user(cashier)

        sale = Sale.objects.create(
            owner=cashier_profile.effective_owner,
            cashier=cashier,
            shift=shift,
            location=location,
            discount=discount,
            subtotal=Decimal("0.00"),
            gct_amount=Decimal("0.00"),
            total_price=Decimal("0.00"),
            tender=tender,
            receipt_no=next_receipt_no,
            timestamp=timestamp or timezone.now(),
            sync_token=sync_token,
            sync_source=sync_source,
        )

        for line_item in line_items:
            if len(line_item) == 5:
                item, stock_record, quantity, unit_price, tax_rate_percent = line_item
            else:
                item, stock_record, quantity, unit_price = line_item
                tax_rate_percent = (
                    _get_tax_rate_for_location(location) * Decimal("100")
                ).quantize(Decimal("0.01"))
            # type/decimal safety
            qty = int(quantity)
            price = Decimal(str(unit_price))

            if qty <= 0:
                raise SaleWorkflowError(f"Invalid quantity for {item.name}.")
            if price <= 0:
                raise SaleWorkflowError(f"{item.name} does not have a valid selling price.", status=400)
            if stock_record.quantity < qty:
                raise SaleWorkflowError(f"Insufficient stock for {item.name} at {location.name}.", status=400)
            if item.total_global_quantity < qty:
                raise SaleWorkflowError(f"Global stock is inconsistent for {item.name}.", status=400)

            SaleItem.objects.create(
                sale=sale,
                item=item,
                quantity=qty,
                unit_price=price,
                unit_cost=item.cost_price,
                tax_rate_applied=Decimal(str(tax_rate_percent)),
                total_price=price * qty,
            )

        sale.recalculate_totals(save=True)

        if shift:
            shift.calculate_expected_balance(force=True)
            shift.save(
                update_fields=["total_sales", "expected_cash", "last_calculated_at"],
                _allow_integrity_update=True,
            )

    return sale

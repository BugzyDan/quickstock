import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any


CHECKOUT_CONTRACT_VERSION = 1
CHECKOUT_QUEUE_SCHEMA_VERSION = 1
MONEY_QUANTUM = Decimal("0.01")
RATE_QUANTUM = Decimal("0.0001")
REFERENCE_PATTERN = re.compile(r"[A-Za-z0-9._:-]+")


class CheckoutContractError(ValueError):
    def __init__(self, code: str, field: str, message: str, *, status: int = 400):
        super().__init__(message)
        self.code = code
        self.field = field
        self.message = message
        self.status = status

    def as_payload(self) -> dict[str, Any]:
        return {
            "ok": False,
            "success": False,
            "code": self.code,
            "field": self.field,
            "message": self.message,
            "error": self.message,
        }


@dataclass(frozen=True)
class CheckoutLine:
    product_id: int
    sku: str
    quantity: int
    unit_price: Decimal
    unit_cost: Decimal
    is_taxable: bool
    tax_rate: Decimal
    status: str
    is_deleted: bool


@dataclass(frozen=True)
class CheckoutContract:
    reference: str
    occurred_at: datetime
    operator_id: int
    operator_role: str
    tenant_id: int
    location_id: int
    register_id: int
    register_location_id: int
    register_opened_at: datetime
    customer_id: int | None
    customer_name: str
    currency: str
    payment_method: str
    amount_tendered: Decimal
    account_credit: Decimal
    change_due: Decimal
    credited_overpayment: Decimal
    subtotal: Decimal
    discount: Decimal
    tax_amount: Decimal
    total: Decimal
    tax_label: str
    tax_rate: Decimal
    tax_inclusive: bool
    line_items: tuple[CheckoutLine, ...]
    source: str
    queue_schema_version: int


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CheckoutContractError("INVALID_PAYLOAD", field, f"{field} must be an object.")
    return value


def _integer(value: Any, field: str, *, optional: bool = False) -> int | None:
    if optional and value in (None, ""):
        return None
    if isinstance(value, bool):
        raise CheckoutContractError("INVALID_INTEGER", field, f"{field} must be an integer.")
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise CheckoutContractError("INVALID_INTEGER", field, f"{field} must be an integer.")
    if str(value).strip() != str(parsed) and not isinstance(value, int):
        raise CheckoutContractError("INVALID_INTEGER", field, f"{field} must be an integer.")
    if parsed <= 0:
        raise CheckoutContractError("INVALID_INTEGER", field, f"{field} must be greater than zero.")
    return parsed


def _decimal(value: Any, field: str, *, places: int, minimum: Decimal = Decimal("0.00")) -> Decimal:
    if isinstance(value, bool) or value in (None, ""):
        raise CheckoutContractError("INVALID_DECIMAL", field, f"{field} is required.")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise CheckoutContractError("INVALID_DECIMAL", field, f"{field} must be a valid decimal.")
    if not parsed.is_finite():
        raise CheckoutContractError("INVALID_DECIMAL", field, f"{field} must be finite.")
    decimal_places = max(0, -parsed.as_tuple().exponent)
    if decimal_places > places:
        raise CheckoutContractError(
            "INVALID_PRECISION",
            field,
            f"{field} may contain at most {places} decimal places.",
        )
    if parsed < minimum:
        raise CheckoutContractError("INVALID_DECIMAL", field, f"{field} cannot be less than {minimum}.")
    quantum = MONEY_QUANTUM if places == 2 else RATE_QUANTUM
    return parsed.quantize(quantum)


def _timestamp(value: Any, field: str) -> datetime:
    if not value:
        raise CheckoutContractError("INVALID_TIMESTAMP", field, f"{field} is required.")
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        raise CheckoutContractError("INVALID_TIMESTAMP", field, f"{field} must be an ISO-8601 timestamp.")
    if parsed.tzinfo is None:
        raise CheckoutContractError("INVALID_TIMESTAMP", field, f"{field} must include a timezone.")
    return parsed.astimezone(timezone.utc)


def _boolean(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise CheckoutContractError("INVALID_BOOLEAN", field, f"{field} must be true or false.")
    return value


def parse_checkout_contract(payload: Any) -> CheckoutContract:
    root = _mapping(payload, "checkout")
    if root.get("contract_version") != CHECKOUT_CONTRACT_VERSION:
        raise CheckoutContractError(
            "UNSUPPORTED_CONTRACT_VERSION",
            "contract_version",
            f"Checkout contract version {CHECKOUT_CONTRACT_VERSION} is required.",
        )

    reference = str(root.get("offline_client_ref") or "").strip()
    if not reference:
        raise CheckoutContractError(
            "MISSING_CLIENT_REFERENCE",
            "offline_client_ref",
            "offline_client_ref is required for desktop sales.",
        )
    if len(reference) > 64 or not REFERENCE_PATTERN.fullmatch(reference):
        raise CheckoutContractError("INVALID_CLIENT_REFERENCE", "offline_client_ref", "Sale reference is invalid.")

    operator = _mapping(root.get("operator"), "operator")
    tenant = _mapping(root.get("tenant"), "tenant")
    location = _mapping(root.get("location"), "location")
    register = _mapping(root.get("register"), "register")
    customer = _mapping(root.get("customer", {}), "customer")
    payment = _mapping(root.get("payment"), "payment")
    totals = _mapping(root.get("totals"), "totals")
    tax = _mapping(root.get("tax"), "tax")
    metadata = _mapping(root.get("metadata"), "metadata")

    currency = str(root.get("currency") or "").strip().upper()
    if currency != "JMD":
        raise CheckoutContractError("INVALID_CURRENCY", "currency", "Desktop checkout currency must be JMD.")

    payment_method = str(payment.get("method") or "").strip().lower()
    if not payment_method:
        raise CheckoutContractError("MISSING_PAYMENT", "payment.method", "Payment method is required.")

    raw_lines = root.get("line_items")
    if not isinstance(raw_lines, list):
        raise CheckoutContractError("INVALID_CART", "line_items", "line_items must be a list.")
    if not raw_lines:
        raise CheckoutContractError("EMPTY_CART", "line_items", "Cart is empty.")

    lines = []
    seen_product_ids = set()
    seen_skus = set()
    for index, raw_line in enumerate(raw_lines):
        field = f"line_items.{index}"
        line = _mapping(raw_line, field)
        product_id = _integer(line.get("product_id"), f"{field}.product_id")
        sku = str(line.get("sku") or "").strip().upper()
        if not sku:
            raise CheckoutContractError("MISSING_SKU", f"{field}.sku", "Every cart line requires a SKU.")
        if product_id in seen_product_ids or sku in seen_skus:
            raise CheckoutContractError("DUPLICATE_ITEM", field, "Duplicate items are not allowed in one sale.")
        seen_product_ids.add(product_id)
        seen_skus.add(sku)

        quantity = _integer(line.get("quantity"), f"{field}.quantity")
        unit_price = _decimal(line.get("unit_price"), f"{field}.unit_price", places=2)
        if unit_price <= Decimal("0.00"):
            raise CheckoutContractError("INVALID_PRICE", f"{field}.unit_price", "Unit price must be greater than zero.")
        unit_cost = _decimal(line.get("unit_cost"), f"{field}.unit_cost", places=2)
        line_tax_rate = _decimal(line.get("tax_rate"), f"{field}.tax_rate", places=4)
        if line_tax_rate > Decimal("1.0000"):
            raise CheckoutContractError("INVALID_TAX", f"{field}.tax_rate", "Tax rate cannot exceed 1.0000.")

        lines.append(
            CheckoutLine(
                product_id=product_id,
                sku=sku,
                quantity=quantity,
                unit_price=unit_price,
                unit_cost=unit_cost,
                is_taxable=_boolean(line.get("is_taxable"), f"{field}.is_taxable"),
                tax_rate=line_tax_rate,
                status=str(line.get("status") or "").strip().lower(),
                is_deleted=_boolean(line.get("is_deleted"), f"{field}.is_deleted"),
            )
        )

    tax_rate = _decimal(tax.get("rate"), "tax.rate", places=4)
    if tax_rate > Decimal("1.0000"):
        raise CheckoutContractError("INVALID_TAX", "tax.rate", "Tax rate cannot exceed 1.0000.")
    tax_label = str(tax.get("label") or "").strip().upper()
    if not tax_label:
        raise CheckoutContractError("INVALID_TAX", "tax.label", "Tax label is required.")

    queue_schema_version = metadata.get("queue_schema_version")
    if queue_schema_version != CHECKOUT_QUEUE_SCHEMA_VERSION:
        raise CheckoutContractError(
            "INVALID_QUEUE_SCHEMA",
            "metadata.queue_schema_version",
            f"Queue schema version {CHECKOUT_QUEUE_SCHEMA_VERSION} is required.",
        )
    source = str(metadata.get("source") or "").strip().lower()
    if source != "desktop":
        raise CheckoutContractError("INVALID_SOURCE", "metadata.source", "Checkout source must be desktop.")

    return CheckoutContract(
        reference=reference,
        occurred_at=_timestamp(root.get("occurred_at"), "occurred_at"),
        operator_id=_integer(operator.get("id"), "operator.id"),
        operator_role=str(operator.get("role") or "").strip().lower(),
        tenant_id=_integer(tenant.get("id"), "tenant.id"),
        location_id=_integer(location.get("id"), "location.id"),
        register_id=_integer(register.get("id"), "register.id"),
        register_location_id=_integer(register.get("location_id"), "register.location_id"),
        register_opened_at=_timestamp(register.get("opened_at"), "register.opened_at"),
        customer_id=_integer(customer.get("id"), "customer.id", optional=True),
        customer_name=" ".join(str(customer.get("name") or "").split())[:255],
        currency=currency,
        payment_method=payment_method,
        amount_tendered=_decimal(payment.get("amount_tendered"), "payment.amount_tendered", places=2),
        account_credit=_decimal(payment.get("account_credit"), "payment.account_credit", places=2),
        change_due=_decimal(payment.get("change_due"), "payment.change_due", places=2),
        credited_overpayment=_decimal(
            payment.get("credited_overpayment"),
            "payment.credited_overpayment",
            places=2,
        ),
        subtotal=_decimal(totals.get("subtotal"), "totals.subtotal", places=2),
        discount=_decimal(totals.get("discount"), "totals.discount", places=2),
        tax_amount=_decimal(totals.get("tax"), "totals.tax", places=2),
        total=_decimal(totals.get("total"), "totals.total", places=2),
        tax_label=tax_label,
        tax_rate=tax_rate,
        tax_inclusive=_boolean(tax.get("inclusive"), "tax.inclusive"),
        line_items=tuple(lines),
        source=source,
        queue_schema_version=queue_schema_version,
    )

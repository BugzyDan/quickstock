from decimal import Decimal

import requests
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.conf import settings
from django.utils import timezone

from .models import (
    AccountingIntegration,
    AccountingSyncRecord,
    Item,
    Sale,
    SalesInvoice,
    SupplierInvoice,
)


class AccountingSyncError(Exception):
    pass


class AccountingProviderNotConfigured(AccountingSyncError):
    pass


def decimal_as_string(value):
    return str(Decimal(value or "0.00").quantize(Decimal("0.01")))


def get_or_create_accounting_integration(owner, provider=AccountingIntegration.PROVIDER_XERO):
    integration, _created = AccountingIntegration.objects.get_or_create(
        owner=owner,
        provider=provider,
        defaults={
            "display_name": dict(AccountingIntegration.PROVIDER_CHOICES).get(provider, provider.title()),
            "status": AccountingIntegration.STATUS_DISCONNECTED,
        },
    )
    return integration


def _contact_from_customer(customer, fallback_name="", fallback_email=""):
    if customer:
        return {
            "name": customer.name,
            "email": customer.email or "",
            "phone": customer.phone or "",
            "tax_number": customer.trn or "",
        }
    return {
        "name": fallback_name or "Walk-in Customer",
        "email": fallback_email or "",
        "phone": "",
        "tax_number": "",
    }


def build_xero_sales_invoice_payload(invoice):
    return {
        "type": "ACCREC",
        "source": "quickstock.sales_invoice",
        "source_id": invoice.pk,
        "invoice_number": invoice.invoice_no,
        "status": invoice.status,
        "issued_at": invoice.issued_at.isoformat() if invoice.issued_at else "",
        "due_date": invoice.due_date.isoformat() if invoice.due_date else "",
        "contact": _contact_from_customer(invoice.customer),
        "currency": "JMD",
        "subtotal": decimal_as_string(invoice.subtotal),
        "tax_amount": decimal_as_string(invoice.tax_amount),
        "total_amount": decimal_as_string(invoice.total_amount),
        "line_items": [
            {
                "sku": line.item.sku or "",
                "item_code": line.item.sku or line.item.barcode or str(line.item_id),
                "description": line.item_name or line.item.name,
                "quantity": line.quantity,
                "unit_amount": decimal_as_string(line.unit_price),
                "line_amount": decimal_as_string(line.line_total),
                "tax_type": "OUTPUT" if line.item.is_taxable else "NONE",
            }
            for line in invoice.items.select_related("item").all()
        ],
    }


def build_xero_sale_payload(sale):
    receipt_number = sale.receipt_no or sale.pk
    return {
        "type": "ACCREC",
        "source": "quickstock.sale",
        "source_id": sale.pk,
        "invoice_number": f"QS-REC-{receipt_number}",
        "status": sale.receipt_status,
        "issued_at": sale.timestamp.isoformat() if sale.timestamp else "",
        "contact": _contact_from_customer(
            None,
            fallback_name=sale.customer_name,
            fallback_email=sale.customer_email,
        ),
        "currency": "JMD",
        "payment_method": sale.tender,
        "payment_reference": sale.payment_reference,
        "subtotal": decimal_as_string(sale.subtotal),
        "tax_amount": decimal_as_string(sale.gct_amount),
        "total_amount": decimal_as_string(sale.total_price),
        "line_items": [
            {
                "sku": line.item.sku or "",
                "item_code": line.item.sku or line.item.barcode or str(line.item_id),
                "description": line.item.name,
                "quantity": line.quantity,
                "unit_amount": decimal_as_string(line.unit_price),
                "line_amount": decimal_as_string(line.total_price),
                "tax_amount": decimal_as_string(line.tax_amount),
                "tax_type": "OUTPUT" if line.item.is_taxable else "NONE",
            }
            for line in sale.items.select_related("item").all()
        ],
    }


def build_xero_supplier_invoice_payload(invoice):
    return {
        "type": "ACCPAY",
        "source": "quickstock.supplier_invoice",
        "source_id": invoice.pk,
        "invoice_number": invoice.invoice_no,
        "status": invoice.status,
        "issued_at": invoice.date_issued.isoformat() if invoice.date_issued else "",
        "due_date": invoice.due_date.isoformat() if invoice.due_date else "",
        "contact": {
            "name": invoice.supplier.name,
            "email": invoice.supplier.email or "",
            "phone": invoice.supplier.phone or "",
        },
        "currency": "JMD",
        "total_amount": decimal_as_string(invoice.amount),
        "amount_paid": decimal_as_string(invoice.paid_amount),
    }


def build_xero_item_payload(item):
    return {
        "source": "quickstock.item",
        "source_id": item.pk,
        "code": item.sku or item.barcode or str(item.pk),
        "name": item.name,
        "description": item.name,
        "sales_unit_price": decimal_as_string(item.price),
        "purchase_unit_price": decimal_as_string(item.cost_price),
        "is_taxable": item.is_taxable,
        "quantity_on_hand": item.total_global_quantity,
    }


def build_accounting_payload(obj, provider=AccountingIntegration.PROVIDER_XERO):
    if provider != AccountingIntegration.PROVIDER_XERO:
        raise AccountingSyncError(f"Provider '{provider}' does not have a payload mapper yet.")
    if isinstance(obj, SalesInvoice):
        return build_xero_sales_invoice_payload(obj)
    if isinstance(obj, Sale):
        return build_xero_sale_payload(obj)
    if isinstance(obj, SupplierInvoice):
        return build_xero_supplier_invoice_payload(obj)
    if isinstance(obj, Item):
        return build_xero_item_payload(obj)
    raise AccountingSyncError(f"{obj.__class__.__name__} is not supported for accounting sync.")


def owner_for_accounting_object(obj):
    if isinstance(obj, SalesInvoice):
        return obj.owner
    if isinstance(obj, Sale):
        return obj.owner or getattr(obj.location, "owner", None)
    if isinstance(obj, SupplierInvoice):
        return obj.supplier.owner
    if isinstance(obj, Item):
        return obj.owner
    return None


def queue_accounting_sync(obj, provider=AccountingIntegration.PROVIDER_XERO, operation=None):
    owner = owner_for_accounting_object(obj)
    if owner is None:
        raise AccountingSyncError("Cannot queue accounting sync without an owning workspace.")

    integration = get_or_create_accounting_integration(owner, provider=provider)
    payload = build_accounting_payload(obj, provider=provider)
    content_type = ContentType.objects.get_for_model(obj, for_concrete_model=False)
    operation = operation or AccountingSyncRecord.OPERATION_CREATE

    with transaction.atomic():
        record, _created = AccountingSyncRecord.objects.update_or_create(
            integration=integration,
            content_type=content_type,
            object_id=obj.pk,
            defaults={
                "owner": owner,
                "operation": operation,
                "status": AccountingSyncRecord.STATUS_PENDING,
                "payload": payload,
                "error_message": "",
                "next_attempt_at": timezone.now(),
            },
        )
    return record


def queue_existing_accounting_data(owner, provider=AccountingIntegration.PROVIDER_XERO):
    integration = get_or_create_accounting_integration(owner, provider=provider)
    queued = []
    if integration.sync_sales_invoices:
        for invoice in SalesInvoice.objects.filter(owner=owner).exclude(status="draft").iterator():
            queued.append(queue_accounting_sync(invoice, provider=provider))
    if integration.sync_sales:
        for sale in Sale.objects.filter(owner=owner).exclude(receipt_status="draft").iterator():
            queued.append(queue_accounting_sync(sale, provider=provider))
    if integration.sync_purchase_invoices:
        for invoice in SupplierInvoice.objects.filter(supplier__owner=owner).iterator():
            queued.append(queue_accounting_sync(invoice, provider=provider))
    if integration.sync_inventory_items:
        for item in Item.objects.filter(owner=owner, is_deleted=False).iterator():
            queued.append(queue_accounting_sync(item, provider=provider))
    return queued


class XeroAccountingClient:
    api_base_url = "https://api.xero.com/api.xro/2.0"

    def __init__(self, integration):
        self.integration = integration
        payload = integration.settings_payload or {}
        self.access_token = payload.get("access_token") or ""
        self.tenant_id = integration.external_tenant_id or payload.get("tenant_id") or ""

    def _headers(self):
        if not self.access_token or not self.tenant_id:
            raise AccountingProviderNotConfigured("Xero OAuth tokens are not configured for this workspace.")
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Xero-tenant-id": self.tenant_id,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _post(self, endpoint, body):
        response = requests.post(
            f"{self.api_base_url}/{endpoint}",
            headers=self._headers(),
            json=body,
            timeout=30,
        )
        try:
            response_body = response.json()
        except ValueError:
            response_body = {"text": response.text}
        if response.status_code >= 400:
            raise AccountingSyncError(response_body)
        return response_body

    def sync_record(self, record):
        payload = record.payload or {}
        source = payload.get("source")
        if source == "quickstock.item":
            response = self._post("Items", {"Items": [self._item_body(payload)]})
            external_id = ((response.get("Items") or [{}])[0].get("ItemID") or "")
            return external_id, response

        response = self._post("Invoices", {"Invoices": [self._invoice_body(payload)]})
        external_id = ((response.get("Invoices") or [{}])[0].get("InvoiceID") or "")
        return external_id, response

    def _invoice_body(self, payload):
        return {
            "Type": payload.get("type") or "ACCREC",
            "Contact": {"Name": (payload.get("contact") or {}).get("name") or "QuickStock Contact"},
            "InvoiceNumber": payload.get("invoice_number") or f"QS-{payload.get('source_id')}",
            "Date": (payload.get("issued_at") or "")[:10],
            "DueDate": (payload.get("due_date") or payload.get("issued_at") or "")[:10],
            "Reference": payload.get("payment_reference") or payload.get("source") or "QuickStock",
            "LineAmountTypes": "Inclusive",
            "LineItems": [
                {
                    "ItemCode": line.get("item_code") or line.get("sku") or "",
                    "Description": line.get("description") or "QuickStock item",
                    "Quantity": line.get("quantity") or 1,
                    "UnitAmount": line.get("unit_amount") or line.get("line_amount") or "0.00",
                    "TaxType": line.get("tax_type") or "NONE",
                }
                for line in payload.get("line_items", [])
            ] or [
                {
                    "Description": payload.get("source") or "QuickStock transaction",
                    "Quantity": 1,
                    "UnitAmount": payload.get("total_amount") or "0.00",
                    "TaxType": "NONE",
                }
            ],
        }

    def _item_body(self, payload):
        return {
            "Code": payload.get("code") or f"QS-{payload.get('source_id')}",
            "Name": payload.get("name") or "QuickStock item",
            "Description": payload.get("description") or payload.get("name") or "",
            "SalesDetails": {
                "UnitPrice": payload.get("sales_unit_price") or "0.00",
                "TaxType": "OUTPUT" if payload.get("is_taxable") else "NONE",
            },
            "PurchaseDetails": {
                "UnitPrice": payload.get("purchase_unit_price") or "0.00",
            },
        }


def accounting_client_for_integration(integration):
    if integration.provider == AccountingIntegration.PROVIDER_XERO:
        return XeroAccountingClient(integration)
    raise AccountingSyncError(f"No accounting client is registered for provider '{integration.provider}'.")


def process_pending_accounting_sync(integration=None, limit=None):
    limit = limit or getattr(settings, "ACCOUNTING_SYNC_BATCH_SIZE", 50)
    records = AccountingSyncRecord.objects.filter(status=AccountingSyncRecord.STATUS_PENDING)
    if integration is not None:
        records = records.filter(integration=integration)
    records = records.select_related("integration").order_by("created_at")[:limit]

    processed = {"synced": 0, "failed": 0}
    for record in records:
        record.status = AccountingSyncRecord.STATUS_IN_PROGRESS
        record.attempt_count += 1
        record.save(update_fields=["status", "attempt_count", "updated_at"])
        try:
            client = accounting_client_for_integration(record.integration)
            external_id, response_payload = client.sync_record(record)
            record.mark_synced(external_id=external_id, response_payload=response_payload)
            processed["synced"] += 1
        except Exception as exc:
            record.mark_failed(str(exc))
            processed["failed"] += 1
    return processed

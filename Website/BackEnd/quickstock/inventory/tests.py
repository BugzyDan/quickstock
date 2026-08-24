import hashlib
import importlib
import json
import os
from pathlib import Path
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from urllib.parse import parse_qs, urlencode, urlparse
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core import mail
from django.core.management import call_command
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, connection, transaction
from django.db.models.deletion import ProtectedError
from django.db.models.signals import post_save
from django.http import HttpResponse
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from .models import (
    AccountingIntegration,
    AccountingSyncRecord,
    AuditLog,
    Brand,
    CashShift,
    Category,
    Customer,
    CustomerCreditMovement,
    CustomerNote,
    DailyCashCount,
    Item,
    Location,
    Payment,
    PurchaseOrder,
    ReceiptEmailLog,
    ReceiptRevision,
    Sale,
    SaleItem,
    SalesInvoice,
    SalesInvoiceCreditNote,
    SalesInvoiceItem,
    SalesInvoicePayment,
    SalesInvoicePaymentReversal,
    SalesQuotation,
    SalesQuotationItem,
    StockRecord,
    StockTransfer,
    Supplier,
    SupplierInvoice,
    SupplierInvoiceAdjustment,
    SupplierInvoiceRefund,
    SupplierInvoicePayment,
    SupplierInvoicePaymentReversal,
    UserProfile,
    UserVerification,
)
from .accounting import (
    build_xero_sales_invoice_payload,
    process_pending_accounting_sync,
    queue_accounting_sync,
    queue_existing_accounting_data,
)
from .email_utils import CONSOLE_BACKEND, RESEND_BACKEND, email_delivery_status
from .signals import create_user_verification, send_welcome_email
from .sales import SaleWorkflowError, finalize_sale
from .views import (
    DOCUMENT_META_PREFIX,
    _compose_document_notes,
    _customer_queryset_for_user,
    _daily_reconciliation_context,
    _sales_document_email_context,
    _send_login_otp,
    _stock_queryset_for_user,
)


class LoadingStateAssetTests(SimpleTestCase):
    def test_loading_state_assets_define_busy_ui_contract(self):
        app_dir = Path(__file__).resolve().parent
        js = (app_dir / "static" / "inventory" / "js" / "loading_states.js").read_text()
        css = (app_dir / "static" / "inventory" / "css" / "style.css").read_text()

        self.assertIn("window.QuickStockLoading", js)
        self.assertIn('setAttribute("aria-busy", "true")', js)
        self.assertIn(".nav-search-trigger", js)
        self.assertIn(".nav-search-link", js)
        self.assertIn("prefers-reduced-motion: reduce", css)
        self.assertIn(".qs-button-spinner", css)

    def test_core_templates_include_loading_state_script(self):
        templates_dir = Path(__file__).resolve().parent / "templates" / "inventory"
        template_paths = [
            templates_dir / "partials" / "app_shell_header.html",
            templates_dir / "partials" / "global_app_shell_header.html",
            templates_dir / "app_shell_header.html",
            templates_dir / "cash_register.html",
        ]

        for path in template_paths:
            with self.subTest(template=path.name):
                self.assertIn("inventory/js/loading_states.js", path.read_text())

    def test_advanced_reports_valuation_card_survives_global_card_polish(self):
        css = (Path(__file__).resolve().parent / "static" / "inventory" / "css" / "style.css").read_text()

        self.assertIn("body.page-advanced-reports .report-card.analytics-valuation-card", css)
        self.assertIn("linear-gradient(145deg, #0f172a", css)
        self.assertIn("color: #f8fafc !important", css)
        self.assertIn(".analytics-valuation-row-emphasis strong", css)

    def test_cash_register_full_refinement_survives_late_ui_layers(self):
        app_dir = Path(__file__).resolve().parent
        css = (app_dir / "static" / "inventory" / "css" / "style.css").read_text()
        template = (app_dir / "templates" / "inventory" / "cash_register.html").read_text()

        self.assertIn("CASH REGISTER DESKTOP REPAIR", css)
        self.assertIn("CASH REGISTER FULL TERMINAL REFINEMENT", css)
        self.assertIn("--qs-cash-sidebar-width", css)
        self.assertIn("--qs-cash-panel-radius", css)
        self.assertIn("--qs-cash-action-blue", css)
        self.assertIn("--qs-cash-rail-min", css)
        self.assertIn("grid-template-columns: minmax(0, 1fr) var(--qs-cash-sidebar-width) !important", css)
        self.assertIn("grid-template-columns: minmax(0, 1fr) minmax(340px, var(--qs-cash-sidebar-width)) !important", css)
        self.assertIn("grid-template-columns: minmax(0, 1fr) minmax(var(--qs-cash-rail-min), var(--qs-cash-sidebar-width)) !important", css)
        self.assertIn("body.page-cash-register .product-area", css)
        self.assertIn("overflow: auto !important", css)
        self.assertIn("body.page-cash-register .checkout-sidebar", css)
        self.assertIn("position: sticky !important", css)
        self.assertIn("body.page-cash-register #cart-list:empty::before", css)
        self.assertIn('content: "Cart is empty"', css)
        self.assertIn("body.page-cash-register #cart-list:not(:empty)", css)
        self.assertIn("body.page-cash-register .cash-mobile-brand img", css)
        self.assertIn("width: 42px !important", css)
        self.assertIn("min-width: 42px !important", css)
        self.assertIn("max-width: 42px !important", css)
        self.assertIn("flex: 0 0 42px !important", css)
        self.assertIn("body.page-cash-register .cash-mobile-brand,\n    body.page-cash-register .cash-mobile-actions", css)
        self.assertIn("body.page-cash-register .cash-mobile-actions", css)
        self.assertIn("display: none !important", css)
        self.assertIn("body.page-cash-register .cash-sidebar-search-entry {\n        display: none !important;", css)
        self.assertIn("body.page-cash-register .product-area {\n        display: block !important;", css)
        self.assertIn("Evaluator fix: the desktop rail must scroll instead of clipping tender/finalize controls.", css)
        self.assertIn("@media (min-width: 901px) and (max-height: 860px)", css)
        self.assertIn("body.page-cash-register .checkout-sidebar {\n        overflow-y: auto !important;", css)
        self.assertIn("body.page-cash-register .cart-items-list {\n        flex: 1 1 0 !important;\n        min-height: 0 !important;", css)
        self.assertIn("body.page-cash-register #cart-list:empty {\n        min-height: 64px !important;", css)
        self.assertIn("html.force-dark body.page-cash-register .payment-channel-option.active", css)
        self.assertIn("html.force-dark body.page-cash-register .cash-tender-presets button.is-active", css)
        self.assertIn("color: #eff6ff !important", css)
        self.assertIn("CASH REGISTER 150% ZOOM SIDE-BY-SIDE GUARD", css)
        self.assertIn("@media (min-width: 901px) and (max-width: 1360px)", css)
        self.assertIn("grid-template-columns: minmax(0, 1fr) minmax(300px, var(--qs-cash-sidebar-width)) !important", css)
        self.assertIn("body.page-cash-register .product-area {\n        height: calc(100dvh - var(--qs-cash-header-height) - (var(--qs-cash-page-gap) * 2)) !important;", css)
        self.assertIn("body.page-cash-register .checkout-sidebar {\n        position: sticky !important;\n        top: calc(var(--qs-cash-header-height) + var(--qs-cash-page-gap)) !important;", css)
        self.assertIn("height: calc(100dvh - var(--qs-cash-header-height) - (var(--qs-cash-page-gap) * 2)) !important", css)
        self.assertIn("overflow-y: auto !important", css)
        self.assertIn("body.page-cash-register .cart-items-list {\n        flex: 1 1 0 !important;\n        min-height: 0 !important;\n        max-height: none !important;", css)
        self.assertIn("grid-template-columns: repeat(auto-fill, minmax(132px, 1fr)) !important", css)
        self.assertIn("grid-template-columns: repeat(3, minmax(0, 1fr)) !important", css)
        self.assertNotIn("Switch reduced desktop widths out of the narrow rail layout.", css)
        self.assertIn("CASH REGISTER NAV COMMAND BAR REFINEMENT", css)
        self.assertIn("--qs-cash-nav-height", css)
        self.assertIn("grid-template-columns: minmax(0, 1fr) auto !important", css)
        self.assertIn("body.page-cash-register .nav-desktop-links :is(a, .cash-nav-current)", css)
        self.assertIn("body.page-cash-register .cash-nav-current", css)
        self.assertIn("max-width: calc(100vw - 280px) !important", css)
        self.assertIn("cash-register-complete-state-1", template)
        self.assertIn('class="cash-nav-current" aria-current="page"', template)
        self.assertIn('class="logout-link">LOGOUT</a>', template)
        self.assertNotIn("cash-register-nav-refine-2", template)
        self.assertNotIn('class="logout-link" style=', template)
        self.assertNotIn("border: 1px solid #ffffff !important", template)
        self.assertNotIn("background: #ffffff !important", template)
        self.assertIn("body.page-cash-register .logout-link", css)
        self.assertIn("body.page-cash-register .cash-register-command-bar .logout-link", css)
        self.assertIn("rgba(248, 113, 113, 0.16)", css)
        self.assertIn("border-color: rgba(251, 113, 133, 0.72) !important", css)
        self.assertNotIn("body.page-cash-register .cash-register-command-bar .logout-link {\n    border-color: #ffffff !important;\n    background: #ffffff !important;", css)
        self.assertIn('id="post-sale-actions" class="pos-post-sale-card" hidden', template)
        self.assertIn("CASH REGISTER COMPLETION STATE GUARD", css)
        self.assertIn("body.page-cash-register #post-sale-actions[hidden]", css)
        self.assertIn("body.page-cash-register .pos-post-sale-card[hidden]", css)
        self.assertIn("body.page-cash-register #offline-status[hidden]", css)
        self.assertIn("body.page-cash-register .pos-offline-status[hidden]", css)
        self.assertNotIn("cash-register-full-refine-4", template)

    def test_sales_documents_refinement_assets_survive_late_ui_layers(self):
        app_dir = Path(__file__).resolve().parent
        css = (app_dir / "static" / "inventory" / "css" / "style.css").read_text()
        templates_dir = app_dir / "templates" / "inventory"
        template_names = [
            "sales_quotation_list.html",
            "sales_quotation_form.html",
            "sales_quotation_detail.html",
            "sales_invoice_list.html",
            "sales_invoice_form.html",
            "sales_invoice_detail.html",
            "sales_invoice_payment_form.html",
        ]
        page_scopes = [
            "body.page-sales-quotation-list",
            "body.page-sales-quotation-create",
            "body.page-sales-quotation-detail",
            "body.page-sales-invoice-list",
            "body.page-sales-invoice-create",
            "body.page-sales-invoice-detail",
            "body.page-sales-invoice-payment",
        ]

        self.assertIn("QUICKSTOCK SALES DOCUMENTS SUITE REFINEMENT", css)
        self.assertIn("--qs-doc-navy", css)
        self.assertIn(".sales-document-list-card", css)
        self.assertIn(".sales-detail-grid-premium", css)
        self.assertIn(".sales-payment-form", css)
        self.assertIn("html.force-dark body.page-sales-quotation-list", css)
        self.assertIn('html[data-theme-applied="dark"] body.page-sales-invoice-payment', css)
        self.assertIn("@media (max-width: 1180px)", css)
        self.assertIn("@media (max-width: 760px)", css)

        for scope in page_scopes:
            with self.subTest(scope=scope):
                self.assertIn(scope, css)

        for template_name in template_names:
            with self.subTest(template=template_name):
                template = (templates_dir / template_name).read_text()
                self.assertIn("sales-documents-refine-1", template)


class AccountingIntegrationTests(TestCase):
    def _make_owner(self, username="acct-owner"):
        user = User.objects.create_user(username=username, password="password123")
        profile = UserProfile.for_user(user)
        profile.role = "admin"
        profile.status = "active"
        profile.plan = "PRO"
        profile.pro_expires = timezone.now().date() + timedelta(days=30)
        profile.plan_end = timezone.now() + timedelta(days=30)
        profile.save()
        return user

    def _make_item(self, owner, sku="ACCT-001"):
        brand = Brand.objects.create(owner=owner, name=f"Brand {sku}")
        category = Category.objects.create(owner=owner, name=f"Category {sku}")
        return Item.objects.create(
            owner=owner,
            brand=brand,
            category=category,
            name=f"Accounting Item {sku}",
            sku=sku,
            price=Decimal("115.00"),
            cost_price=Decimal("70.00"),
            is_taxable=True,
        )

    def _make_sales_invoice(self, owner, sku="ACCT-001"):
        location = Location.objects.create(owner=owner, name=f"Accounting Branch {sku}")
        customer = Customer.objects.create(owner=owner, name="Island Customer", email="customer@example.com")
        item = self._make_item(owner, sku=sku)
        invoice = SalesInvoice.objects.create(
            owner=owner,
            created_by=owner,
            customer=customer,
            location=location,
            status="issued",
            due_date=timezone.localdate() + timedelta(days=7),
        )
        SalesInvoiceItem.objects.create(
            invoice=invoice,
            item=item,
            quantity=2,
            unit_price=Decimal("115.00"),
        )
        invoice.refresh_from_db()
        return invoice

    def test_xero_sales_invoice_payload_uses_quickstock_financial_facts(self):
        owner = self._make_owner()
        invoice = self._make_sales_invoice(owner)

        payload = build_xero_sales_invoice_payload(invoice)

        self.assertEqual(payload["source"], "quickstock.sales_invoice")
        self.assertEqual(payload["invoice_number"], invoice.invoice_no)
        self.assertEqual(payload["due_date"], invoice.due_date.isoformat())
        self.assertEqual(payload["contact"]["name"], "Island Customer")
        self.assertEqual(payload["total_amount"], "230.00")
        self.assertEqual(payload["line_items"][0]["item_code"], "ACCT-001")

    def test_queue_accounting_sync_is_provider_and_tenant_scoped(self):
        owner = self._make_owner("acct-owner-a")
        other_owner = self._make_owner("acct-owner-b")
        owner_invoice = self._make_sales_invoice(owner, sku="ACCT-A")
        other_invoice = self._make_sales_invoice(other_owner, sku="ACCT-B")

        record = queue_accounting_sync(owner_invoice)

        self.assertEqual(record.owner, owner)
        self.assertEqual(record.integration.owner, owner)
        self.assertEqual(record.integration.provider, AccountingIntegration.PROVIDER_XERO)
        self.assertEqual(record.status, AccountingSyncRecord.STATUS_PENDING)
        self.assertFalse(AccountingSyncRecord.objects.filter(owner=owner, object_id=other_invoice.pk).exists())

    def test_queue_existing_accounting_data_uses_integration_toggles(self):
        owner = self._make_owner()
        self._make_sales_invoice(owner)
        integration = AccountingIntegration.objects.create(
            owner=owner,
            provider=AccountingIntegration.PROVIDER_XERO,
            status=AccountingIntegration.STATUS_CONFIGURED,
            sync_sales=False,
            sync_sales_invoices=True,
            sync_purchase_invoices=False,
            sync_inventory_items=False,
        )

        queued = queue_existing_accounting_data(owner)

        self.assertEqual(len(queued), 1)
        self.assertEqual(AccountingSyncRecord.objects.filter(integration=integration).count(), 1)

    @patch("inventory.accounting.requests.post")
    def test_process_pending_accounting_sync_posts_to_xero_client(self, mock_post):
        class _Response:
            status_code = 200

            def json(self):
                return {"Invoices": [{"InvoiceID": "xero-invoice-123"}]}

        mock_post.return_value = _Response()
        owner = self._make_owner()
        invoice = self._make_sales_invoice(owner)
        integration = AccountingIntegration.objects.create(
            owner=owner,
            provider=AccountingIntegration.PROVIDER_XERO,
            status=AccountingIntegration.STATUS_ACTIVE,
            external_tenant_id="tenant-123",
            settings_payload={"access_token": "token-123"},
        )
        record = queue_accounting_sync(invoice)

        result = process_pending_accounting_sync(integration=integration, limit=10)

        record.refresh_from_db()
        self.assertEqual(result, {"synced": 1, "failed": 0})
        self.assertEqual(record.status, AccountingSyncRecord.STATUS_SYNCED)
        self.assertEqual(record.external_id, "xero-invoice-123")
        self.assertIn("/Invoices", mock_post.call_args.args[0])


class WeekOneSecurityTests(TestCase):
    @staticmethod
    def _oauth_response(payload):
        class _Response:
            def __init__(self, body):
                self._body = body

            def raise_for_status(self):
                return None

            def json(self):
                return self._body

        return _Response(payload)

    def _make_user(self, username):
        user = User.objects.create_user(username=username, password="password123")
        profile = UserProfile.for_user(user)
        profile.role = "admin"
        profile.status = "active"
        profile.plan = "PRO"
        profile.pro_expires = timezone.now().date() + timedelta(days=30)
        profile.plan_end = timezone.now() + timedelta(days=30)
        profile.parent_admin = None
        user.email = f"{username}@example.com"
        user.is_active = True
        user.save(update_fields=["email", "is_active"])
        profile.save()
        return user

    def _make_location(self, owner, name):
        return Location.objects.create(owner=owner, name=name)

    def _make_staff_user(self, username, owner, role="cashier", default_location=None):
        user = User.objects.create_user(username=username, password="password123")
        profile = UserProfile.for_user(user)
        profile.role = role
        profile.status = "active"
        profile.plan = "PRO"
        profile.pro_expires = timezone.now().date() + timedelta(days=30)
        profile.plan_end = timezone.now() + timedelta(days=30)
        profile.parent_admin = UserProfile.for_user(owner)
        profile.default_location = default_location
        profile.save()
        return user

    @override_settings(
        QUICKSTOCK_REQUIRE_EMAIL_VERIFICATION=False,
        QUICKSTOCK_SEND_SIGNUP_EMAILS=False,
    )
    def test_public_signup_creates_active_account_when_verification_disabled(self):
        response = self.client.post(
            reverse("signup"),
            {
                "username": "public-active-signup",
                "email": "public-active-signup@example.com",
                "password": "StrongPass123!",
                "confirm_password": "StrongPass123!",
                "accept_terms": "on",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        user = User.objects.get(username="public-active-signup")
        profile = UserProfile.objects.get(user=user)
        self.assertTrue(user.is_active)
        self.assertTrue(user.is_staff)
        self.assertEqual(profile.role, "admin")
        self.assertEqual(profile.status, "active")
        self.assertEqual(len(mail.outbox), 0)
        self.assertContains(response, "Account created! You can now log in.")

    @override_settings(
        QUICKSTOCK_REQUIRE_EMAIL_VERIFICATION=True,
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    )
    def test_public_signup_can_require_email_verification(self):
        response = self.client.post(
            reverse("signup"),
            {
                "username": "public-pending-signup",
                "email": "public-pending-signup@example.com",
                "password": "StrongPass123!",
                "confirm_password": "StrongPass123!",
                "accept_terms": "on",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        user = User.objects.get(username="public-pending-signup")
        profile = UserProfile.objects.get(user=user)
        self.assertFalse(user.is_active)
        self.assertTrue(user.is_staff)
        self.assertEqual(profile.role, "admin")
        self.assertEqual(profile.status, "pending")
        self.assertTrue(
            any(message.subject == "Activate your QuickStock JA account" for message in mail.outbox)
        )
        self.assertContains(response, "Account created! Verify your email to continue.")

    def test_customer_edit_requires_login(self):
        owner = self._make_user("owner-a")
        customer = Customer.objects.create(owner=owner, name="Alice")

        response = self.client.get(reverse("edit_customer", args=[customer.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_customer_edit_and_delete_are_owner_scoped(self):
        owner = self._make_user("owner-a")
        intruder = self._make_user("owner-b")
        customer = Customer.objects.create(owner=owner, name="Alice")

        self.client.force_login(intruder)

        edit_response = self.client.get(reverse("edit_customer", args=[customer.pk]))
        delete_response = self.client.post(reverse("delete_customer", args=[customer.pk]))

        self.assertEqual(edit_response.status_code, 404)
        self.assertEqual(delete_response.status_code, 404)
        self.assertTrue(Customer.objects.filter(pk=customer.pk).exists())

    def test_inventory_api_rejects_foreign_location_id(self):
        owner = self._make_user("owner-a")
        other_owner = self._make_user("owner-b")
        foreign_location = self._make_location(other_owner, "Other Branch")

        self.client.force_login(owner)
        response = self.client.get(reverse("api_inventory"), {"location_id": foreign_location.pk})

        self.assertEqual(response.status_code, 404)
        self.assertJSONEqual(response.content, {"ok": False, "error": "Location not found"})

    def test_advanced_reports_does_not_disclose_foreign_supplier_name(self):
        owner = self._make_user("owner-report-supplier")
        other_owner = self._make_user("other-report-supplier")
        Supplier.objects.create(owner=owner, name="Owner Supplier")
        foreign_supplier = Supplier.objects.create(owner=other_owner, name="Private Supplier")

        self.client.force_login(owner)
        response = self.client.get(
            reverse("advanced_reports"),
            {"supplier": str(foreign_supplier.pk)},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["supplier_scope_label"], "All Suppliers")
        self.assertEqual(response.context["supplier_filter"], "")
        self.assertNotContains(response, "Private Supplier")

    def test_audit_log_pages_and_exports_are_tenant_scoped(self):
        owner = self._make_user("owner-audit-scope")
        other_owner = self._make_user("other-audit-scope")
        AuditLog.objects.create(
            user=owner,
            action="inventory",
            message="OWNER-AUDIT-MARKER",
        )
        AuditLog.objects.create(
            user=other_owner,
            action="inventory",
            message="FOREIGN-AUDIT-MARKER",
        )

        self.client.force_login(owner)
        page_response = self.client.get(reverse("audit_logs"))
        csv_response = self.client.get(reverse("export_audit_csv"))
        compliance_response = self.client.get(reverse("export_audit_logs"), {"format": "json"})

        self.assertEqual(page_response.status_code, 200)
        self.assertContains(page_response, "OWNER-AUDIT-MARKER")
        self.assertNotContains(page_response, "FOREIGN-AUDIT-MARKER")
        csv_text = csv_response.content.decode("utf-8")
        self.assertIn("OWNER-AUDIT-MARKER", csv_text)
        self.assertNotIn("FOREIGN-AUDIT-MARKER", csv_text)
        compliance_text = compliance_response.content.decode("utf-8")
        self.assertIn("OWNER-AUDIT-MARKER", compliance_text)
        self.assertNotIn("FOREIGN-AUDIT-MARKER", compliance_text)

    def test_supplier_csv_neutralizes_spreadsheet_formulas(self):
        owner = self._make_user("owner-supplier-csv")
        Supplier.objects.create(
            owner=owner,
            name='=HYPERLINK("https://evil.example")',
            contact_name="+1+1",
        )

        self.client.force_login(owner)
        response = self.client.get(reverse("export_suppliers_csv"))

        self.assertEqual(response.status_code, 200)
        csv_text = response.content.decode("utf-8")
        self.assertIn("'=HYPERLINK", csv_text)
        self.assertIn("'+1+1", csv_text)

    def test_sales_csv_handles_revoked_cashier_without_losing_sale(self):
        owner = self._make_user("owner-sales-export-null-cashier")
        location = self._make_location(owner, "Main Branch")
        Sale.objects.create(
            owner=owner,
            cashier=None,
            location=location,
            total_price=Decimal("100.00"),
        )

        self.client.force_login(owner)
        response = self.client.get(reverse("export_sales_csv"))

        self.assertEqual(response.status_code, 200)
        self.assertIn("Unassigned", response.content.decode("utf-8"))

    def test_cashier_api_locations_are_scoped_to_default_location(self):
        owner = self._make_user("owner-locations")
        main = self._make_location(owner, "Main Branch")
        self._make_location(owner, "Warehouse")
        cashier = self._make_staff_user("cashier-locations", owner, default_location=main)

        self.client.force_login(cashier)
        response = self.client.get(reverse("api_locations"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["locations"]), 1)
        self.assertEqual(payload["locations"][0]["id"], main.id)

    def test_cashier_api_location_creation_is_forbidden(self):
        owner = self._make_user("owner-location-write")
        main = self._make_location(owner, "Main Branch")
        cashier = self._make_staff_user("cashier-location-write", owner, default_location=main)

        self.client.force_login(cashier)
        response = self.client.post(
            reverse("api_locations"),
            data={"name": "Back Office"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)

    def test_pro_plan_ignores_stale_trial_plan_end_when_paid_expiry_is_active(self):
        user = User.objects.create_user(username="paid-owner", password="password123")
        profile = UserProfile.for_user(user)
        profile.role = "admin"
        profile.status = "active"
        profile.plan = "PRO"
        profile.pro_expires = timezone.now().date() + timedelta(days=30)
        profile.plan_end = timezone.now() - timedelta(days=30)
        profile.save()

        self.assertTrue(profile.is_pro_active())
        self.assertEqual(profile.plan_badge_label, "PRO")

    def test_operator_plan_badge_renders_from_profile_state(self):
        pro_user = self._make_user("badge-pro-owner")
        trial_user = self._make_user("badge-trial-owner")
        trial_user.profile.plan = "TRIAL"
        trial_user.profile.pro_expires = None
        trial_user.profile.plan_end = timezone.now() + timedelta(days=5)
        trial_user.profile.save(update_fields=["plan", "pro_expires", "plan_end"])

        self.client.force_login(pro_user)
        pro_response = self.client.get(reverse("dashboard"))
        self.assertEqual(pro_response.status_code, 200)
        self.assertContains(pro_response, ">PRO<", html=False)

        self.client.force_login(trial_user)
        trial_response = self.client.get(reverse("dashboard"))
        self.assertEqual(trial_response.status_code, 200)
        self.assertContains(trial_response, ">TRIAL<", html=False)

    def test_support_script_is_scoped_to_support_page(self):
        app_dir = Path(__file__).resolve().parent
        support_js = (app_dir / "static" / "inventory" / "js" / "support.js").read_text()

        self.assertIn('contains("page-support")', support_js)
        self.assertIn("return;", support_js)

    def test_cashier_inventory_api_write_is_forbidden(self):
        owner = self._make_user("owner-inv-write")
        main = self._make_location(owner, "Main Branch")
        cashier = self._make_staff_user("cashier-inv-write", owner, default_location=main)

        self.client.force_login(cashier)
        response = self.client.post(
            reverse("api_inventory"),
            data={"name": "Unauthorized Item", "quantity": 1, "price": "10.00"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)

    def test_cashier_cannot_transfer_stock_through_page_or_legacy_api(self):
        owner = self._make_user("cashier-transfer-owner")
        source = self._make_location(owner, "Warehouse")
        destination = self._make_location(owner, "Storefront")
        cashier = self._make_staff_user(
            "cashier-transfer-user",
            owner,
            default_location=source,
        )
        category = Category.objects.create(owner=owner, name="Transfer Category")
        brand = Brand.objects.create(owner=owner, name="Transfer Brand")
        item = Item.objects.create(
            owner=owner,
            name="Protected Transfer Item",
            sku="CASHIER-XFER-1",
            category=category,
            brand=brand,
            cost_price=Decimal("50.00"),
            price=Decimal("100.00"),
        )
        StockRecord.objects.create(item=item, location=source, quantity=8)

        self.client.force_login(cashier)
        page_response = self.client.post(
            reverse("transfer_stock"),
            {
                "item_id": item.id,
                "from_location": source.name,
                "to_location": destination.id,
                "quantity": 3,
            },
        )
        api_response = self.client.post(
            reverse("api_transfer_stock"),
            data={
                "sku": item.sku,
                "from_location_id": source.id,
                "to_location_id": destination.id,
                "quantity": 3,
            },
            content_type="application/json",
        )

        self.assertEqual(page_response.status_code, 302)
        self.assertEqual(page_response["Location"], reverse("dashboard"))
        self.assertEqual(api_response.status_code, 403)
        self.assertFalse(StockTransfer.objects.filter(item=item).exists())
        self.assertEqual(StockRecord.objects.get(item=item, location=source).quantity, 8)
        self.assertFalse(StockRecord.objects.filter(item=item, location=destination).exists())

    def test_cashier_cannot_reassign_register_to_foreign_location(self):
        owner = self._make_user("register-location-owner")
        assigned_location = self._make_location(owner, "Assigned Branch")
        cashier = self._make_staff_user(
            "register-location-cashier",
            owner,
            default_location=assigned_location,
        )
        other_owner = self._make_user("register-location-other-owner")
        foreign_location = self._make_location(other_owner, "Foreign Branch")

        self.client.force_login(cashier)
        response = self.client.post(
            reverse("cash_register"),
            {"action": "update_location", "new_location": foreign_location.id},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("cash_register"))
        profile = UserProfile.objects.get(user=cashier)
        self.assertEqual(profile.default_location, assigned_location)

    def test_cash_shift_rejects_location_from_another_tenant(self):
        owner = self._make_user("shift-owner")
        cashier_location = self._make_location(owner, "Cashier Branch")
        cashier = self._make_staff_user(
            "shift-cashier",
            owner,
            default_location=cashier_location,
        )
        other_owner = self._make_user("shift-other-owner")
        foreign_location = self._make_location(other_owner, "Foreign Branch")

        with self.assertRaises(ValidationError):
            CashShift.objects.create(
                cashier=cashier,
                location=foreign_location,
                opening_cash=Decimal("100.00"),
            )

    def test_inventory_add_creates_owner_scoped_category_and_brand(self):
        owner_a = self._make_user("owner-catalog-a")
        owner_b = self._make_user("owner-catalog-b")
        Category.objects.create(owner=owner_a, name="Shared Name")
        Brand.objects.create(owner=owner_a, name="Shared Brand")

        self.client.force_login(owner_b)
        response = self.client.post(
            reverse("inventory_add"),
            {
                "name": "Tenant Scoped Item",
                "category": "Shared Name",
                "brand": "Shared Brand",
                "selling_price": "25.00",
                "cost_price": "10.00",
                "quantity": "2",
            },
        )

        self.assertEqual(response.status_code, 302)
        item = Item.objects.get(owner=owner_b, name="Tenant Scoped Item")
        self.assertEqual(item.category.owner, owner_b)
        self.assertEqual(item.brand.owner, owner_b)
        self.assertEqual(Category.objects.filter(name="Shared Name").count(), 2)
        self.assertEqual(Brand.objects.filter(name="Shared Brand").count(), 2)

    def test_stock_record_rejects_location_from_other_admin(self):
        owner_a = self._make_user("owner-stock-a")
        owner_b = self._make_user("owner-stock-b")
        category = Category.objects.create(owner=owner_a, name="Stock Category")
        brand = Brand.objects.create(owner=owner_a, name="Stock Brand")
        item = Item.objects.create(
            owner=owner_a,
            name="Tenant Item",
            sku="TENANT-1",
            category=category,
            brand=brand,
            cost_price=Decimal("10.00"),
            price=Decimal("20.00"),
        )
        foreign_location = self._make_location(owner_b, "Foreign Branch")

        with self.assertRaises(ValidationError):
            StockRecord.objects.create(item=item, location=foreign_location, quantity=1)

    def test_sale_item_rejects_negative_quantity_without_increasing_stock(self):
        owner = self._make_user("owner-negative-sale-line")
        location = self._make_location(owner, "Main Branch")
        category = Category.objects.create(owner=owner, name="Sale Category")
        brand = Brand.objects.create(owner=owner, name="Sale Brand")
        item = Item.objects.create(
            owner=owner,
            name="Protected Stock",
            sku="PROTECTED-STOCK",
            category=category,
            brand=brand,
            cost_price=Decimal("50.00"),
            price=Decimal("100.00"),
        )
        stock = StockRecord.objects.create(item=item, location=location, quantity=5)
        sale = Sale.objects.create(owner=owner, cashier=owner, location=location)

        with self.assertRaises(ValidationError):
            SaleItem.objects.create(
                sale=sale,
                item=item,
                quantity=-3,
                unit_price=Decimal("100.00"),
            )

        stock.refresh_from_db()
        self.assertEqual(stock.quantity, 5)
        self.assertFalse(SaleItem.objects.filter(sale=sale).exists())

    def test_sale_rejects_negative_financial_totals(self):
        owner = self._make_user("owner-negative-sale-total")
        location = self._make_location(owner, "Main Branch")

        with self.assertRaises(ValidationError):
            Sale.objects.create(
                owner=owner,
                cashier=owner,
                location=location,
                total_price=Decimal("-1.00"),
            )

    def test_sales_quotation_rejects_customer_from_other_admin(self):
        owner_a = self._make_user("owner-quote-a")
        owner_b = self._make_user("owner-quote-b")
        foreign_customer = Customer.objects.create(owner=owner_b, name="Foreign Customer")

        with self.assertRaises(ValidationError):
            SalesQuotation.objects.create(
                owner=owner_a,
                created_by=owner_a,
                customer=foreign_customer,
            )

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="noreply@quickstockja.com",
        SUPPORT_EMAIL_RECIPIENT="support@quickstockja.com",
    )
    def test_support_accepts_non_gmail_reply_to_address(self):
        response = self.client.post(
            reverse("support"),
            {
                "email": "guest_user@yahoo.com",
                "message": "Need help getting into my account.",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].reply_to, ["guest_user@yahoo.com"])
        self.assertEqual(mail.outbox[0].to, ["support@quickstockja.com"])

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="noreply@quickstockja.com",
        SUPPORT_EMAIL_RECIPIENT="support@quickstockja.com",
        SUPPORT_TICKET_RATE_LIMIT=1,
        SUPPORT_TICKET_RATE_WINDOW=60,
    )
    def test_support_rate_limits_repeated_anonymous_email_dispatch(self):
        cache.clear()
        payload = {
            "email": "guest_rate_limit@example.com",
            "message": "Please help with my account.",
        }

        first_response = self.client.post(reverse("support"), payload)
        second_response = self.client.post(reverse("support"), payload)

        self.assertEqual(first_response.status_code, 302)
        self.assertEqual(second_response.status_code, 429)
        self.assertEqual(second_response["Retry-After"], "60")
        self.assertEqual(len(mail.outbox), 1)

    def test_support_page_has_editable_email_field_for_guests(self):
        response = self.client.get(reverse("support"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="email"')
        self.assertContains(response, 'autocomplete="email"')
        self.assertContains(response, 'aria-controls="support-answer-login"')
        self.assertContains(response, 'aria-controls="support-answer-wipay"')
        self.assertContains(response, 'aria-controls="support-answer-export"')
        self.assertContains(response, "inventory/js/support.js")

    @override_settings(LOGIN_OTP_MAX_ATTEMPTS=3, LOGIN_OTP_RATE_WINDOW=60)
    def test_login_otp_rate_limits_guesses_and_invalidates_challenge(self):
        cache.clear()
        user = self._make_user("otp-rate-limit")
        session = self.client.session
        session["otp_user_id"] = user.id
        session["otp_pending_ip"] = "127.0.0.1"
        session["otp_pending_fp"] = hashlib.sha256("TestBrowser".encode("utf-8")).hexdigest()
        session.save()
        cache.set(f"login_otp:{user.id}", "123456", timeout=600)

        for _ in range(2):
            response = self.client.post(
                reverse("login_otp"),
                {"otp_code": "000000"},
                HTTP_USER_AGENT="TestBrowser",
                REMOTE_ADDR="127.0.0.1",
            )
            self.assertEqual(response.status_code, 200)
        response = self.client.post(
            reverse("login_otp"),
            {"otp_code": "000000"},
            HTTP_USER_AGENT="TestBrowser",
            REMOTE_ADDR="127.0.0.1",
        )

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response["Retry-After"], "60")
        self.assertNotIn("otp_user_id", self.client.session)
        self.assertIsNone(cache.get(f"login_otp:{user.id}"))

    def test_login_otp_rejects_changed_device_context(self):
        cache.clear()
        user = self._make_user("otp-device-change")
        session = self.client.session
        session["otp_user_id"] = user.id
        session["otp_pending_ip"] = "127.0.0.1"
        session["otp_pending_fp"] = hashlib.sha256("OriginalBrowser".encode("utf-8")).hexdigest()
        session.save()
        cache.set(f"login_otp:{user.id}", "123456", timeout=600)

        response = self.client.post(
            reverse("login_otp"),
            {"otp_code": "123456"},
            HTTP_USER_AGENT="DifferentBrowser",
            REMOTE_ADDR="127.0.0.1",
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("login"))
        self.assertNotIn("otp_user_id", self.client.session)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_login_otp_survives_render_proxy_ip_change(self):
        cache.clear()
        user = self._make_user("otp-proxy-change")
        session = self.client.session
        session["otp_user_id"] = user.id
        session["otp_pending_ip"] = "10.0.0.1"
        session["otp_pending_fp"] = hashlib.sha256("StableBrowser".encode("utf-8")).hexdigest()
        session.save()
        cache.set(f"login_otp:{user.id}", "123456", timeout=600)

        response = self.client.post(
            reverse("login_otp"),
            {"otp_code": "123456"},
            HTTP_USER_AGENT="StableBrowser",
            REMOTE_ADDR="10.0.0.2",
            HTTP_X_FORWARDED_FOR="203.0.113.25",
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("_auth_user_id", self.client.session)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="noreply@quickstockja.com",
    )
    def test_signup_accepts_non_gmail_email_address(self):
        post_save.disconnect(create_user_verification, sender=User)
        post_save.disconnect(send_welcome_email, sender=User)
        try:
            response = self.client.post(
                reverse("signup"),
                {
                    "username": "fresh-user",
                    "email": "fresh.user@outlook.com",
                    "password": "StrongPass123!",
                    "confirm_password": "StrongPass123!",
                    "accept_terms": "on",
                },
            )
        finally:
            post_save.connect(create_user_verification, sender=User)
            post_save.connect(send_welcome_email, sender=User)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("login"))
        self.assertTrue(User.objects.filter(username="fresh-user", email="fresh.user@outlook.com").exists())

    def test_signup_rejects_email_already_linked_to_an_account(self):
        existing = self._make_user("existing-email-owner")

        response = self.client.post(
            reverse("signup"),
            {
                "username": "duplicate-email-signup",
                "email": existing.email.upper(),
                "password": "StrongPass123!",
                "confirm_password": "StrongPass123!",
                "accept_terms": "on",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "An account already uses that email address.")
        self.assertFalse(User.objects.filter(username="duplicate-email-signup").exists())

    def test_user_creation_with_email_creates_one_verification_record(self):
        user = User.objects.create_user(
            username="verification-record-user",
            email="verification-record@example.com",
            password="StrongPass123!",
        )

        self.assertEqual(UserVerification.objects.filter(user=user).count(), 1)

    @override_settings(
        DEBUG=False,
        EMAIL_PROVIDER="resend",
        EMAIL_BACKEND=RESEND_BACKEND,
        RESEND_API_KEY="re_test_key",
        DEFAULT_FROM_EMAIL="QuickStock JA <noreply@quickstockja.com>",
    )
    def test_resend_email_configuration_is_ready_for_render(self):
        status = email_delivery_status()

        self.assertTrue(status["ok"])
        self.assertEqual(status["provider"], "resend")
        self.assertTrue(status["api_key_set"])

    @override_settings(
        DEBUG=False,
        EMAIL_PROVIDER="resend",
        EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend",
        EMAIL_HOST="smtp.gmail.com",
        EMAIL_HOST_USER="owner@quickstockja.com",
        EMAIL_HOST_PASSWORD="stale-app-password",
        DEFAULT_FROM_EMAIL="owner@quickstockja.com",
    )
    def test_resend_configuration_rejects_stale_render_smtp_backend(self):
        status = email_delivery_status()

        self.assertFalse(status["ok"])
        self.assertEqual(status["provider"], "resend")
        self.assertIn("HTTPS email backend is not active", status["detail"])

    @override_settings(
        DEBUG=False,
        EMAIL_PROVIDER="resend",
        EMAIL_BACKEND=RESEND_BACKEND,
        RESEND_API_KEY="re_test_key",
        DEFAULT_FROM_EMAIL="QuickStock JA <noreply@your-verified-domain.example>",
    )
    def test_resend_configuration_rejects_placeholder_sender_domain(self):
        status = email_delivery_status()

        self.assertFalse(status["ok"])
        self.assertFalse(status["sender_set"])
        self.assertIn("real sender", status["detail"])

    @override_settings(
        DEBUG=False,
        EMAIL_PROVIDER="resend",
        EMAIL_BACKEND=RESEND_BACKEND,
        RESEND_API_KEY="re_test_key",
        DEFAULT_FROM_EMAIL="quickstockja@gmail.com",
    )
    def test_resend_configuration_rejects_public_mailbox_sender_domain(self):
        status = email_delivery_status()

        self.assertFalse(status["ok"])
        self.assertFalse(status["sender_set"])
        self.assertIn("domain verified in Resend", status["detail"])

    @override_settings(
        DEBUG=False,
        EMAIL_PROVIDER="resend",
        EMAIL_BACKEND=CONSOLE_BACKEND,
        RESEND_API_KEY="",
        DEFAULT_FROM_EMAIL="QuickStock JA <noreply@quickstockja.com>",
    )
    def test_login_otp_refuses_to_claim_delivery_without_render_email_credentials(self):
        cache.clear()
        user = self._make_user("otp-email-unconfigured")

        self.assertFalse(_send_login_otp(user))
        self.assertIsNone(cache.get(f"login_otp:{user.id}"))

    @override_settings(
        QUICKSTOCK_ALLOW_SUPERUSER_OTP_BYPASS=True,
        QUICKSTOCK_SUPERUSER_USERNAME="KeviiDan",
    )
    def test_configured_superuser_can_use_emergency_otp_bypass(self):
        user = self._make_user("KeviiDan")
        user.is_staff = True
        user.is_superuser = True
        user.save(update_fields=["is_staff", "is_superuser"])

        with patch("inventory.views._send_login_otp") as send_login_otp:
            response = self.client.post(
                reverse("login"),
                {"username": "KeviiDan", "password": "password123"},
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.id)
        send_login_otp.assert_not_called()
        self.assertTrue(
            AuditLog.objects.filter(user=user, action="login_otp_bypass", severity="warn").exists()
        )

    @override_settings(
        QUICKSTOCK_ALLOW_SUPERUSER_OTP_BYPASS=True,
        QUICKSTOCK_SUPERUSER_USERNAME="KeviiDan",
        EMAIL_BACKEND=CONSOLE_BACKEND,
    )
    def test_emergency_otp_bypass_does_not_apply_to_other_users(self):
        self._make_user("regular-admin")

        with (
            patch("inventory.views._send_login_otp", return_value=False) as send_login_otp,
            patch("inventory.views._render_login", return_value=HttpResponse(status=503)),
        ):
            response = self.client.post(
                reverse("login"),
                {"username": "regular-admin", "password": "password123"},
            )

        self.assertEqual(response.status_code, 503)
        send_login_otp.assert_called_once()
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_bootstrap_superuser_repairs_existing_user_without_password_secret(self):
        user = User.objects.create_user(username="KeviiDan", password="old-password")

        with patch.dict(
            os.environ,
            {
                "QUICKSTOCK_SUPERUSER_USERNAME": "KeviiDan",
                "QUICKSTOCK_SUPERUSER_EMAIL": "kevoncampbell84@gmail.com",
                "QUICKSTOCK_SUPERUSER_PASSWORD": "",
                "QUICKSTOCK_SUPERUSER_RESET_PASSWORD": "false",
            },
        ):
            call_command("bootstrap_superuser")

        user.refresh_from_db()
        self.assertEqual(user.email, "kevoncampbell84@gmail.com")
        self.assertTrue(user.is_active)
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.check_password("old-password"))

    def test_delete_deploy_user_removes_configured_user(self):
        User.objects.create_user(username="KeviiDan", password="old-password")

        with patch.dict(os.environ, {"QUICKSTOCK_DELETE_USER_USERNAME": "KeviiDan"}):
            call_command("delete_deploy_user")

        self.assertFalse(User.objects.filter(username="KeviiDan").exists())

    def test_accidentally_archived_deploy_user_is_restored_once(self):
        user = self._make_user("KeviiDan")
        profile = user.profile
        profile.is_archived = True
        profile.archived_at = timezone.now()
        profile.archived_by = user
        profile.archive_reason = "Account purge blocked because financial history must be retained."
        profile.status = "suspended"
        profile.plan = "PRO"
        profile.pro_expires = timezone.localdate() + timedelta(days=365)
        profile.save()
        user.is_active = False
        user.save(update_fields=["is_active"])

        migration = importlib.import_module(
            "inventory.migrations.0081_restore_accidentally_archived_account"
        )
        migration.restore_accidentally_archived_account(
            importlib.import_module("django.apps").apps,
            None,
        )

        user.refresh_from_db()
        profile.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertFalse(profile.is_archived)
        self.assertEqual(profile.status, "active")
        self.assertEqual(profile.plan, "PRO")
        self.assertIsNone(profile.archived_at)
        self.assertIsNone(profile.archived_by)
        self.assertEqual(profile.archive_reason, "")

    def test_render_build_does_not_repeat_deploy_user_deletion(self):
        project_root = Path(__file__).resolve().parents[4]
        build_script = (project_root / "Website" / "BackEnd" / "quickstock" / "build.sh").read_text()
        render_blueprint = (project_root / "render.yaml").read_text()

        self.assertNotIn("delete_deploy_user", build_script)
        self.assertNotIn("QUICKSTOCK_DELETE_USER_USERNAME", render_blueprint)

    @override_settings(
        SOCIAL_AUTH_PROVIDERS={
            "google": {
                "label": "Google",
                "client_id": "google-client",
                "client_secret": "google-secret",
                "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
                "token_url": "https://oauth2.googleapis.com/token",
                "userinfo_url": "https://openidconnect.googleapis.com/v1/userinfo",
                "scope": ["openid", "email", "profile"],
            },
            "microsoft": {
                "label": "Microsoft",
                "client_id": "ms-client",
                "client_secret": "ms-secret",
                "tenant_id": "common",
                "scope": ["openid", "email", "profile", "User.Read"],
            },
        }
    )
    def test_login_page_renders_social_sign_in_options(self):
        response = self.client.get(reverse("login"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sign in with Google")
        self.assertContains(response, "Sign in with Microsoft")

    @override_settings(
        SOCIAL_AUTH_PROVIDERS={
            "google": {
                "label": "Google",
                "client_id": "google-client",
                "client_secret": "google-secret",
                "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
                "token_url": "https://oauth2.googleapis.com/token",
                "userinfo_url": "https://openidconnect.googleapis.com/v1/userinfo",
                "scope": ["openid", "email", "profile"],
            }
        }
    )
    def test_google_social_login_redirects_to_provider(self):
        response = self.client.get(reverse("social_login_start", args=["google"]))

        self.assertEqual(response.status_code, 302)
        self.assertIn("accounts.google.com", response["Location"])
        self.assertIn("state=", response["Location"])
        self.assertIn("social_auth_state:google", self.client.session)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        SOCIAL_AUTH_PROVIDERS={
            "google": {
                "label": "Google",
                "client_id": "google-client",
                "client_secret": "google-secret",
                "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
                "token_url": "https://oauth2.googleapis.com/token",
                "userinfo_url": "https://openidconnect.googleapis.com/v1/userinfo",
                "scope": ["openid", "email", "profile"],
            }
        }
    )
    @patch("inventory.views.requests.get")
    @patch("inventory.views.requests.post")
    def test_google_social_login_callback_uses_existing_account_and_starts_otp(self, mock_post, mock_get):
        user = self._make_user("social-google")
        user.email = "social.google@example.com"
        user.save(update_fields=["email"])

        session = self.client.session
        session["social_auth_state:google"] = "known-state"
        session.save()

        mock_post.return_value = self._oauth_response({"access_token": "token-123"})
        mock_get.return_value = self._oauth_response(
            {
                "email": "social.google@example.com",
                "email_verified": True,
                "name": "Social Google",
            }
        )

        response = self.client.get(
            reverse("social_login_callback", args=["google"]),
            {"state": "known-state", "code": "oauth-code"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "inventory/login_otp.html")
        self.assertEqual(self.client.session.get("otp_user_id"), user.id)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("login code", mail.outbox[0].subject.lower())

    @override_settings(
        SOCIAL_AUTH_PROVIDERS={
            "microsoft": {
                "label": "Microsoft",
                "client_id": "ms-client",
                "client_secret": "ms-secret",
                "tenant_id": "common",
                "scope": ["openid", "email", "profile", "User.Read"],
            }
        }
    )
    @patch("inventory.views.requests.get")
    @patch("inventory.views.requests.post")
    def test_microsoft_social_login_rejects_unlinked_email(self, mock_post, mock_get):
        session = self.client.session
        session["social_auth_state:microsoft"] = "ms-state"
        session.save()

        mock_post.return_value = self._oauth_response({"access_token": "token-123"})
        mock_get.return_value = self._oauth_response(
            {
                "preferred_username": "missing.user@example.com",
                "name": "Missing User",
            }
        )

        response = self.client.get(
            reverse("social_login_callback", args=["microsoft"]),
            {"state": "ms-state", "code": "oauth-code"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No QuickStock account is linked to that email address yet.")

    def test_quickstock_manifest_route_is_available(self):
        response = self.client.get(reverse("quickstock_manifest"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/manifest+json")
        payload = response.json()
        self.assertEqual(payload["start_url"], reverse("sales"))
        self.assertEqual(payload["name"], "QuickStock JA")

    def test_quickstock_service_worker_route_is_available(self):
        response = self.client.get(reverse("quickstock_service_worker"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/javascript")
        self.assertIn("self.addEventListener", response.content.decode())

    def test_admin_settings_updates_system_logistics(self):
        cache.clear()
        owner = self._make_user("owner-settings")
        main = self._make_location(owner, "Main Branch")
        warehouse = self._make_location(owner, "Warehouse")

        self.client.force_login(owner)
        response = self.client.post(
            reverse("settings"),
            {
                "action": "update_system_logistics",
                "audit_retention_days": "45",
                "funding_branch": str(warehouse.id),
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("settings"))
        self.assertEqual(cache.get("audit_retention_days"), 45)
        self.assertEqual(cache.get(f"primary_funding_branch:{owner.id}"), warehouse.id)

    def test_receipt_branding_rejects_non_image_logo_upload(self):
        owner = self._make_user("owner-invalid-logo")
        self.client.force_login(owner)
        fake_logo = SimpleUploadedFile(
            "receipt-logo.html",
            b"<html><script>alert('not an image')</script></html>",
            content_type="image/png",
        )

        response = self.client.post(
            reverse("settings"),
            {"brand_name": "Safe Store", "brand_logo_file": fake_logo},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Upload a valid PNG, JPEG, or WebP image.")
        profile = UserProfile.objects.get(user=owner)
        self.assertFalse(bool(profile.receipt_logo))

    def test_settings_uploaded_logo_does_not_fill_external_url_field(self):
        owner = self._make_user("owner-uploaded-logo-display")
        profile = UserProfile.objects.get(user=owner)
        profile.receipt_logo.save(
            "receipt-logo.png",
            SimpleUploadedFile("receipt-logo.png", b"fake-image-bytes", content_type="image/png"),
            save=True,
        )
        self.client.force_login(owner)

        response = self.client.get(reverse("settings"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["brand_defaults"]["logo_url"], "")
        self.assertContains(response, "Remove logo from receipts and invoices")

    def test_admin_can_remove_uploaded_receipt_logo_from_settings(self):
        owner = self._make_user("owner-remove-logo")
        profile = UserProfile.objects.get(user=owner)
        profile.receipt_logo.save(
            "receipt-logo.png",
            SimpleUploadedFile("receipt-logo.png", b"fake-image-bytes", content_type="image/png"),
            save=True,
        )
        profile.receipt_logo_url = "https://cdn.example.test/logo.png"
        profile.save(update_fields=["receipt_logo", "receipt_logo_url"])
        self.client.force_login(owner)

        response = self.client.post(
            reverse("settings"),
            {
                "brand_name": "No Logo Store",
                "brand_logo_url": profile.receipt_logo_url,
                "remove_brand_logo": "1",
            },
        )

        self.assertEqual(response.status_code, 302)
        profile.refresh_from_db()
        self.assertFalse(bool(profile.receipt_logo))
        self.assertEqual(profile.receipt_logo_url, "")

    def test_staff_creation_cannot_reassign_an_existing_tenant_account(self):
        owner_a = self._make_user("staff-owner-a")
        owner_b = self._make_user("staff-owner-b")
        existing_staff = self._make_staff_user(
            "existing-tenant-staff",
            owner_b,
            role="manager",
        )
        original_profile = UserProfile.objects.get(user=existing_staff)

        self.client.force_login(owner_a)
        response = self.client.post(
            reverse("manage_staff"),
            {
                "action": "create",
                "username": existing_staff.username,
                "email": "takeover-attempt@example.com",
                "role": "cashier",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "That username is already registered.")
        original_profile.refresh_from_db()
        self.assertEqual(original_profile.parent_admin, UserProfile.objects.get(user=owner_b))
        self.assertEqual(original_profile.role, "manager")

    def test_staff_creation_rejects_admin_role_tampering(self):
        owner = self._make_user("staff-role-owner")
        self.client.force_login(owner)

        response = self.client.post(
            reverse("manage_staff"),
            {
                "action": "create",
                "username": "crafted-nested-admin",
                "email": "crafted-admin@example.com",
                "role": "admin",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Staff accounts must use the Manager or Cashier role.")
        self.assertFalse(User.objects.filter(username="crafted-nested-admin").exists())

    def test_new_staff_profile_is_active_and_linked_to_requesting_company(self):
        owner = self._make_user("new-staff-owner")
        self.client.force_login(owner)

        response = self.client.post(
            reverse("manage_staff"),
            {
                "action": "create",
                "username": "new-company-cashier",
                "email": "new-company-cashier@example.com",
                "role": "cashier",
            },
        )

        self.assertEqual(response.status_code, 302)
        user = User.objects.get(username="new-company-cashier")
        profile = UserProfile.objects.get(user=user)
        self.assertTrue(user.is_active)
        self.assertEqual(profile.status, "active")
        self.assertEqual(profile.role, "cashier")
        self.assertEqual(profile.parent_admin, UserProfile.objects.get(user=owner))

    def test_revoking_cashier_preserves_company_sales_history(self):
        owner = self._make_user("sale-history-owner")
        location = self._make_location(owner, "Main Branch")
        cashier = self._make_staff_user(
            "sale-history-cashier",
            owner,
            role="cashier",
            default_location=location,
        )
        sale = Sale.objects.create(
            owner=owner,
            cashier=cashier,
            location=location,
            total_price=Decimal("115.00"),
        )

        self.client.force_login(owner)
        response = self.client.post(
            reverse("manage_staff"),
            {"action": "delete", "user_id": cashier.id},
        )

        self.assertEqual(response.status_code, 302)
        cashier.refresh_from_db()
        cashier.profile.refresh_from_db()
        self.assertTrue(User.objects.filter(pk=cashier.pk).exists())
        self.assertFalse(cashier.is_active)
        self.assertTrue(cashier.profile.is_archived)
        sale.refresh_from_db()
        self.assertEqual(sale.owner, owner)
        self.assertEqual(sale.cashier, cashier)

    def test_revoked_staff_are_hidden_from_settings_staff_list(self):
        owner = self._make_user("revoked-staff-list-owner")
        location = self._make_location(owner, "Main Branch")
        cashier = self._make_staff_user(
            "revoked-staff-list-cashier",
            owner,
            role="cashier",
            default_location=location,
        )
        Sale.objects.create(
            owner=owner,
            cashier=cashier,
            location=location,
            total_price=Decimal("115.00"),
        )

        self.client.force_login(owner)
        response = self.client.post(
            reverse("manage_staff"),
            {"action": "delete", "user_id": cashier.id},
        )

        self.assertEqual(response.status_code, 302)
        cashier.refresh_from_db()
        cashier.profile.refresh_from_db()
        self.assertFalse(cashier.is_active)
        self.assertTrue(cashier.profile.is_archived)

        settings_page = self.client.get(reverse("settings"))

        self.assertEqual(settings_page.status_code, 200)
        self.assertNotIn(cashier.profile, list(settings_page.context["all_staff"]))

    def test_dashboard_honors_saved_dark_theme_preference(self):
        owner = self._make_user("owner-dark-theme")
        owner.profile.theme = "dark"
        owner.profile.save(update_fields=["theme"])

        self.client.force_login(owner)
        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('data-theme="dark"', content)
        self.assertIn('data-theme-applied="dark"', content)
        self.assertIn('class="force-dark"', content)
        self.assertIn('"themePref": "dark"', content)

    def test_daily_summary_manual_cash_count_updates_actual_cash(self):
        owner = self._make_user("owner-cash-count")
        location = self._make_location(owner, "Main Branch")
        selected_date = timezone.localdate()
        invoice = SalesInvoice.objects.create(
            owner=owner,
            customer=None,
            location=location,
            subtotal=Decimal("500.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("500.00"),
        )
        SalesInvoicePayment.objects.create(
            invoice=invoice,
            received_by=owner,
            amount=Decimal("500.00"),
            payment_method="cash",
        )

        self.client.force_login(owner)
        response = self.client.post(
            reverse("daily_summary"),
            {
                "action": "update_cash_count",
                "qty_5000": "1",
                "qty_2000": "2",
                "qty_1000": "0",
                "qty_500": "0",
                "qty_100": "3",
                "qty_50": "0",
                "qty_20": "0",
                "qty_10": "0",
                "qty_5": "0",
                "qty_1": "7",
                "ncb_machine_amount": "1250.75",
                "bns_machine_amount": "800.25",
            },
        )

        self.assertEqual(response.status_code, 302)
        cash_count = DailyCashCount.objects.get(owner=owner, summary_date=selected_date)
        self.assertEqual(cash_count.qty_5000, 1)
        self.assertEqual(cash_count.qty_2000, 2)
        self.assertEqual(cash_count.qty_100, 3)
        self.assertEqual(cash_count.qty_1, 7)
        self.assertEqual(cash_count.actual_cash, Decimal("9307.00"))
        self.assertEqual(cash_count.ncb_machine_amount, Decimal("1250.75"))
        self.assertEqual(cash_count.bns_machine_amount, Decimal("800.25"))

        page = self.client.get(reverse("daily_summary"))
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.context["actual_cash"], Decimal("9307.00"))
        self.assertEqual(page.context["expected_cash"], Decimal("500.00"))
        self.assertEqual(page.context["ncb_machine_amount"], Decimal("1250.75"))
        self.assertEqual(page.context["bns_machine_amount"], Decimal("800.25"))
        self.assertEqual(page.context["discounts"], Decimal("0.00"))
        self.assertFalse(page.context["has_discounts"])
        self.assertNotContains(page, "Discounts Given:")
        self.assertNotContains(page, "$5,000")

    def test_daily_summary_card_machine_amounts_balance_card_sales(self):
        owner = self._make_user("owner-card-balance")
        location = self._make_location(owner, "Main Branch")
        invoice = SalesInvoice.objects.create(
            owner=owner,
            customer=None,
            location=location,
            subtotal=Decimal("2550.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("2550.00"),
        )
        SalesInvoicePayment.objects.create(
            invoice=invoice,
            received_by=owner,
            amount=Decimal("500.00"),
            payment_method="cash",
        )
        SalesInvoicePayment.objects.create(
            invoice=invoice,
            received_by=owner,
            amount=Decimal("1250.00"),
            payment_method="debit_card",
        )
        SalesInvoicePayment.objects.create(
            invoice=invoice,
            received_by=owner,
            amount=Decimal("800.00"),
            payment_method="credit_card",
        )

        self.client.force_login(owner)
        response = self.client.post(
            reverse("daily_summary"),
            {
                "action": "update_cash_count",
                "qty_5000": "0",
                "qty_2000": "0",
                "qty_1000": "0",
                "qty_500": "1",
                "qty_100": "0",
                "qty_50": "0",
                "qty_20": "0",
                "qty_10": "0",
                "qty_5": "0",
                "qty_1": "0",
                "ncb_machine_amount": "1250.00",
                "bns_machine_amount": "800.00",
            },
        )

        self.assertEqual(response.status_code, 302)
        page = self.client.get(reverse("daily_summary"))
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.context["expected_cash"], Decimal("500.00"))
        self.assertEqual(page.context["expected_card_amount"], Decimal("2050.00"))
        self.assertEqual(page.context["expected_reconciliation_total"], Decimal("2550.00"))
        self.assertEqual(page.context["actual_cash"], Decimal("500.00"))
        self.assertEqual(page.context["actual_card_amount"], Decimal("2050.00"))
        self.assertEqual(page.context["actual_reconciliation_total"], Decimal("2550.00"))
        self.assertEqual(page.context["variance"], Decimal("0.00"))
        self.assertContains(page, "Balanced")

    def test_daily_summary_tracks_cash_expenditure_and_lodgement_in_expected_cash(self):
        owner = self._make_user("owner-summary-cash-movement")
        location = self._make_location(owner, "Main Branch")
        selected_date = timezone.localdate()
        invoice = SalesInvoice.objects.create(
            owner=owner,
            customer=None,
            location=location,
            subtotal=Decimal("1200.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("1200.00"),
        )
        SalesInvoicePayment.objects.create(
            invoice=invoice,
            received_by=owner,
            amount=Decimal("1200.00"),
            payment_method="cash",
        )

        self.client.force_login(owner)
        response = self.client.post(
            reverse("daily_summary"),
            {
                "action": "update_cash_count",
                "opening_cash_amount": "25000.00",
                "cash_expenditure_amount": "8000.00",
                "cash_expenditure_party_name": "Office Depot Jamaica",
                "cash_expenditure_currency": "JMD",
                "cash_expenditure_receipt_reference": "EXP-2026-004",
                "cash_expenditure_notes": "Office supplies and courier",
                "cash_lodgement_amount": "18000.00",
                "cash_lodgement_notes": "Cash lodged to NCB",
                "qty_5000": "0",
                "qty_2000": "0",
                "qty_1000": "0",
                "qty_500": "0",
                "qty_100": "2",
                "qty_50": "0",
                "qty_20": "0",
                "qty_10": "0",
                "qty_5": "0",
                "qty_1": "0",
                "ncb_machine_amount": "0.00",
                "bns_machine_amount": "0.00",
            },
        )

        self.assertEqual(response.status_code, 302)
        cash_count = DailyCashCount.objects.get(owner=owner, summary_date=selected_date)
        self.assertEqual(cash_count.opening_cash_amount, Decimal("25000.00"))
        self.assertEqual(cash_count.cash_expenditure_amount, Decimal("8000.00"))
        self.assertEqual(cash_count.cash_lodgement_amount, Decimal("18000.00"))
        self.assertEqual(cash_count.cash_expenditure_party_name, "Office Depot Jamaica")
        self.assertEqual(cash_count.cash_expenditure_currency, "JMD")
        self.assertEqual(cash_count.cash_expenditure_receipt_reference, "EXP-2026-004")
        self.assertEqual(cash_count.cash_expenditure_notes, "Office supplies and courier")
        self.assertEqual(cash_count.cash_lodgement_notes, "Cash lodged to NCB")

        page = self.client.get(reverse("daily_summary"))
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.context["opening_cash_amount"], Decimal("25000.00"))
        self.assertEqual(page.context["cash_expenditure_amount"], Decimal("8000.00"))
        self.assertEqual(page.context["cash_lodgement_amount"], Decimal("18000.00"))
        self.assertEqual(page.context["expected_cash"], Decimal("200.00"))
        self.assertContains(page, "Office Depot Jamaica")
        self.assertContains(page, "EXP-2026-004")
        self.assertContains(page, "JMD")
        self.assertContains(page, "Cash Expenditure")
        self.assertContains(page, "Cash Lodgement")
        self.assertContains(page, "Office supplies and courier")
        self.assertContains(page, "Cash lodged to NCB")

    def test_daily_summary_updates_payment_receipt_number(self):
        owner = self._make_user("owner-receipt-edit")
        location = self._make_location(owner, "Main Branch")
        invoice = SalesInvoice.objects.create(
            owner=owner,
            customer=None,
            location=location,
            subtotal=Decimal("500.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("500.00"),
        )
        payment = SalesInvoicePayment.objects.create(
            invoice=invoice,
            received_by=owner,
            amount=Decimal("500.00"),
            payment_method="cash",
            reference="OLD-1",
        )

        self.client.force_login(owner)
        response = self.client.post(
            reverse("daily_summary"),
            {
                "action": "update_receipt_no",
                "row_type": "payment",
                "row_id": str(payment.id),
                "receipt_no": "RCPT-2026-0007",
            },
        )

        self.assertEqual(response.status_code, 302)
        payment.refresh_from_db()
        self.assertEqual(payment.reference, "RCPT-2026-0007")

        page = self.client.get(reverse("daily_summary"))
        self.assertContains(page, "RCPT-2026-0007")

    def test_daily_summary_edits_are_scoped_before_record_lookup(self):
        owner = self._make_user("owner-daily-summary-scope")
        other_owner = self._make_user("other-daily-summary-scope")
        foreign_location = self._make_location(other_owner, "Private Branch")
        foreign_invoice = SalesInvoice.objects.create(
            owner=other_owner,
            customer=None,
            location=foreign_location,
            subtotal=Decimal("500.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("500.00"),
            notes="Private note",
        )
        foreign_payment = SalesInvoicePayment.objects.create(
            invoice=foreign_invoice,
            received_by=other_owner,
            amount=Decimal("500.00"),
            payment_method="cash",
            reference="PRIVATE-RECEIPT",
        )

        self.client.force_login(owner)
        comment_response = self.client.post(
            reverse("daily_summary"),
            {"row_type": "invoice", "row_id": foreign_invoice.id, "comment": "Changed"},
            follow=True,
        )
        receipt_response = self.client.post(
            reverse("daily_summary"),
            {
                "action": "update_receipt_no",
                "row_type": "payment",
                "row_id": foreign_payment.id,
                "receipt_no": "CHANGED",
            },
            follow=True,
        )

        foreign_invoice.refresh_from_db()
        foreign_payment.refresh_from_db()
        self.assertEqual(comment_response.status_code, 200)
        self.assertEqual(receipt_response.status_code, 200)
        self.assertEqual(foreign_invoice.notes, "Private note")
        self.assertEqual(foreign_payment.reference, "PRIVATE-RECEIPT")

    def test_cash_reconciliation_updates_cash_count(self):
        owner = self._make_user("owner-recon-edit")
        location = self._make_location(owner, "Main Branch")
        selected_date = timezone.localdate()
        invoice = SalesInvoice.objects.create(
            owner=owner,
            customer=None,
            location=location,
            subtotal=Decimal("900.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("900.00"),
        )
        SalesInvoicePayment.objects.create(
            invoice=invoice,
            received_by=owner,
            amount=Decimal("900.00"),
            payment_method="cash",
        )

        self.client.force_login(owner)
        response = self.client.post(
            reverse("cash_reconciliation"),
            {
                "action": "update_cash_count",
                "qty_5000": "0",
                "qty_2000": "0",
                "qty_1000": "0",
                "qty_500": "1",
                "qty_100": "4",
                "qty_50": "0",
                "qty_20": "0",
                "qty_10": "0",
                "qty_5": "0",
                "qty_1": "0",
                "ncb_machine_amount": "125.25",
                "bns_machine_amount": "300.00",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("cash_reconciliation"), response["Location"])
        cash_count = DailyCashCount.objects.get(owner=owner, summary_date=selected_date)
        self.assertEqual(cash_count.qty_500, 1)
        self.assertEqual(cash_count.qty_100, 4)
        self.assertEqual(cash_count.actual_cash, Decimal("900.00"))
        self.assertEqual(cash_count.ncb_machine_amount, Decimal("125.25"))
        self.assertEqual(cash_count.bns_machine_amount, Decimal("300.00"))

        page = self.client.get(reverse("cash_reconciliation"))
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.context["actual_cash"], Decimal("900.00"))
        self.assertEqual(page.context["actual_card_amount"], Decimal("425.25"))
        self.assertEqual(page.context["actual_reconciliation_total"], Decimal("1325.25"))

    def test_daily_summary_hides_cash_register_metadata_and_shows_payment_channel_label(self):
        owner = self._make_user("owner-daily-summary-meta")
        location = self._make_location(owner, "Main Branch")
        invoice = SalesInvoice.objects.create(
            owner=owner,
            customer=None,
            location=location,
            subtotal=Decimal("500.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("500.00"),
            notes=_compose_document_notes(
                "",
                {
                    "source": "cash_register",
                    "payment_channel": "wipay2me",
                    "discount_type": "flat",
                    "discount_value": "0.00",
                    "discount_amount": "0.00",
                },
            ),
        )

        self.client.force_login(owner)
        page = self.client.get(reverse("daily_summary"))

        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "WiPay")
        self.assertNotContains(page, DOCUMENT_META_PREFIX)
        self.assertNotContains(page, "Created from POS payment channel.")

    def test_daily_summary_comment_save_preserves_hidden_invoice_metadata(self):
        owner = self._make_user("owner-daily-summary-comment")
        location = self._make_location(owner, "Main Branch")
        invoice = SalesInvoice.objects.create(
            owner=owner,
            customer=None,
            location=location,
            subtotal=Decimal("500.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("500.00"),
            notes=_compose_document_notes(
                "",
                {
                    "source": "cash_register",
                    "payment_channel": "invoice",
                    "discount_type": "flat",
                    "discount_value": "0.00",
                    "discount_amount": "0.00",
                },
            ),
        )

        self.client.force_login(owner)
        response = self.client.post(
            reverse("daily_summary"),
            {
                "row_type": "invoice",
                "row_id": str(invoice.id),
                "comment": "All Collected",
            },
        )

        self.assertEqual(response.status_code, 302)
        invoice.refresh_from_db()
        self.assertIn(DOCUMENT_META_PREFIX, invoice.notes)
        self.assertTrue(invoice.notes.endswith("All Collected"))

        page = self.client.get(reverse("daily_summary"))
        self.assertContains(page, "All Collected")
        self.assertNotContains(page, DOCUMENT_META_PREFIX)
        self.assertNotContains(
            page,
            '<textarea name="comment" rows="2" placeholder="Add comment...">All Collected</textarea>',
            html=True,
        )

    def test_daily_summary_ledger_shows_discount_amount_and_tcost_columns(self):
        owner = self._make_user("owner-daily-summary-discount")
        location = self._make_location(owner, "Main Branch")
        customer = Customer.objects.create(owner=owner, name="Discount Customer")
        invoice = SalesInvoice.objects.create(
            owner=owner,
            customer=customer,
            location=location,
            subtotal=Decimal("500.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("450.00"),
            notes=_compose_document_notes(
                "",
                {
                    "discount_type": "flat",
                    "discount_value": "50.00",
                    "discount_amount": "50.00",
                },
            ),
        )
        payment = SalesInvoicePayment.objects.create(
            invoice=invoice,
            received_by=owner,
            amount=Decimal("200.00"),
            payment_method="cash",
            reference="RCPT-1",
        )

        self.client.force_login(owner)
        page = self.client.get(reverse("daily_summary"))
        content = page.content.decode()

        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "<th class=\"text-right\">Cost</th>", html=True)
        self.assertContains(page, "<th class=\"text-right\">Discount</th>", html=True)
        self.assertContains(page, "<th class=\"text-right\">Amount</th>", html=True)
        self.assertLess(content.index(">Cost<"), content.index(">Discount<"))
        self.assertLess(content.index(">Discount<"), content.index(">Amount<"))
        self.assertEqual(page.context["amount_total"], Decimal("200.00"))
        self.assertEqual(page.context["discount_total"], Decimal("50.00"))
        self.assertEqual(page.context["tcost_total"], Decimal("500.00"))
        payment_row = next(row for row in page.context["daily_activity_rows"] if row["row_type"] == "payment" and row["row_id"] == payment.id)
        invoice_row = next(row for row in page.context["daily_activity_rows"] if row["row_type"] == "invoice" and row["row_id"] == invoice.id)
        self.assertEqual(payment_row["amount"], Decimal("200.00"))
        self.assertEqual(payment_row["tcost"], Decimal("0.00"))
        self.assertEqual(invoice_row["discount"], Decimal("50.00"))
        self.assertEqual(invoice_row["amount"], Decimal("0.00"))
        self.assertEqual(invoice_row["tcost"], Decimal("500.00"))
        self.assertContains(page, "$200.00")
        self.assertContains(page, "$50.00")
        self.assertContains(page, "$500.00")

    def test_daily_summary_uses_live_snapshot_metrics_without_placeholder_defaults(self):
        owner = self._make_user("owner-daily-summary-live")
        location = self._make_location(owner, "Main Branch")
        owner.profile.default_location = location
        owner.profile.save(update_fields=["default_location"])

        invoice = SalesInvoice.objects.create(
            owner=owner,
            customer=None,
            location=location,
            subtotal=Decimal("800.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("800.00"),
        )
        SalesInvoicePayment.objects.create(
            invoice=invoice,
            received_by=owner,
            amount=Decimal("300.00"),
            payment_method="cash",
        )
        SalesQuotation.objects.create(
            owner=owner,
            created_by=owner,
            subtotal=Decimal("150.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("150.00"),
            valid_until=timezone.localdate() + timedelta(days=14),
        )

        self.client.force_login(owner)
        page = self.client.get(reverse("daily_summary"))

        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.context["branch_name"], "Main Branch")
        self.assertEqual(page.context["payments_count"], 1)
        self.assertEqual(page.context["open_invoices_count"], 1)
        self.assertEqual(page.context["daily_activity_value_total"], Decimal("950.00"))
        self.assertContains(page, "Location Scope: Main Branch")
        self.assertContains(page, "$950.00")
        self.assertNotContains(page, "$132,500")
        self.assertNotContains(page, "$7,500")

    def test_inventory_page_shows_inventory_operations_dashboard_sections(self):
        owner = self._make_user("owner-inventory-movements")
        warehouse = self._make_location(owner, "Main Warehouse")
        store = self._make_location(owner, "Store")
        supplier = Supplier.objects.create(owner=owner, name="Solar Source JA")
        category = Category.objects.create(name="Panels")
        brand = Brand.objects.create(name="QuickVolt")
        item = Item.objects.create(
            owner=owner,
            name="550W Solar Panel",
            sku="SKU-MOVE",
            category=category,
            brand=brand,
            cost_price=Decimal("34000.00"),
            price=Decimal("50000.00"),
        )
        StockRecord.objects.create(item=item, location=store, quantity=18)
        PurchaseOrder.objects.create(
            item=item,
            supplier=supplier,
            location=warehouse,
            quantity_received=12,
            unit_cost=Decimal("34000.00"),
        )
        StockTransfer.objects.create(
            item=item,
            from_location=warehouse,
            to_location=store,
            quantity=4,
            status="COMPLETED",
            user=owner,
        )
        customer = Customer.objects.create(owner=owner, name="John Brown")
        invoice = SalesInvoice.objects.create(
            owner=owner,
            customer=customer,
            location=store,
            status="paid",
            collection_status="collected_after_hold",
            collection_status_changed_at=timezone.now(),
            collected_at=timezone.now(),
            collection_recorded_by=owner,
            subtotal=Decimal("100000.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("100000.00"),
            issued_at=timezone.now() - timedelta(days=2),
        )
        SalesInvoiceItem.objects.create(
            invoice=invoice,
            item=item,
            item_name=item.name,
            quantity=1,
            unit_price=Decimal("50000.00"),
        )
        SalesInvoicePayment.objects.create(
            invoice=invoice,
            received_by=owner,
            amount=Decimal("100000.00"),
            payment_method="cash",
            payment_date=timezone.now(),
            reference="RCPT-MOVE-1",
        )

        self.client.force_login(owner)
        response = self.client.get(reverse("inventory_overview"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Inventory Operations Dashboard")
        self.assertContains(response, "Locally Received Goods")
        self.assertContains(response, "Imported Goods")
        self.assertContains(response, "Transfer In")
        self.assertContains(response, "Transfer Out")
        self.assertContains(response, "Delivered From Uncollected")
        self.assertContains(response, "Remaining In Store")
        self.assertContains(response, "Collected")
        self.assertContains(response, "Panels")
        self.assertContains(response, "550W Solar Panel")
        self.assertContains(response, "PO-")
        self.assertContains(response, "Main Warehouse")
        self.assertContains(response, "Store")
        self.assertContains(response, invoice.invoice_no)
        self.assertContains(response, "Picked up after hold")

        inventory_response = self.client.get(reverse("inventory"))
        self.assertEqual(inventory_response.status_code, 200)
        self.assertNotContains(inventory_response, "Inventory Operations Dashboard")

    def test_global_search_groups_business_objects_instead_of_page_buckets(self):
        owner = self._make_user("owner-business-search")
        warehouse = self._make_location(owner, "Warehouse A")
        store = self._make_location(owner, "Store")
        customer = Customer.objects.create(owner=owner, name="Battery Buyer Ltd", email="buyer@example.com")
        supplier = Supplier.objects.create(owner=owner, name="Battery Import Hub")
        category = Category.objects.create(name="Storage")
        brand = Brand.objects.create(name="VoltMax")
        item = Item.objects.create(
            owner=owner,
            name="Lithium Battery 5kWh",
            sku="BAT-5KWH",
            category=category,
            brand=brand,
            cost_price=Decimal("120000.00"),
            price=Decimal("155000.00"),
        )
        StockRecord.objects.create(item=item, location=warehouse, quantity=12)
        PurchaseOrder.objects.create(
            item=item,
            supplier=supplier,
            location=warehouse,
            quantity_received=6,
            unit_cost=Decimal("110000.00"),
        )
        StockTransfer.objects.create(
            item=item,
            from_location=warehouse,
            to_location=store,
            quantity=2,
            status="COMPLETED",
            user=owner,
        )

        self.client.force_login(owner)
        response = self.client.get(reverse("global_search"), {"q": "Battery"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Search The Business")
        self.assertContains(response, "Products")
        self.assertContains(response, "Customers")
        self.assertContains(response, "Suppliers")
        self.assertContains(response, "Purchase Orders")
        self.assertContains(response, "Inventory Movements")
        self.assertContains(response, "Stock Receipts")
        self.assertContains(response, "Lithium Battery 5kWh")
        self.assertContains(response, "Battery Buyer Ltd")
        self.assertContains(response, "Battery Import Hub")

        api_response = self.client.get(reverse("global_search_panel_api"), {"q": "Battery"})
        self.assertEqual(api_response.status_code, 200)
        payload = api_response.json()
        section_titles = [section["title"] for section in payload["sections"]]
        self.assertIn("Products", section_titles)
        self.assertIn("Purchase Orders", section_titles)
        self.assertIn("Inventory Movements", section_titles)

    def test_sales_invoice_list_exposes_summary_metrics(self):
        owner = self._make_user("owner-invoice-summary")
        location = self._make_location(owner, "Main Branch")
        quotation = SalesQuotation.objects.create(
            owner=owner,
            created_by=owner,
            subtotal=Decimal("200.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("200.00"),
        )
        paid_invoice = SalesInvoice.objects.create(
            owner=owner,
            created_by=owner,
            quotation=quotation,
            location=location,
            status="paid",
            subtotal=Decimal("200.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("200.00"),
        )
        SalesInvoicePayment.objects.create(
            invoice=paid_invoice,
            received_by=owner,
            amount=Decimal("200.00"),
            payment_method="cash",
        )
        issued_invoice = SalesInvoice.objects.create(
            owner=owner,
            created_by=owner,
            location=location,
            status="issued",
            subtotal=Decimal("400.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("400.00"),
        )

        self.client.force_login(owner)
        response = self.client.get(reverse("sales_invoice_list"))

        self.assertEqual(response.status_code, 200)
        summary = response.context["invoice_summary"]
        self.assertEqual(summary["visible_count"], 2)
        self.assertEqual(summary["quotation_backed_count"], 1)
        self.assertEqual(summary["direct_count"], 1)
        self.assertEqual(summary["paid_count"], 1)
        self.assertEqual(summary["issued_count"], 1)
        self.assertEqual(summary["paid_total"], Decimal("200.00"))
        self.assertEqual(summary["outstanding_total"], Decimal("400.00"))
        self.assertContains(response, "From quotations")
        self.assertContains(response, "$400.00")
        self.assertContains(response, 'data-sales-document-list data-document-label="invoice"')
        self.assertContains(response, 'data-sales-select-all')
        self.assertContains(response, 'data-sales-bulk-action="email"')
        self.assertContains(response, 'class="action-link-premium sales-row-primary-action"')
        self.assertContains(response, 'data-sales-menu-toggle')
        self.assertContains(response, "Record Payment")

    def test_sales_quotation_list_exposes_summary_metrics(self):
        owner = self._make_user("owner-quotation-summary")
        SalesQuotation.objects.create(
            owner=owner,
            created_by=owner,
            status="draft",
            subtotal=Decimal("120.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("120.00"),
            valid_until=timezone.localdate() + timedelta(days=3),
        )
        SalesQuotation.objects.create(
            owner=owner,
            created_by=owner,
            status="converted",
            subtotal=Decimal("80.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("80.00"),
            valid_until=timezone.localdate() + timedelta(days=20),
        )
        SalesQuotation.objects.create(
            owner=owner,
            created_by=owner,
            status="cancelled",
            subtotal=Decimal("40.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("40.00"),
        )

        self.client.force_login(owner)
        response = self.client.get(reverse("sales_quotation_list"))

        self.assertEqual(response.status_code, 200)
        summary = response.context["quotation_summary"]
        self.assertEqual(summary["visible_count"], 3)
        self.assertEqual(summary["draft_count"], 1)
        self.assertEqual(summary["converted_count"], 1)
        self.assertEqual(summary["cancelled_count"], 1)
        self.assertEqual(summary["expired_count"], 0)
        self.assertEqual(summary["expiring_soon_count"], 1)
        self.assertEqual(summary["quoted_total"], Decimal("240.00"))
        self.assertContains(response, "Expiring in 7 days")
        self.assertContains(response, "Expired")
        self.assertContains(response, "$240.00")
        self.assertContains(response, 'data-sales-document-list data-document-label="quotation"')
        self.assertContains(response, 'data-sales-select-all')
        self.assertContains(response, 'data-sales-bulk-action="csv"')
        self.assertContains(response, 'class="action-link-premium sales-row-primary-action"')
        self.assertContains(response, 'data-sales-menu-toggle')
        self.assertContains(response, '<option value="expired" ')
        rendered = response.content.decode()
        self.assertNotIn("Expiring in 7 days:</span>", rendered)
        self.assertNotIn("Total:</span>", rendered)
        self.assertContains(response, 'class="summary-grid sales-kpi-grid"')

    def test_sales_dropdown_pages_render_the_sales_desk_controls(self):
        owner = self._make_user("owner-sales-desk-pages")
        location = self._make_location(owner, "Sales Counter")
        owner.profile.default_location = location
        owner.profile.save(update_fields=["default_location"])
        quotation = SalesQuotation.objects.create(
            owner=owner,
            created_by=owner,
            status="draft",
            subtotal=Decimal("100.00"),
            total_amount=Decimal("100.00"),
        )
        invoice = SalesInvoice.objects.create(
            owner=owner,
            created_by=owner,
            quotation=quotation,
            location=location,
            status="issued",
            subtotal=Decimal("100.00"),
            total_amount=Decimal("100.00"),
        )

        self.client.force_login(owner)
        pages = {
            reverse("sales_quotation_list"): "Quotation desk",
            reverse("sales_quotation_create"): "Quotation studio",
            reverse("sales_quotation_detail", args=[quotation.id]): "Quotation record",
            reverse("sales_invoice_list"): "Sales desk",
            f'{reverse("sales_invoice_create")}?source=scratch': "Invoice workspace",
            reverse("sales_invoice_detail", args=[invoice.id]): "Invoice record",
            reverse("sales_invoice_payment", args=[invoice.id]): "Collection desk",
        }

        for url, expected_kicker in pages.items():
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, expected_kicker)
                self.assertEqual(response.content.decode().count("<h1"), 1)
                self.assertContains(response, reverse("sales"))
                self.assertContains(response, reverse("sales_quotation_list"))
                self.assertContains(response, reverse("sales_invoice_list"))

        quotation_form = self.client.get(reverse("sales_quotation_create"))
        self.assertContains(quotation_form, 'id="addQuoteLine"')
        self.assertContains(quotation_form, 'name="quantity[]"')
        quotation_detail = self.client.get(reverse("sales_quotation_detail", args=[quotation.id]))
        self.assertContains(quotation_detail, reverse("sales_quotation_convert_to_invoice", args=[quotation.id]))
        self.assertContains(quotation_detail, "Convert Options")
        self.assertContains(quotation_detail, "Print Quotation")
        quotation.status = "converted"
        quotation.save(update_fields=["status"])
        converted_quotation_detail = self.client.get(reverse("sales_quotation_detail", args=[quotation.id]))
        self.assertContains(converted_quotation_detail, "Back To Invoice")
        self.assertContains(converted_quotation_detail, "Print Quotation")

        invoice_form = self.client.get(f'{reverse("sales_invoice_create")}?source=scratch')
        self.assertContains(invoice_form, 'id="salesInvoiceForm"')
        self.assertContains(invoice_form, 'id="invoiceSourceMode"')
        self.assertContains(invoice_form, 'class="qs-btn-toggle invoice-mode-btn')
        payment_page = self.client.get(reverse("sales_invoice_payment", args=[invoice.id]))
        self.assertContains(payment_page, 'name="payment_method"')
        self.assertContains(payment_page, 'id="payment-amount-input"')
        invoice_detail = self.client.get(reverse("sales_invoice_detail", args=[invoice.id]))
        self.assertContains(invoice_detail, 'class="footer-meta sales-document-footer-meta"')
        self.assertContains(invoice_detail, "Print")
        self.assertNotContains(invoice_detail, "border-top: 100px")

        CashShift.objects.create(cashier=owner, location=location, opening_cash=Decimal("100.00"))
        cash_register = self.client.get(reverse("sales"), follow=True)
        self.assertEqual(cash_register.status_code, 200)
        self.assertContains(cash_register, 'id="quickstockCashRegisterConfig"')
        self.assertContains(cash_register, 'id="barcode-input"')
        self.assertContains(cash_register, 'class="cash-register-command-bar"')
        self.assertContains(cash_register, 'class="logout-link"')

    def test_supplier_directory_has_one_non_overlapping_semantic_title(self):
        owner = self._make_user("owner-supplier-title")
        self.client.force_login(owner)

        response = self.client.get(reverse("supplier_list"))

        self.assertEqual(response.status_code, 200)
        rendered = response.content.decode()
        self.assertEqual(rendered.count("<h1"), 1)
        self.assertIn('id="supplier-directory-title"', rendered)
        self.assertIn('class="ops-hero ops-hero-suppliers supplier-directory-hero"', rendered)

    def test_operations_pages_render_for_manager_without_redirect_loop(self):
        owner = self._make_user("owner-operations-pages")
        location = self._make_location(owner, "Main Branch")
        manager = self._make_staff_user("manager-operations-pages", owner, role="manager", default_location=location)

        self.client.force_login(manager)

        operations_response = self.client.get(reverse("operations"))
        reconciliation_response = self.client.get(reverse("cash_reconciliation"))
        deliveries_response = self.client.get(reverse("deliveries_collections"))

        self.assertEqual(operations_response.status_code, 200)
        self.assertEqual(reconciliation_response.status_code, 200)
        self.assertEqual(deliveries_response.status_code, 200)
        self.assertContains(operations_response, "Operations Center")
        self.assertContains(reconciliation_response, "Cash Reconciliation")
        self.assertContains(deliveries_response, "Deliveries & Collections")
        self.assertNotContains(reconciliation_response, "$125,000")
        self.assertNotContains(reconciliation_response, "$124,500")

    def test_operations_hub_shows_live_closeout_status_and_wizard_links(self):
        owner = self._make_user("owner-closeout-status")
        location = self._make_location(owner, "Main Branch")
        shift = CashShift.objects.create(
            owner=owner,
            cashier=owner,
            location=location,
            opening_cash=Decimal("100.00"),
            is_closed=False,
        )
        self.client.force_login(owner)

        response = self.client.get(reverse("operations"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No Activity Yet")
        self.assertContains(response, "Drawer Balanced")
        self.assertContains(response, "All Collections Cleared")
        self.assertContains(response, "1 Open Shift")
        self.assertContains(response, "closeout=1")
        self.assertContains(response, "Start Closeout")

    def test_closeout_wizard_links_summary_reconciliation_and_deliveries(self):
        owner = self._make_user("owner-closeout-wizard")
        location = self._make_location(owner, "Main Branch")
        shift = CashShift.objects.create(
            owner=owner,
            cashier=owner,
            location=location,
            opening_cash=Decimal("100.00"),
            is_closed=False,
        )
        self.client.force_login(owner)

        summary_response = self.client.get(reverse("daily_summary"), {"closeout": "1"})
        reconciliation_response = self.client.get(reverse("cash_reconciliation"), {"closeout": "1"})
        deliveries_response = self.client.get(reverse("deliveries_collections"), {"closeout": "1"})

        self.assertEqual(summary_response.status_code, 200)
        self.assertEqual(reconciliation_response.status_code, 200)
        self.assertEqual(deliveries_response.status_code, 200)
        self.assertContains(summary_response, "Next: Reconcile Cash")
        self.assertContains(reconciliation_response, "Next: Deliveries &amp; Collections")
        self.assertContains(deliveries_response, "Finish Closeout")
        self.assertContains(summary_response, 'name="closeout" value="1"')
        self.assertContains(reconciliation_response, 'name="closeout" value="1"')
        self.assertContains(deliveries_response, 'name="closeout" value="1"')

        close_response = self.client.post(
            reverse("cash_reconciliation"),
            {
                "action": "close_shift",
                "shift_id": shift.id,
                "counted_cash": "100.00",
                "closeout": "1",
            },
        )
        self.assertEqual(close_response.status_code, 302)
        self.assertIn(reverse("deliveries_collections"), close_response["Location"])
        self.assertIn("closeout=1", close_response["Location"])

    def test_inventory_delete_archives_item_when_invoice_history_exists(self):
        owner = self._make_user("owner-protected-delete")
        location = self._make_location(owner, "Main Branch")
        category = Category.objects.create(name="Protected Delete Category")
        brand = Brand.objects.create(name="Protected Delete Brand")
        item = Item.objects.create(
            owner=owner,
            name="Protected Item",
            sku="PROTECT-1",
            category=category,
            brand=brand,
            cost_price=Decimal("50.00"),
            price=Decimal("100.00"),
        )
        StockRecord.objects.create(item=item, location=location, quantity=9)
        invoice = SalesInvoice.objects.create(
            owner=owner,
            customer=None,
            location=location,
            subtotal=Decimal("100.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("100.00"),
        )
        SalesInvoiceItem.objects.create(
            invoice=invoice,
            item=item,
            quantity=1,
            unit_price=Decimal("100.00"),
        )

        self.client.force_login(owner)
        response = self.client.post(reverse("inventory_delete", args=[item.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("inventory"))
        item.refresh_from_db()
        self.assertEqual(item.status, "archived")
        self.assertTrue(item.is_deleted)
        self.assertEqual(item.quantity, 0)
        self.assertFalse(StockRecord.objects.filter(item=item).exists())

    @override_settings(JAMDEX_CALLBACK_SECRET="super-secret")
    def test_jamdex_callback_rejects_invalid_secret(self):
        owner = self._make_user("owner-a")
        location = self._make_location(owner, "Main Branch")
        sale = Sale.objects.create(owner=owner, cashier=owner, location=location, total_price="100.00")

        response = self.client.post(
            reverse("jamdex_callback"),
            data='{"reference": "%s", "status": "paid"}' % sale.pk,
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)
        sale.refresh_from_db()
        self.assertEqual(sale.tender, "cash")

    @override_settings(JAMDEX_CALLBACK_SECRET="super-secret")
    def test_jamdex_callback_accepts_valid_secret_and_updates_sale(self):
        owner = self._make_user("owner-a")
        location = self._make_location(owner, "Main Branch")
        sale = Sale.objects.create(owner=owner, cashier=owner, location=location, total_price="100.00")

        response = self.client.post(
            f"{reverse('jamdex_callback')}?token=super-secret",
            data='{"reference": "%s", "status": "paid", "txid": "abc123"}' % sale.pk,
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(response.content, {"success": True})
        sale.refresh_from_db()
        self.assertEqual(sale.tender, "jamdex")

    @override_settings(JAMDEX_CALLBACK_SECRET="super-secret")
    def test_jamdex_numeric_callback_rejects_cross_tenant_receipt_collision(self):
        owner_a = self._make_user("owner-jamdex-id")
        owner_b = self._make_user("owner-jamdex-receipt")
        location_a = self._make_location(owner_a, "Owner A Branch")
        location_b = self._make_location(owner_b, "Owner B Branch")
        receipt_collision = Sale.objects.create(
            owner=owner_b,
            cashier=owner_b,
            location=location_b,
            total_price="100.00",
        )
        intended_sale = Sale.objects.create(
            owner=owner_a,
            cashier=owner_a,
            location=location_a,
            total_price="100.00",
        )
        receipt_collision.receipt_no = intended_sale.id
        receipt_collision.save(update_fields=["receipt_no", "last_modified"], skip_validation=True)

        response = self.client.post(
            f"{reverse('jamdex_callback')}?token=super-secret",
            data=json.dumps(
                {"reference": str(intended_sale.id), "status": "paid", "amount": "100.00"}
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 409)
        intended_sale.refresh_from_db()
        receipt_collision.refresh_from_db()
        self.assertEqual(intended_sale.tender, "cash")
        self.assertEqual(receipt_collision.tender, "cash")

    @override_settings(JAMDEX_CALLBACK_SECRET="super-secret")
    def test_jamdex_failed_callback_does_not_mark_sale_as_jamdex(self):
        owner = self._make_user("owner-jamdex-failed")
        location = self._make_location(owner, "Main Branch")
        sale = Sale.objects.create(owner=owner, cashier=owner, location=location, total_price="100.00")

        response = self.client.post(
            f"{reverse('jamdex_callback')}?token=super-secret",
            data=json.dumps({"reference": str(sale.id), "status": "failed"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        sale.refresh_from_db()
        self.assertEqual(sale.tender, "cash")

    @override_settings(
        JAMDEX_API_URL="https://provider.example/pay",
        JAMDEX_MERCHANT_ID="merchant-1",
        JAMDEX_API_KEY="api-key",
    )
    @patch("inventory.views.requests.post")
    def test_jamdex_sale_checkout_persists_server_generated_reference(self, mock_post):
        owner = self._make_user("owner-jamdex-init")
        location = self._make_location(owner, "Main Branch")
        sale = Sale.objects.create(owner=owner, cashier=owner, location=location, total_price="100.00")
        mock_post.return_value.raise_for_status.return_value = None
        mock_post.return_value.json.return_value = {"status": "created"}

        self.client.force_login(owner)
        response = self.client.post(
            reverse("jamdex_checkout"),
            data=json.dumps(
                {"sale_id": sale.id, "total": "1.00", "reference": "CLIENT-CONTROLLED"}
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        sale.refresh_from_db()
        self.assertTrue(sale.payment_reference.startswith(f"JD-SALE-{sale.id}-"))
        self.assertEqual(response.json()["reference"], sale.payment_reference)
        self.assertNotEqual(sale.payment_reference, "CLIENT-CONTROLLED")

    @override_settings(JAMDEX_CALLBACK_SECRET="super-secret")
    def test_jamdex_callback_rejects_amount_mismatch(self):
        owner = self._make_user("owner-jamdex-mismatch")
        location = self._make_location(owner, "Main Branch")
        sale = Sale.objects.create(owner=owner, cashier=owner, location=location, total_price="100.00")

        response = self.client.post(
            f"{reverse('jamdex_callback')}?token=super-secret",
            data='{"reference": "%s", "status": "paid", "amount": "25.00"}' % sale.pk,
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        sale.refresh_from_db()
        self.assertEqual(sale.tender, "cash")

    def test_upgrade_success_requires_verified_paid_payment(self):
        owner = self._make_user("owner-upgrade-verify")
        profile = UserProfile.for_user(owner)
        profile.plan = "TRIAL"
        profile.plan_end = timezone.now() + timedelta(days=7)
        profile.pro_expires = None
        profile.save(update_fields=["plan", "plan_end", "pro_expires"])
        Payment.objects.create(
            user=owner,
            order_id="PENDING-UPGRADE-1",
            amount=Decimal("2600.00"),
            status="pending",
        )

        self.client.force_login(owner)
        response = self.client.get(
            reverse("upgrade_success"),
            {"order_id": "PENDING-UPGRADE-1", "status": "success"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("upgrade"))
        profile.refresh_from_db()
        self.assertEqual(profile.plan, "TRIAL")
        self.assertIsNone(profile.pro_expires)

    def test_upgrade_success_accepts_legacy_wipay_callback_payload(self):
        owner = self._make_user("owner-legacy-wipay")
        profile = UserProfile.for_user(owner)
        profile.plan = "TRIAL"
        profile.status = "active"
        profile.plan_end = timezone.now() + timedelta(days=7)
        profile.pro_expires = None
        profile.save(update_fields=["plan", "status", "plan_end", "pro_expires"])
        payment = Payment.objects.create(
            user=owner,
            order_id="LEGACY-WIPAY-1",
            amount=Decimal("2600.00"),
            status="pending",
            response_payload={"billing_cycle": "monthly"},
        )

        self.client.force_login(owner)
        response = self.client.get(
            reverse("upgrade_success"),
            {"order_id": payment.order_id, "status": "success", "amount": "2600.00"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], f"{reverse('upgrade_success')}?order_id=LEGACY-WIPAY-1")
        payment.refresh_from_db()
        profile.refresh_from_db()
        self.assertEqual(payment.status, "paid")
        self.assertEqual(profile.plan, "PRO")
        self.assertEqual(profile.pro_expires, timezone.localdate() + timedelta(days=30))

    def test_wipay_response_matches_sandbox_wrapped_order_reference(self):
        owner = self._make_user("owner-wrapped-wipay")
        profile = UserProfile.for_user(owner)
        profile.plan = "TRIAL"
        profile.status = "active"
        profile.plan_end = timezone.now() + timedelta(days=7)
        profile.pro_expires = None
        profile.save(update_fields=["plan", "status", "plan_end", "pro_expires"])
        payment = Payment.objects.create(
            user=owner,
            order_id="QS-1-115b2e",
            amount=Decimal("30400.00"),
            status="pending",
            response_payload={"billing_cycle": "yearly"},
        )

        response = self.client.get(
            reverse("wipay_response"),
            {
                "order_id": "SB-99-1-QS-1-115b2e-20260824135458",
                "status": "success",
                "amount": "30400.00",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], f"{reverse('upgrade_success')}?order_id=QS-1-115b2e")
        payment.refresh_from_db()
        profile.refresh_from_db()
        self.assertEqual(payment.status, "paid")
        self.assertEqual(payment.response_payload["raw_order_id"], "SB-99-1-QS-1-115b2e-20260824135458")
        self.assertEqual(profile.plan, "PRO")
        self.assertEqual(profile.pro_expires, timezone.localdate() + timedelta(days=365))

    def test_wipay_response_accepts_completed_status_label(self):
        owner = self._make_user("owner-completed-wipay")
        profile = UserProfile.for_user(owner)
        profile.plan = "TRIAL"
        profile.status = "active"
        profile.plan_end = timezone.now() + timedelta(days=7)
        profile.pro_expires = None
        profile.save(update_fields=["plan", "status", "plan_end", "pro_expires"])
        payment = Payment.objects.create(
            user=owner,
            order_id="QS-1-complete",
            amount=Decimal("30400.00"),
            status="pending",
            response_payload={"billing_cycle": "yearly"},
        )

        response = self.client.get(
            reverse("wipay_response"),
            {
                "order_id": "SB-72-1-QS-1-complete-20260824141938",
                "status": "completed",
                "amount": "30400.00",
            },
        )

        self.assertEqual(response.status_code, 302)
        payment.refresh_from_db()
        profile.refresh_from_db()
        self.assertEqual(payment.status, "paid")
        self.assertEqual(profile.plan, "PRO")

    def test_wipay_response_accepts_success_phrase_and_comma_total(self):
        owner = self._make_user("owner-phrase-wipay")
        profile = UserProfile.for_user(owner)
        profile.plan = "TRIAL"
        profile.status = "active"
        profile.plan_end = timezone.now() + timedelta(days=7)
        profile.pro_expires = None
        profile.save(update_fields=["plan", "status", "plan_end", "pro_expires"])
        payment = Payment.objects.create(
            user=owner,
            order_id="QS-1-phrase",
            amount=Decimal("30400.00"),
            status="pending",
            response_payload={"billing_cycle": "yearly"},
        )

        with CaptureQueriesContext(connection) as captured_queries:
            response = self.client.get(
                reverse("wipay_response"),
                {
                    "order_id": "SB-72-1-QS-1-phrase-20260824141938",
                    "status": "Transaction Complete - Success",
                    "total": "30,400.00",
                },
            )

        self.assertEqual(response.status_code, 302)
        payment.refresh_from_db()
        profile.refresh_from_db()
        self.assertEqual(payment.status, "paid")
        self.assertEqual(profile.plan, "PRO")
        payment_selects = [
            query["sql"]
            for query in captured_queries.captured_queries
            if query["sql"].lstrip().upper().startswith("SELECT")
            and 'FROM "inventory_payment"' in query["sql"]
        ]
        self.assertTrue(payment_selects)
        self.assertFalse(
            any('JOIN "auth_user"' in sql for sql in payment_selects),
            "WiPay's payment row lock must not include the nullable user join.",
        )

    def test_wipay_response_creates_missing_profile_before_upgrade(self):
        owner = self._make_user("owner-missing-profile-wipay")
        owner.profile.delete()
        payment = Payment.objects.create(
            user=owner,
            order_id="QS-1-missingprofile",
            amount=Decimal("30400.00"),
            status="pending",
            response_payload={"billing_cycle": "yearly"},
        )

        response = self.client.get(
            reverse("wipay_response"),
            {
                "order_id": "SB-72-1-QS-1-missingprofile-20260824141938",
                "status": "Transaction Complete - Success",
                "total": "30,400.00",
            },
        )

        self.assertEqual(response.status_code, 302)
        payment.refresh_from_db()
        profile = UserProfile.objects.get(user=owner)
        self.assertEqual(payment.status, "paid")
        self.assertEqual(profile.plan, "PRO")

    @override_settings(
        WIPAY_ENVIRONMENT="live",
        WIPAY_ACCOUNT_NUMBER_LIVE="1234567890",
        WIPAY_API_KEY_LIVE="live-secret",
    )
    def test_wipay_live_config_accepts_sandbox_wrapped_reference_without_hash(self):
        owner = self._make_user("owner-live-config-sandbox-wipay")
        profile = UserProfile.for_user(owner)
        profile.plan = "TRIAL"
        profile.status = "active"
        profile.plan_end = timezone.now() + timedelta(days=7)
        profile.pro_expires = None
        profile.save(update_fields=["plan", "status", "plan_end", "pro_expires"])
        payment = Payment.objects.create(
            user=owner,
            order_id="QS-1-a0d8c9",
            amount=Decimal("30400.00"),
            status="pending",
            response_payload={"billing_cycle": "yearly"},
        )

        response = self.client.get(
            reverse("wipay_response"),
            {
                "order_id": "SB-72-1-QS-1-a0d8c9-20260824141938",
                "status": "success",
                "total": "30400.00",
            },
        )

        self.assertEqual(response.status_code, 302)
        payment.refresh_from_db()
        profile.refresh_from_db()
        self.assertEqual(payment.status, "paid")
        self.assertEqual(profile.plan, "PRO")

    @override_settings(
        WIPAY_ENVIRONMENT="live",
        WIPAY_ACCOUNT_NUMBER_LIVE="1234567890",
        WIPAY_API_KEY_LIVE="live-secret",
    )
    def test_wipay_live_success_uses_transaction_hash_contract(self):
        owner = self._make_user("owner-live-wipay")
        profile = UserProfile.for_user(owner)
        profile.plan = "TRIAL"
        profile.status = "active"
        profile.plan_end = timezone.now() + timedelta(days=7)
        profile.pro_expires = None
        profile.save(update_fields=["plan", "status", "plan_end", "pro_expires"])
        payment = Payment.objects.create(
            user=owner,
            order_id="LIVE-UPGRADE-1",
            amount=Decimal("30400.00"),
            status="pending",
            response_payload={"billing_cycle": "yearly"},
        )
        transaction_id = "TX-LIVE-123"
        response_hash = hashlib.md5(f"{transaction_id}30400.00live-secret".encode()).hexdigest()

        response = self.client.get(
            reverse("wipay_response"),
            {
                "order_id": payment.order_id,
                "status": "success",
                "transaction_id": transaction_id,
                "total": "30400.00",
                "hash": response_hash,
            },
        )

        self.assertEqual(response.status_code, 302)
        payment.refresh_from_db()
        profile.refresh_from_db()
        self.assertEqual(payment.status, "paid")
        self.assertEqual(profile.plan, "PRO")

    @override_settings(
        WIPAY_ENVIRONMENT="live",
        WIPAY_ACCOUNT_NUMBER_LIVE="1234567890",
        WIPAY_API_KEY_LIVE="live-secret",
    )
    def test_wipay_live_rejects_hash_from_order_id_contract(self):
        owner = self._make_user("owner-old-hash-wipay")
        profile = UserProfile.for_user(owner)
        profile.plan = "TRIAL"
        profile.status = "active"
        profile.plan_end = timezone.now() + timedelta(days=7)
        profile.pro_expires = None
        profile.save(update_fields=["plan", "status", "plan_end", "pro_expires"])
        payment = Payment.objects.create(
            user=owner,
            order_id="LIVE-UPGRADE-2",
            amount=Decimal("30400.00"),
            status="pending",
        )
        transaction_id = "TX-LIVE-456"
        wrong_hash = hashlib.md5(f"{payment.order_id}30400.00live-secret".encode()).hexdigest()

        response = self.client.get(
            reverse("wipay_response"),
            {
                "order_id": payment.order_id,
                "status": "success",
                "transaction_id": transaction_id,
                "total": "30400.00",
                "hash": wrong_hash,
            },
        )

        self.assertEqual(response.status_code, 302)
        payment.refresh_from_db()
        profile.refresh_from_db()
        self.assertEqual(payment.status, "failed")
        self.assertEqual(profile.plan, "TRIAL")

    def test_wipay_success_preserves_checkout_billing_cycle_and_reference_redirect(self):
        owner = self._make_user("owner-monthly-renewal")
        profile = UserProfile.for_user(owner)
        profile.plan = "TRIAL"
        profile.status = "active"
        profile.pro_expires = None
        profile.plan_end = timezone.now() + timedelta(days=3)
        profile.save(update_fields=["plan", "status", "pro_expires", "plan_end"])
        payment = Payment.objects.create(
            user=owner,
            order_id="MONTHLY-UPGRADE-1",
            amount=Decimal("190.00"),
            status="pending",
            response_payload={"billing_cycle": "monthly"},
        )

        response = self.client.get(
            reverse("wipay_response"),
            {
                "order_id": payment.order_id,
                "status": "success",
                "amount": "190.00",
                # A callback must not be able to upgrade the purchased term.
                "billing_cycle": "yearly",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"],
            f"{reverse('upgrade_success')}?order_id=MONTHLY-UPGRADE-1",
        )
        payment.refresh_from_db()
        profile.refresh_from_db()
        self.assertEqual(payment.status, "paid")
        self.assertEqual(payment.response_payload["billing_cycle"], "monthly")
        self.assertEqual(profile.plan, "PRO")
        self.assertEqual(profile.pro_expires, timezone.localdate() + timedelta(days=30))

    @override_settings(TIME_ZONE="America/Jamaica", USE_TZ=True)
    @patch("inventory.views.timezone.now")
    def test_wipay_success_uses_local_business_date_for_expiry(self, mock_now):
        mock_now.return_value = datetime(2026, 7, 24, 3, 30, tzinfo=dt_timezone.utc)
        owner = self._make_user("owner-wipay-boundary")
        UserProfile.for_user(owner)
        payment = Payment.objects.create(
            user=owner,
            order_id="BOUNDARY-UPGRADE-1",
            amount=Decimal("190.00"),
            status="pending",
            response_payload={"billing_cycle": "monthly"},
        )

        response = self.client.get(
            reverse("wipay_response"),
            {"order_id": payment.order_id, "status": "success", "amount": "190.00"},
        )

        self.assertEqual(response.status_code, 302)
        owner.profile.refresh_from_db()
        self.assertEqual(owner.profile.pro_expires, datetime(2026, 7, 23).date() + timedelta(days=30))

    def test_wipay_paid_callback_retry_keeps_payment_reference_in_redirect(self):
        owner = self._make_user("owner-paid-retry")
        payment = Payment.objects.create(
            user=owner,
            order_id="PAID-RETRY-1",
            amount=Decimal("190.00"),
            status="paid",
            response_payload={"billing_cycle": "monthly"},
        )

        response = self.client.get(
            reverse("wipay_response"),
            {"order_id": payment.order_id, "status": "success", "amount": "190.00"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"],
            f"{reverse('upgrade_success')}?order_id=PAID-RETRY-1",
        )

    def test_authenticated_pos_checkout_endpoints_require_csrf(self):
        owner = self._make_user("owner-pos-csrf")
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(owner)

        for route_name in ("jamdex_checkout", "card_checkout"):
            with self.subTest(route_name=route_name):
                response = csrf_client.post(
                    reverse(route_name),
                    data=json.dumps({"total": "100.00"}),
                    content_type="application/json",
                )
                self.assertEqual(response.status_code, 403)

    def test_wipay_failure_does_not_suspend_existing_paid_account(self):
        owner = self._make_user("owner-payment-failure")
        profile = UserProfile.for_user(owner)
        profile.plan = "PRO"
        profile.status = "active"
        profile.pro_expires = timezone.now().date() + timedelta(days=30)
        profile.plan_end = timezone.now() + timedelta(days=30)
        profile.save(update_fields=["plan", "status", "pro_expires", "plan_end"])
        owner.is_active = True
        owner.save(update_fields=["is_active"])
        Payment.objects.create(
            user=owner,
            order_id="FAILED-RENEWAL-1",
            amount=Decimal("2600.00"),
            status="pending",
        )

        response = self.client.get(
            reverse("wipay_response"),
            {"order_id": "FAILED-RENEWAL-1", "status": "failed", "amount": "2600.00"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("upgrade_cancel"))
        owner.refresh_from_db()
        profile.refresh_from_db()
        payment = Payment.objects.get(order_id="FAILED-RENEWAL-1")
        self.assertEqual(payment.status, "failed")
        self.assertTrue(owner.is_active)
        self.assertEqual(profile.status, "active")


class SuperAdminDashboardTests(TestCase):
    def _make_company_owner(self, username, plan="PRO", status="active", pro_days=30):
        user = User.objects.create_user(username=username, password="password123")
        profile = UserProfile.for_user(user)
        profile.role = "admin"
        profile.status = status
        profile.plan = plan
        profile.plan_start = timezone.now() - timedelta(days=5)
        profile.plan_end = timezone.now() + timedelta(days=pro_days)
        profile.pro_expires = timezone.now().date() + timedelta(days=pro_days)
        profile.parent_admin = None
        profile.save()
        return user

    def _make_location(self, owner, name):
        return Location.objects.create(owner=owner, name=name)

    def test_superuser_dashboard_lists_all_companies_and_metrics(self):
        superuser = User.objects.create_superuser(
            username="platform-owner",
            password="password123",
        )

        alpha = self._make_company_owner("alpha-co", plan="PRO", status="active", pro_days=10)
        beta = self._make_company_owner("beta-co", plan="TRIAL", status="suspended", pro_days=3)

        alpha_location = self._make_location(alpha, "Alpha HQ")
        beta_location = self._make_location(beta, "Beta HQ")

        category = Category.objects.create(name="Dash Category")
        brand = Brand.objects.create(name="Dash Brand")
        item = Item.objects.create(
            owner=alpha,
            name="Alpha Item",
            sku="ALPHA-1",
            category=category,
            brand=brand,
            cost_price=Decimal("10.00"),
            price=Decimal("25.00"),
        )
        StockRecord.objects.create(item=item, location=alpha_location, quantity=5)
        Sale.objects.create(
            owner=alpha,
            cashier=alpha,
            location=alpha_location,
            total_price=Decimal("25.00"),
        )
        Supplier.objects.create(owner=alpha, name="Alpha Vendor")
        SalesInvoice.objects.create(
            owner=alpha,
            created_by=alpha,
            location=alpha_location,
            total_amount=Decimal("25.00"),
        )
        SalesQuotation.objects.create(
            owner=alpha,
            created_by=alpha,
            total_amount=Decimal("30.00"),
        )
        staff = User.objects.create_user(username="alpha-staff", password="password123")
        staff_profile = UserProfile.for_user(staff)
        staff_profile.role = "manager"
        staff_profile.status = "active"
        staff_profile.plan = "PRO"
        staff_profile.parent_admin = UserProfile.for_user(alpha)
        staff_profile.save()

        self.client.force_login(superuser)
        response = self.client.get(reverse("super_admin_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_companies"], 2)
        self.assertEqual(response.context["active_companies"], 1)
        self.assertEqual(response.context["trial_companies"], 1)
        self.assertEqual(response.context["suspended_companies"], 1)
        self.assertEqual(response.context["total_staff"], 1)
        self.assertEqual(response.context["total_locations"], 2)
        self.assertEqual(response.context["total_items"], 1)
        self.assertEqual(response.context["total_suppliers"], 1)
        self.assertEqual(response.context["total_sales"], 1)
        self.assertEqual(response.context["total_invoices"], 1)
        self.assertEqual(response.context["total_quotations"], 1)
        self.assertEqual(response.context["attention_count"], 1)
        self.assertEqual(len(response.context["attention_companies"]), 1)
        self.assertContains(response, "alpha-co")
        self.assertContains(response, "beta-co")
        self.assertContains(response, "Attention Queue")
        self.assertContains(response, "Recent Tenant Activity")
        self.assertContains(response, "Manage accounts")

    def test_non_superuser_cannot_access_super_admin_dashboard(self):
        company = self._make_company_owner("tenant-owner")
        self.client.force_login(company)

        response = self.client.get(reverse("super_admin_dashboard"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("dashboard"))

    def test_superuser_login_redirects_to_super_admin_dashboard(self):
        superuser = User.objects.create_superuser(
            username="platform-owner-2",
            password="password123",
        )

        self.client.force_login(superuser)
        response = self.client.get(reverse("login_redirect"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("super_admin_dashboard"))

    def test_superuser_is_redirected_from_tenant_operations(self):
        superuser = User.objects.create_superuser(
            username="platform-owner-ops",
            password="password123",
        )

        self.client.force_login(superuser)

        for route_name in ("cash_register", "customer_list", "sales_invoice_list"):
            response = self.client.get(reverse(route_name))
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response["Location"], reverse("super_admin_dashboard"))

    def test_superuser_customer_and_stock_helpers_stay_in_platform_workspace(self):
        superuser = User.objects.create_superuser(
            username="platform-owner-scope",
            password="password123",
        )
        tenant = self._make_company_owner("tenant-scope", plan="PRO", status="active")
        tenant_location = self._make_location(tenant, "Tenant HQ")
        platform_location = self._make_location(superuser, "Platform HQ")
        category = Category.objects.create(name="Scope Category")
        brand = Brand.objects.create(name="Scope Brand")
        tenant_item = Item.objects.create(
            owner=tenant,
            name="Tenant Item",
            sku="TENANT-SCOPE",
            category=category,
            brand=brand,
            cost_price=Decimal("10.00"),
            price=Decimal("25.00"),
        )
        platform_item = Item.objects.create(
            owner=superuser,
            name="Platform Item",
            sku="PLATFORM-SCOPE",
            category=category,
            brand=brand,
            cost_price=Decimal("5.00"),
            price=Decimal("15.00"),
        )
        StockRecord.objects.create(item=tenant_item, location=tenant_location, quantity=5)
        platform_stock = StockRecord.objects.create(
            item=platform_item,
            location=platform_location,
            quantity=2,
        )
        Customer.objects.create(owner=tenant, name="Tenant Customer")
        platform_customer = Customer.objects.create(owner=superuser, name="Platform Customer")

        self.assertEqual(list(_customer_queryset_for_user(superuser)), [platform_customer])
        self.assertEqual(list(_stock_queryset_for_user(superuser)), [platform_stock])


class DashboardCommandCenterTests(TestCase):
    def _make_user(self, username):
        user = User.objects.create_user(username=username, password="password123")
        profile = UserProfile.for_user(user)
        profile.role = "admin"
        profile.status = "active"
        profile.plan = "PRO"
        profile.pro_expires = timezone.now().date() + timedelta(days=30)
        profile.plan_end = timezone.now() + timedelta(days=30)
        profile.save()
        return user

    def test_dashboard_uses_branch_capacity_and_surfaces_open_shift_alert(self):
        owner = self._make_user("dashboard-command-owner")
        location = Location.objects.create(owner=owner, name="Kingston HQ", inventory_capacity=30000)
        category = Category.objects.create(owner=owner, name="Dashboard Category")
        brand = Brand.objects.create(owner=owner, name="Dashboard Brand")
        item = Item.objects.create(
            owner=owner,
            name="Bigga Pineapple Soda",
            sku="DASH-BIGGA",
            category=category,
            brand=brand,
            cost_price=Decimal("75.00"),
            price=Decimal("150.00"),
        )
        StockRecord.objects.create(item=item, location=location, quantity=28000)
        shift = CashShift.objects.create(
            cashier=owner,
            location=location,
            opening_cash=Decimal("5000.00"),
        )
        self.client.force_login(owner)

        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["inventory_capacity"], 30000)
        self.assertEqual(response.context["inventory_percent"], 93)
        self.assertEqual(response.context["raw_inventory_percent"], 93)
        self.assertEqual(response.context["inventory_over_capacity"], 0)
        self.assertEqual(response.context["open_shift_count"], 1)
        self.assertContains(response, "28,000 / 30,000 Units")
        self.assertContains(response, "2,000 units remaining")
        self.assertContains(response, "Drawer closeout pending")
        self.assertContains(response, "Start Closeout")
        self.assertIn(f"shift={shift.id}", response.context["open_shift_alert_url"])

        closeout_response = self.client.get(response.context["open_shift_alert_url"])
        self.assertEqual(closeout_response.status_code, 200)
        self.assertContains(closeout_response, f"Open shift #{shift.id}")
        self.assertContains(closeout_response, "Finalize Shift Closeout")

        post_response = self.client.post(
            reverse("cash_reconciliation"),
            {
                "action": "close_shift",
                "shift_id": shift.id,
                "counted_cash": "5000.00",
                "notes": "Dashboard alert closeout.",
            },
        )
        self.assertEqual(post_response.status_code, 302)
        shift.refresh_from_db()
        self.assertTrue(shift.is_closed)

    def test_locations_page_updates_branch_capacity(self):
        owner = self._make_user("dashboard-capacity-owner")
        location = Location.objects.create(owner=owner, name="Montego Bay", inventory_capacity=1000)
        self.client.force_login(owner)

        response = self.client.post(
            reverse("locations"),
            {
                "action": "update_capacity",
                "location_id": location.pk,
                "inventory_capacity": "45000",
            },
        )

        self.assertEqual(response.status_code, 302)
        location.refresh_from_db()
        self.assertEqual(location.inventory_capacity, 45000)

    def test_locations_page_formats_valuation_and_links_branch_inventory(self):
        owner = self._make_user("locations-polish-owner")
        location = Location.objects.create(
            owner=owner,
            name="3 Felix Fox Boulevard, Kingston",
            inventory_capacity=28200,
        )
        category = Category.objects.create(owner=owner, name="Beverages")
        brand = Brand.objects.create(owner=owner, name="QuickStock")
        item = Item.objects.create(
            owner=owner,
            name="Asset Valuation Case",
            sku="VAL-CASE",
            category=category,
            brand=brand,
            cost_price=Decimal("500.00"),
            price=Decimal("423002.00"),
        )
        StockRecord.objects.create(item=item, location=location, quantity=10)
        self.client.force_login(owner)

        response = self.client.get(reverse("locations"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "$4,230,020.00")
        self.assertContains(response, f"location={location.pk}")
        self.assertContains(response, "View Branch Inventory")


class InventoryControlPolishTests(TestCase):
    def _make_user(self, username):
        user = User.objects.create_user(username=username, password="password123")
        profile = UserProfile.for_user(user)
        profile.role = "admin"
        profile.status = "active"
        profile.plan = "PRO"
        profile.pro_expires = timezone.now().date() + timedelta(days=30)
        profile.plan_end = timezone.now() + timedelta(days=30)
        profile.save()
        return user

    def test_inventory_table_formats_price_and_quantities_with_commas(self):
        owner = self._make_user("inventory-polish-owner")
        location = Location.objects.create(owner=owner, name="Main Store")
        category = Category.objects.create(owner=owner, name="Dry Goods")
        brand = Brand.objects.create(owner=owner, name="Catherine's Peak")
        item = Item.objects.create(
            owner=owner,
            name="Catherine's Peak Coffee",
            sku="DRY-COFFEE",
            category=category,
            brand=brand,
            cost_price=Decimal("1000.00"),
            price=Decimal("2100.00"),
        )
        StockRecord.objects.create(item=item, location=location, quantity=10081)
        self.client.force_login(owner)

        response = self.client.get(reverse("inventory"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "$2,100.00")
        self.assertContains(response, "10,081")

    def test_inventory_location_filter_shows_branch_specific_quantity(self):
        owner = self._make_user("inventory-branch-filter-owner")
        main_store = Location.objects.create(owner=owner, name="Main Store")
        warehouse = Location.objects.create(owner=owner, name="Warehouse")
        category = Category.objects.create(owner=owner, name="Snacks")
        brand = Brand.objects.create(owner=owner, name="QuickStock")
        item = Item.objects.create(
            owner=owner,
            name="Banana Chips",
            sku="SNK-BCHIP",
            category=category,
            brand=brand,
            cost_price=Decimal("90.00"),
            price=Decimal("150.00"),
        )
        StockRecord.objects.create(item=item, location=main_store, quantity=5)
        StockRecord.objects.create(item=item, location=warehouse, quantity=100)
        self.client.force_login(owner)

        response = self.client.get(reverse("inventory"), {"location": main_store.pk})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Branch: Main Store")
        self.assertContains(response, 'name="location"')
        self.assertContains(response, '<option value="">All Branches</option>')
        self.assertContains(response, f'<option value="{main_store.pk}" selected>Main Store</option>')
        self.assertContains(response, f'<option value="{warehouse.pk}" >Warehouse</option>')
        self.assertContains(response, 'data-quantity="5"')
        self.assertNotContains(response, 'data-quantity="105"')
        self.assertNotContains(response, 'data-quantity="100"')

    def test_receive_stock_page_exposes_scanner_and_live_batch_hooks(self):
        owner = self._make_user("receive-stock-polish-owner")
        Location.objects.create(owner=owner, name="3 Felix Fox Boulevard, Kingston")
        Supplier.objects.create(owner=owner, name="Kingston Supplier")
        category = Category.objects.create(owner=owner, name="Beverages")
        brand = Brand.objects.create(owner=owner, name="QuickStock")
        Item.objects.create(
            owner=owner,
            name="Bigga Pineapple Soda",
            sku="BIGGA-PINE",
            barcode="123456789012",
            category=category,
            brand=brand,
            cost_price=Decimal("85.00"),
            price=Decimal("150.00"),
        )
        self.client.force_login(owner)

        response = self.client.get(reverse("receive_stock"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="stock-barcode-input"')
        self.assertContains(response, 'id="batch_total"')
        self.assertContains(response, 'id="receive_live_math"')

        script_path = Path(__file__).resolve().parent / "static" / "inventory" / "js" / "receive_stock.js"
        script = script_path.read_text()
        self.assertIn("refocusScanner", script)
        self.assertIn("stockItemAdded", script)
        self.assertIn("requestSubmit", script)
        self.assertIn("toLocaleString", script)


class CustomerDetailTests(TestCase):
    def _make_user(self, username):
        user = User.objects.create_user(username=username, password="password123")
        profile = UserProfile.for_user(user)
        profile.role = "admin"
        profile.status = "active"
        profile.plan = "PRO"
        profile.pro_expires = timezone.now().date() + timedelta(days=30)
        profile.plan_end = timezone.now() + timedelta(days=30)
        profile.parent_admin = None
        profile.save()
        return user

    def _make_customer_record(self, owner, name="Customer A"):
        return Customer.objects.create(
            owner=owner,
            name=name,
            email="customer@example.com",
            phone="18005551234",
            physical_address="12 Hope Road, Kingston",
            business_address="Suite 4, Billing Centre",
            notes="Preferred customer",
        )

    def test_customer_detail_shows_only_owner_records(self):
        owner = self._make_user("customer-owner")
        customer = self._make_customer_record(owner, "Acme Co")
        CustomerNote.objects.create(
            customer=customer,
            owner=owner,
            created_by=owner,
            body="Called to confirm the next quotation.",
        )
        location = Location.objects.create(owner=owner, name="Main Branch")
        category = Category.objects.create(name="Customer Category")
        brand = Brand.objects.create(name="Customer Brand")
        item = Item.objects.create(
            owner=owner,
            name="Service Item",
            sku="CUST-1",
            category=category,
            brand=brand,
            cost_price=Decimal("10.00"),
            price=Decimal("20.00"),
        )

        quote = SalesQuotation.objects.create(owner=owner, created_by=owner, customer=customer)
        SalesQuotationItem.objects.create(quotation=quote, item=item, quantity=2, unit_price=Decimal("20.00"))
        invoice = SalesInvoice.objects.create(
            owner=owner,
            created_by=owner,
            customer=customer,
            location=location,
            subtotal=Decimal("40.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("40.00"),
        )

        other_owner = self._make_user("other-owner")
        other_customer = self._make_customer_record(other_owner, "Other Co")
        SalesQuotation.objects.create(owner=other_owner, created_by=other_owner, customer=other_customer)
        SalesInvoice.objects.create(
            owner=other_owner,
            created_by=other_owner,
            customer=other_customer,
            location=Location.objects.create(owner=other_owner, name="Other Branch"),
            subtotal=Decimal("10.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("10.00"),
        )

        self.client.force_login(owner)
        response = self.client.get(reverse("customer_detail", args=[customer.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Acme Co")
        self.assertContains(response, quote.quote_no)
        self.assertContains(response, invoice.invoice_no)
        self.assertContains(response, "Called to confirm the next quotation.")
        self.assertContains(response, "12 Hope Road, Kingston")
        self.assertContains(response, "Suite 4, Billing Centre")
        self.assertNotContains(response, "Other Co")

    def test_customer_detail_shows_outstanding_ar_and_filters_invoices(self):
        owner = self._make_user("customer-ar-owner")
        customer = self._make_customer_record(owner, "AR Customer")
        location = Location.objects.create(owner=owner, name="Main Branch")
        open_invoice = SalesInvoice.objects.create(
            owner=owner,
            created_by=owner,
            customer=customer,
            location=location,
            status="issued",
            subtotal=Decimal("220.00"),
            tax_amount=Decimal("33.00"),
            total_amount=Decimal("253.00"),
        )
        paid_invoice = SalesInvoice.objects.create(
            owner=owner,
            created_by=owner,
            customer=customer,
            location=location,
            status="paid",
            subtotal=Decimal("100.00"),
            tax_amount=Decimal("15.00"),
            total_amount=Decimal("115.00"),
        )
        SalesInvoicePayment.objects.create(
            invoice=paid_invoice,
            received_by=owner,
            amount=Decimal("115.00"),
            payment_method="cash",
        )
        self.client.force_login(owner)

        response = self.client.get(reverse("customer_detail", args=[customer.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["customer_outstanding_balance"], Decimal("253.00"))
        self.assertEqual(response.context["customer_open_invoice_count"], 1)
        self.assertEqual(response.context["invoice_filter_counts"], {"all": 2, "open": 1, "paid": 1})
        self.assertContains(response, "Outstanding AR")
        self.assertContains(response, "$253.00")
        self.assertContains(response, "Open / Unpaid")
        self.assertContains(response, f"?invoice_filter=open")
        self.assertContains(response, f"{reverse('sales_quotation_create')}?customer_id={customer.pk}")
        self.assertContains(response, f"{reverse('sales_invoice_create')}?customer_id={customer.pk}")

        open_response = self.client.get(reverse("customer_detail", args=[customer.pk]), {"invoice_filter": "open"})
        self.assertEqual(open_response.status_code, 200)
        self.assertEqual(open_response.context["invoice_filter"], "open")
        self.assertContains(open_response, open_invoice.invoice_no)
        self.assertNotContains(open_response, paid_invoice.invoice_no)

        paid_response = self.client.get(reverse("customer_detail", args=[customer.pk]), {"invoice_filter": "paid"})
        self.assertEqual(paid_response.status_code, 200)
        self.assertContains(paid_response, paid_invoice.invoice_no)
        self.assertNotContains(paid_response, open_invoice.invoice_no)

    def test_customer_profile_create_links_preselect_customer_on_sales_forms(self):
        owner = self._make_user("customer-preselect-owner")
        customer = self._make_customer_record(owner, "Preselected Customer")
        self.client.force_login(owner)

        quote_response = self.client.get(reverse("sales_quotation_create"), {"customer_id": customer.pk})
        invoice_response = self.client.get(reverse("sales_invoice_create"), {"customer_id": customer.pk})

        self.assertEqual(quote_response.status_code, 200)
        self.assertEqual(invoice_response.status_code, 200)
        self.assertEqual(quote_response.context["selected_customer_id"], customer.pk)
        self.assertEqual(invoice_response.context["selected_customer_id"], customer.pk)
        self.assertContains(
            quote_response,
            f'<option value="{customer.id}" data-tax-exempt="0" selected>{customer.name}',
        )
        self.assertContains(
            invoice_response,
            f'<option value="{customer.id}" data-tax-exempt="0" selected>{customer.name}',
        )

    def test_customer_detail_is_tenant_scoped(self):
        owner = self._make_user("tenant-owner-a")
        intruder = self._make_user("tenant-owner-b")
        customer = self._make_customer_record(owner, "Secret Customer")

        self.client.force_login(intruder)
        response = self.client.get(reverse("customer_detail", args=[customer.pk]))

        self.assertEqual(response.status_code, 404)


class CustomerEditTests(TestCase):
    def _make_user(self, username):
        user = User.objects.create_user(username=username, password="password123")
        profile = UserProfile.for_user(user)
        profile.role = "admin"
        profile.status = "active"
        profile.plan = "PRO"
        profile.pro_expires = timezone.now().date() + timedelta(days=30)
        profile.plan_end = timezone.now() + timedelta(days=30)
        profile.parent_admin = None
        profile.save()
        return user

    def test_customer_edit_page_renders_and_saves(self):
        owner = self._make_user("edit-owner")
        customer = Customer.objects.create(owner=owner, name="Original Name", email="old@example.com", notes="Front desk pickup")
        CustomerNote.objects.create(
            customer=customer,
            owner=owner,
            created_by=owner,
            body="Initial onboarding note.",
        )

        self.client.force_login(owner)
        response = self.client.get(reverse("edit_customer", args=[customer.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Edit Customer")
        self.assertContains(response, "Initial onboarding note.")
        self.assertContains(response, "TRN / Tax ID")
        self.assertContains(response, "Exempt from 15% GCT")
        self.assertContains(response, "customer-note-composer")
        self.assertContains(response, "Post Note to Timeline")

        post_response = self.client.post(
            reverse("edit_customer", args=[customer.pk]),
            data={
                "name": "Updated Name",
                "email": "new@example.com",
                "phone": "8765551212",
                "trn": "123456789",
                "is_tax_exempt": "on",
                "physical_address": "44 Constant Spring Road",
                "business_address": "Warehouse 2, Spanish Town",
                "notes": "Updated billing instructions",
                "new_note": "Customer requested Saturday follow-up.",
            },
        )

        self.assertEqual(post_response.status_code, 302)
        self.assertEqual(post_response["Location"], reverse("customer_detail", args=[customer.pk]))
        customer.refresh_from_db()
        self.assertEqual(customer.name, "Updated Name")
        self.assertEqual(customer.email, "new@example.com")
        self.assertEqual(customer.phone, "8765551212")
        self.assertEqual(customer.trn, "123-456-789")
        self.assertTrue(customer.is_tax_exempt)
        self.assertEqual(customer.physical_address, "44 Constant Spring Road")
        self.assertEqual(customer.business_address, "Warehouse 2, Spanish Town")
        self.assertEqual(customer.notes, "Updated billing instructions")
        self.assertEqual(customer.note_entries.count(), 2)
        self.assertTrue(customer.note_entries.filter(body="Customer requested Saturday follow-up.").exists())

    def test_customer_note_post_does_not_save_profile_changes(self):
        owner = self._make_user("edit-note-only-owner")
        customer = Customer.objects.create(owner=owner, name="Original Name", email="old@example.com")
        self.client.force_login(owner)

        response = self.client.post(
            reverse("edit_customer", args=[customer.pk]),
            data={
                "post_note": "1",
                "name": "Unsaved Name",
                "email": "changed@example.com",
                "new_note": "Independent note entry.",
            },
        )

        self.assertEqual(response.status_code, 302)
        customer.refresh_from_db()
        self.assertEqual(customer.name, "Original Name")
        self.assertEqual(customer.email, "old@example.com")
        self.assertTrue(customer.note_entries.filter(body="Independent note entry.").exists())

    def test_customer_trn_requires_nine_digits(self):
        owner = self._make_user("customer-trn-owner")
        customer = Customer(owner=owner, name="TRN Test", trn="12345")

        with self.assertRaises(ValidationError):
            customer.full_clean()

    def test_customer_list_searches_customer_addresses(self):
        owner = self._make_user("customer-address-search-owner")
        Customer.objects.create(
            owner=owner,
            name="Address Match",
            physical_address="22 Barbican Road",
            business_address="Accounts Office, New Kingston",
        )
        Customer.objects.create(owner=owner, name="Other Customer")

        self.client.force_login(owner)
        response = self.client.get(reverse("customer_list"), {"q": "Barbican"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Address Match")
        self.assertContains(response, "22 Barbican Road")
        self.assertNotContains(response, "Other Customer")

    def test_customer_edit_is_tenant_scoped(self):
        owner = self._make_user("edit-owner-a")
        intruder = self._make_user("edit-owner-b")
        customer = Customer.objects.create(owner=owner, name="Private Customer")

        self.client.force_login(intruder)
        response = self.client.get(reverse("edit_customer", args=[customer.pk]))

        self.assertEqual(response.status_code, 404)


class WeekTwoSalesIntegrityTests(TestCase):
    def _make_user(self, username):
        user = User.objects.create_user(username=username, password="password123")
        profile = UserProfile.for_user(user)
        profile.role = "admin"
        profile.status = "active"
        profile.plan = "PRO"
        profile.pro_expires = timezone.now().date() + timedelta(days=30)
        profile.plan_end = timezone.now() + timedelta(days=30)
        profile.parent_admin = None
        profile.save()
        return user

    def _make_location(self, owner, name):
        return Location.objects.create(owner=owner, name=name)

    def _open_shift(self, user, location, opening_cash="100.00"):
        return CashShift.objects.create(
            cashier=user,
            location=location,
            opening_cash=Decimal(opening_cash),
            expected_cash=Decimal(opening_cash),
            is_closed=False,
        )

    def _make_item(self, owner, name="Item A", sku="SKU-1", price="100.00", quantity=10, is_taxable=True):
        category = Category.objects.create(name=f"Category-{sku}")
        brand = Brand.objects.create(name=f"Brand-{sku}")
        return Item.objects.create(
            owner=owner,
            name=name,
            sku=sku,
            category=category,
            brand=brand,
            cost_price=Decimal("50.00"),
            price=Decimal(price),
            is_taxable=is_taxable,
        )

    def test_customer_pos_receipt_and_history_views_require_allowed_roles(self):
        owner = self._make_user("authz-role-owner")
        profile = UserProfile.for_user(owner)
        profile.role = "inventory_clerk"
        profile.save(update_fields=["role"])
        location = self._make_location(owner, "Main Branch")
        customer = Customer.objects.create(owner=owner, name="Role Scoped Customer")
        sale = Sale.objects.create(owner=owner, cashier=owner, location=location, total_price=Decimal("10.00"))

        self.client.force_login(owner)
        blocked_gets = (
            reverse("customer_list"),
            reverse("customer_detail", args=[customer.pk]),
            reverse("pos_items"),
            reverse("pos_item_lookup") + "?q=ANY",
            reverse("view_receipt", args=[sale.pk]),
            reverse("sales_history"),
        )
        for url in blocked_gets:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302, url)
            self.assertIn(reverse("dashboard"), response["Location"])

        delete_response = self.client.post(reverse("delete_customer", args=[customer.pk]))
        self.assertEqual(delete_response.status_code, 302)
        self.assertIn(reverse("dashboard"), delete_response["Location"])

    def test_cash_register_open_shift_returns_to_register(self):
        owner = self._make_user("register-shift-owner")
        location = self._make_location(owner, "Main Store")
        profile = UserProfile.for_user(owner)
        profile.default_location = location
        profile.save(update_fields=["default_location"])

        self.client.force_login(owner)
        register_response = self.client.get(reverse("cash_register"))

        self.assertEqual(register_response.status_code, 302)
        self.assertEqual(
            register_response["Location"],
            f"{reverse('open_shift')}?{urlencode({'next': reverse('cash_register')})}",
        )

        open_response = self.client.get(register_response["Location"])
        self.assertEqual(open_response.status_code, 200)
        self.assertEqual(open_response.context["next_url"], reverse("cash_register"))

        submit_response = self.client.post(
            reverse("open_shift"),
            {"opening_cash": "0.00", "next": reverse("cash_register")},
        )
        self.assertEqual(submit_response.status_code, 302)
        self.assertEqual(submit_response["Location"], reverse("cash_register"))

    def test_sales_history_is_scoped_by_sale_owner(self):
        owner = self._make_user("history-scope-owner")
        other_owner = self._make_user("history-scope-other")
        owner_location = self._make_location(owner, "Main Branch")
        other_location = self._make_location(other_owner, "Other Branch")
        owner_sale = Sale.objects.create(
            owner=owner,
            cashier=owner,
            location=owner_location,
            receipt_no=101,
            total_price=Decimal("10.00"),
        )
        other_sale = Sale.objects.create(
            owner=other_owner,
            cashier=other_owner,
            location=other_location,
            receipt_no=202,
            total_price=Decimal("20.00"),
        )

        self.client.force_login(owner)
        response = self.client.get(reverse("sales_history"))

        self.assertEqual(response.status_code, 200)
        sales = list(response.context["sales"])
        self.assertIn(owner_sale, sales)
        self.assertNotIn(other_sale, sales)

    def test_sales_history_is_paginated_and_location_filtered(self):
        owner = self._make_user("history-pagination-owner")
        main = self._make_location(owner, "Main Branch")
        warehouse = self._make_location(owner, "Warehouse")
        for index in range(30):
            Sale.objects.create(
                owner=owner,
                cashier=owner,
                location=main if index < 26 else warehouse,
                receipt_no=1000 + index,
                total_price=Decimal("10.00"),
            )

        self.client.force_login(owner)
        first_page = self.client.get(reverse("sales_history"))
        second_page = self.client.get(reverse("sales_history"), {"page": 2})
        filtered_page = self.client.get(reverse("sales_history"), {"location": warehouse.id})

        self.assertEqual(first_page.status_code, 200)
        self.assertEqual(second_page.status_code, 200)
        self.assertEqual(filtered_page.status_code, 200)
        self.assertEqual(len(first_page.context["sales"]), 25)
        self.assertEqual(len(second_page.context["sales"]), 5)
        self.assertEqual(first_page.context["page_obj"].paginator.count, 30)
        self.assertEqual(filtered_page.context["page_obj"].paginator.count, 4)
        self.assertTrue(all(sale.location_id == warehouse.id for sale in filtered_page.context["sales"]))

    def test_customer_directory_is_paginated(self):
        owner = self._make_user("customer-pagination-owner")
        Customer.objects.bulk_create(
            Customer(owner=owner, name=f"Customer {index:02d}")
            for index in range(30)
        )

        self.client.force_login(owner)
        first_page = self.client.get(reverse("customer_list"))
        second_page = self.client.get(reverse("customer_list"), {"page": 2})

        self.assertEqual(first_page.status_code, 200)
        self.assertEqual(second_page.status_code, 200)
        self.assertEqual(len(first_page.context["customers"]), 25)
        self.assertEqual(len(second_page.context["customers"]), 5)
        self.assertEqual(first_page.context["page_size"], 25)
        self.assertEqual(first_page.context["page_obj"].paginator.count, 30)

    @override_settings(POS_INITIAL_ITEMS_LIMIT=10, POS_SEARCH_RESULT_LIMIT=7)
    def test_pos_product_payloads_are_bounded(self):
        owner = self._make_user("pos-search-bounds")
        location = self._make_location(owner, "Main Branch")
        owner.profile.default_location = location
        owner.profile.save(update_fields=["default_location"])
        self._open_shift(owner, location)
        for index in range(25):
            name = "Match Product" if index < 15 else "Other Product"
            item = self._make_item(
                owner,
                name=f"{name} {index:02d}",
                sku=f"POS-BOUND-{index:02d}",
                price="100.00",
                quantity=5,
            )
            StockRecord.objects.create(item=item, location=location, quantity=5)

        self.client.force_login(owner)
        initial_response = self.client.get(reverse("pos_items"))
        search_response = self.client.get(reverse("pos_items"), {"q": "Match"})
        register_response = self.client.get(reverse("cash_register"))

        self.assertEqual(initial_response.status_code, 200)
        self.assertEqual(search_response.status_code, 200)
        self.assertEqual(register_response.status_code, 200)
        self.assertEqual(len(initial_response.json()["items"]), 10)
        self.assertEqual(len(search_response.json()["items"]), 7)
        self.assertEqual(len(register_response.context["items"]), 10)
        self.assertTrue(all("Match" in item["name"] for item in search_response.json()["items"]))

    def test_cash_register_exposes_tender_presets_stock_badges_and_hotkey_script(self):
        owner = self._make_user("pos-ux-polish")
        location = self._make_location(owner, "Main Branch")
        owner.profile.default_location = location
        owner.profile.save(update_fields=["default_location"])
        self._open_shift(owner, location)
        item = self._make_item(
            owner,
            name="Bigga Grape Soda",
            sku="POS-BIGGA",
            price="150.00",
            quantity=2,
        )
        StockRecord.objects.create(item=item, location=location, quantity=2)
        self.client.force_login(owner)

        register_response = self.client.get(reverse("cash_register"))
        items_response = self.client.get(reverse("pos_items"))

        self.assertEqual(register_response.status_code, 200)
        self.assertContains(register_response, 'data-cash-preset="exact"')
        self.assertContains(register_response, 'data-cash-preset="5000"')
        self.assertContains(register_response, "Low Stock: 2")
        self.assertContains(register_response, "pos-stock-badge-low")
        self.assertContains(register_response, "pos-tender-stock-hotkeys-1")
        self.assertEqual(items_response.status_code, 200)
        payload_item = items_response.json()["items"][0]
        self.assertEqual(payload_item["stock_quantity"], 2)
        self.assertEqual(payload_item["stock_label"], "Low Stock: 2")
        self.assertEqual(payload_item["stock_tone"], "low")

        script_path = Path(__file__).resolve().parent / "static" / "inventory" / "js" / "cash_register.js"
        script = script_path.read_text()
        self.assertIn("applyCashTenderPreset", script)
        self.assertIn("option.classList.toggle('is-active', active)", script)
        self.assertIn("button.setAttribute('aria-pressed', 'false')", script)
        self.assertIn("renderProductCardHtml", script)
        self.assertIn("e.key === 'F2'", script)
        self.assertIn("window.completeCheckout()", script)

    def test_legacy_inventory_api_post_requires_csrf(self):
        owner = self._make_user("legacy-inventory-csrf")
        self._make_location(owner, "Main Branch")
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(owner)

        get_response = csrf_client.get(reverse("api_inventory"))
        post_response = csrf_client.post(
            reverse("api_inventory"),
            data={"sku": "SHOULD-NOT-MUTATE"},
            content_type="application/json",
        )

        self.assertEqual(get_response.status_code, 200)
        self.assertEqual(post_response.status_code, 403)

    def test_customer_lifecycle_writes_required_audit_logs(self):
        owner = self._make_user("customer-audit-owner")
        self.client.force_login(owner)

        create_response = self.client.post(
            reverse("add_customer"),
            data={
                "name": "Audit Customer",
                "email": "audit@example.com",
                "phone": "876-000-0000",
                "physical_address": "Kingston",
                "business_address": "Downtown Kingston",
                "notes": "Initial profile",
                "new_note": "",
            },
        )
        customer = Customer.objects.get(owner=owner, name="Audit Customer")
        update_response = self.client.post(
            reverse("edit_customer", args=[customer.pk]),
            data={
                "name": "Audit Customer Updated",
                "email": "audit-updated@example.com",
                "phone": "876-000-1111",
                "physical_address": "Kingston",
                "business_address": "New Kingston",
                "notes": "Updated profile",
                "new_note": "Called to confirm billing address.",
            },
        )
        delete_response = self.client.post(reverse("delete_customer", args=[customer.pk]))

        self.assertEqual(create_response.status_code, 302)
        self.assertEqual(update_response.status_code, 302)
        self.assertEqual(delete_response.status_code, 302)
        messages = set(
            AuditLog.objects.filter(user=owner, action="customer").values_list("message", flat=True)
        )
        self.assertIn("Customer created", messages)
        self.assertIn("Customer updated", messages)
        self.assertIn("Customer deleted", messages)
        self.assertFalse(Customer.objects.filter(pk=customer.pk).exists())


    def test_api_sales_creates_sale_atomically_and_deducts_stock(self):
        owner = self._make_user("sales-owner")
        location = self._make_location(owner, "Main Branch")
        item = self._make_item(owner, sku="SKU-SALE", price="230.00", quantity=8)
        StockRecord.objects.create(item=item, location=location, quantity=8)

        self.client.force_login(owner)
        response = self.client.post(
            reverse("api_sales"),
            data={
                "location_id": location.pk,
                "discount": "10.00",
                "tender": "card",
                "items": [
                    {"sku": "SKU-SALE", "quantity": 2, "price": "1.00"},
                ],
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        sale = Sale.objects.get(pk=payload["sale_id"])
        line = sale.items.get()
        stock = StockRecord.objects.get(item=item, location=location)
        item.refresh_from_db()

        self.assertEqual(sale.tender, "card")
        self.assertEqual(line.quantity, 2)
        self.assertEqual(line.unit_price, Decimal("230.00"))
        self.assertEqual(stock.quantity, 6)
        self.assertEqual(item.quantity, 6)
        self.assertEqual(sale.subtotal, Decimal("400.00"))
        self.assertEqual(sale.gct_amount, Decimal("60.00"))
        self.assertEqual(sale.total_price, Decimal("450.00"))

    def test_finalize_sale_rejects_stock_from_wrong_location_atomically(self):
        owner = self._make_user("finalize-location-owner")
        sale_location = self._make_location(owner, "Main Branch")
        other_location = self._make_location(owner, "Warehouse")
        shift = self._open_shift(owner, sale_location)
        item = self._make_item(owner, sku="FINALIZE-WRONG-LOCATION", price="100.00", quantity=5)
        wrong_stock = StockRecord.objects.create(item=item, location=other_location, quantity=5)

        with self.assertRaises(SaleWorkflowError) as raised:
            finalize_sale(
                owner=owner,
                cashier=owner,
                location=sale_location,
                shift=shift,
                tender="cash",
                line_items=[(item, wrong_stock, 1, item.price)],
            )

        self.assertIn("stock does not belong", raised.exception.message)
        self.assertFalse(Sale.objects.filter(owner=owner).exists())
        self.assertFalse(SaleItem.objects.exists())
        self.assertEqual(StockRecord.objects.get(pk=wrong_stock.pk).quantity, 5)
        shift.refresh_from_db()
        self.assertEqual(shift.total_sales, Decimal("0.00"))

    def test_finalize_sale_rejects_cross_tenant_item_atomically(self):
        owner = self._make_user("finalize-tenant-owner")
        other_owner = self._make_user("finalize-tenant-other")
        location = self._make_location(owner, "Main Branch")
        shift = self._open_shift(owner, location)
        owner_item = self._make_item(owner, sku="FINALIZE-OWNER-ITEM", price="100.00", quantity=5)
        foreign_item = self._make_item(other_owner, sku="FINALIZE-FOREIGN-ITEM", price="100.00", quantity=5)
        owner_stock = StockRecord.objects.create(item=owner_item, location=location, quantity=5)

        with self.assertRaises(SaleWorkflowError) as raised:
            finalize_sale(
                owner=owner,
                cashier=owner,
                location=location,
                shift=shift,
                tender="cash",
                line_items=[(foreign_item, owner_stock, 1, foreign_item.price)],
            )

        self.assertIn("belongs to another tenant", raised.exception.message)
        self.assertFalse(Sale.objects.filter(owner=owner).exists())
        self.assertFalse(SaleItem.objects.exists())
        self.assertEqual(StockRecord.objects.get(pk=owner_stock.pk).quantity, 5)
        shift.refresh_from_db()
        self.assertEqual(shift.total_sales, Decimal("0.00"))

    def test_api_sales_rolls_back_when_stock_is_insufficient(self):
        owner = self._make_user("rollback-owner")
        location = self._make_location(owner, "Main Branch")
        item = self._make_item(owner, sku="SKU-ROLLBACK", price="150.00", quantity=1)
        StockRecord.objects.create(item=item, location=location, quantity=1)

        self.client.force_login(owner)
        response = self.client.post(
            reverse("api_sales"),
            data={
                "location_id": location.pk,
                "items": [
                    {"sku": "SKU-ROLLBACK", "quantity": 2},
                ],
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Sale.objects.count(), 0)
        self.assertEqual(StockRecord.objects.get(item=item, location=location).quantity, 1)
        item.refresh_from_db()
        self.assertEqual(item.quantity, 1)

    def test_api_sales_rolls_back_when_any_item_is_missing(self):
        owner = self._make_user("missing-owner")
        location = self._make_location(owner, "Main Branch")
        item = self._make_item(owner, sku="SKU-OK", price="120.00", quantity=5)
        StockRecord.objects.create(item=item, location=location, quantity=5)

        self.client.force_login(owner)
        response = self.client.post(
            reverse("api_sales"),
            data={
                "location_id": location.pk,
                "items": [
                    {"sku": "SKU-OK", "quantity": 1},
                    {"sku": "SKU-MISSING", "quantity": 1},
                ],
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(Sale.objects.count(), 0)
        self.assertEqual(StockRecord.objects.get(item=item, location=location).quantity, 5)
        item.refresh_from_db()
        self.assertEqual(item.quantity, 5)

    def test_api_sales_preserves_desktop_bank_transfer_tender(self):
        owner = self._make_user("bank-transfer-owner")
        location = self._make_location(owner, "Main Branch")
        item = self._make_item(owner, sku="SKU-BANK", price="120.00", quantity=5)
        StockRecord.objects.create(item=item, location=location, quantity=5)

        self.client.force_login(owner)
        response = self.client.post(
            reverse("api_sales"),
            data={
                "location_id": location.pk,
                "tender": "bank_transfer",
                "items": [{"sku": item.sku, "quantity": 1}],
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Sale.objects.get(pk=response.json()["sale_id"]).tender, "bank_transfer")

    def test_api_sales_rejects_invalid_line_without_partial_checkout(self):
        owner = self._make_user("invalid-line-owner")
        location = self._make_location(owner, "Main Branch")
        item = self._make_item(owner, sku="SKU-INVALID-LINE", price="120.00", quantity=5)
        StockRecord.objects.create(item=item, location=location, quantity=5)

        self.client.force_login(owner)
        response = self.client.post(
            reverse("api_sales"),
            data={
                "location_id": location.pk,
                "items": [
                    {"sku": item.sku, "quantity": 1},
                    {"sku": "", "quantity": 0},
                ],
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Sale.objects.filter(owner=owner).exists())
        self.assertEqual(StockRecord.objects.get(item=item, location=location).quantity, 5)

    def test_api_sales_rejects_discount_above_gross_total(self):
        owner = self._make_user("invalid-discount-owner")
        location = self._make_location(owner, "Main Branch")
        item = self._make_item(owner, sku="SKU-DISCOUNT", price="120.00", quantity=5)
        StockRecord.objects.create(item=item, location=location, quantity=5)

        self.client.force_login(owner)
        response = self.client.post(
            reverse("api_sales"),
            data={
                "location_id": location.pk,
                "discount": "121.00",
                "items": [{"sku": item.sku, "quantity": 1}],
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Sale.objects.filter(owner=owner).exists())

    def test_web_cash_register_replay_is_idempotent(self):
        owner = self._make_user("web-idempotent-sale")
        location = self._make_location(owner, "Main Branch")
        self._open_shift(owner, location)
        item = self._make_item(owner, sku="WEB-IDEMPOTENT", price="100.00", quantity=5)
        StockRecord.objects.create(item=item, location=location, quantity=5)
        payload = {
            "cart": [{"id": item.id, "quantity": 2}],
            "location_id": location.id,
            "discount": "0.00",
            "discount_type": "flat",
            "payment_channel": "cash",
            "amount_paid": "200.00",
            "offline_client_ref": "WEB-IDEMPOTENT-001",
        }

        self.client.force_login(owner)
        first_response = self.client.post(
            reverse("cash_register"),
            data=payload,
            content_type="application/json",
        )
        second_response = self.client.post(
            reverse("cash_register"),
            data=payload,
            content_type="application/json",
        )

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 200)
        self.assertTrue(second_response.json()["idempotent_replay"])
        self.assertEqual(first_response.json()["sale_id"], second_response.json()["sale_id"])
        self.assertEqual(Sale.objects.filter(owner=owner).count(), 1)
        self.assertEqual(StockRecord.objects.get(item=item, location=location).quantity, 3)

    def test_web_invoice_replay_is_idempotent(self):
        owner = self._make_user("web-idempotent-invoice")
        location = self._make_location(owner, "Main Branch")
        self._open_shift(owner, location)
        item = self._make_item(owner, sku="WEB-INVOICE-IDEMPOTENT", price="100.00", quantity=5)
        StockRecord.objects.create(item=item, location=location, quantity=5)
        payload = {
            "cart": [{"id": item.id, "quantity": 2}],
            "location_id": location.id,
            "discount": "0.00",
            "discount_type": "flat",
            "payment_channel": "invoice",
            "offline_client_ref": "WEB-INVOICE-IDEMPOTENT-001",
        }

        self.client.force_login(owner)
        first_response = self.client.post(
            reverse("cash_register"),
            data=payload,
            content_type="application/json",
        )
        second_response = self.client.post(
            reverse("cash_register"),
            data=payload,
            content_type="application/json",
        )

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 200)
        self.assertTrue(second_response.json()["idempotent_replay"])
        self.assertEqual(first_response.json()["invoice_id"], second_response.json()["invoice_id"])
        self.assertEqual(SalesInvoice.objects.filter(owner=owner).count(), 1)
        self.assertEqual(StockRecord.objects.get(item=item, location=location).quantity, 3)

    def test_web_invoice_checkout_assigns_selected_customer(self):
        owner = self._make_user("web-invoice-customer-owner")
        location = self._make_location(owner, "Main Branch")
        self._open_shift(owner, location)
        customer = Customer.objects.create(
            owner=owner,
            name="Credit Account Customer",
            credit_balance=Decimal("75.00"),
        )
        item = self._make_item(owner, sku="WEB-INVOICE-CUSTOMER", price="100.00", quantity=5)
        StockRecord.objects.create(item=item, location=location, quantity=5)

        self.client.force_login(owner)
        response = self.client.post(
            reverse("cash_register"),
            data={
                "cart": [{"id": item.id, "quantity": 2}],
                "location_id": location.id,
                "discount": "0.00",
                "discount_type": "flat",
                "payment_channel": "invoice",
                "customer_id": str(customer.id),
                "offline_client_ref": "WEB-INVOICE-CUSTOMER-001",
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        invoice = SalesInvoice.objects.get(pk=payload["invoice_id"])
        self.assertEqual(invoice.customer, customer)
        self.assertEqual(payload["customer_name"], "Credit Account Customer")

    def test_web_invoice_checkout_rejects_foreign_customer(self):
        owner = self._make_user("web-invoice-customer-owner-safe")
        other_owner = self._make_user("web-invoice-customer-other")
        location = self._make_location(owner, "Main Branch")
        self._open_shift(owner, location)
        foreign_customer = Customer.objects.create(owner=other_owner, name="Other Tenant Customer")
        item = self._make_item(owner, sku="WEB-INVOICE-FOREIGN-CUSTOMER", price="100.00", quantity=5)
        StockRecord.objects.create(item=item, location=location, quantity=5)

        self.client.force_login(owner)
        response = self.client.post(
            reverse("cash_register"),
            data={
                "cart": [{"id": item.id, "quantity": 2}],
                "location_id": location.id,
                "discount": "0.00",
                "discount_type": "flat",
                "payment_channel": "invoice",
                "customer_id": str(foreign_customer.id),
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(SalesInvoice.objects.filter(owner=owner).exists())
        self.assertEqual(StockRecord.objects.get(item=item, location=location).quantity, 5)

    def test_web_cash_register_rejects_invalid_cart_line_atomically(self):
        owner = self._make_user("web-invalid-cart")
        location = self._make_location(owner, "Main Branch")
        self._open_shift(owner, location)
        item = self._make_item(owner, sku="WEB-INVALID-CART", price="100.00", quantity=5)
        StockRecord.objects.create(item=item, location=location, quantity=5)

        self.client.force_login(owner)
        response = self.client.post(
            reverse("cash_register"),
            data={
                "cart": [
                    {"id": item.id, "quantity": 1},
                    {"id": item.id + 999, "quantity": 0},
                ],
                "location_id": location.id,
                "discount": "0.00",
                "discount_type": "flat",
                "payment_channel": "cash",
                "amount_paid": "100.00",
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Sale.objects.filter(owner=owner).exists())
        self.assertEqual(StockRecord.objects.get(item=item, location=location).quantity, 5)

    def test_web_cash_register_rejects_fractional_quantity_atomically(self):
        owner = self._make_user("web-fractional-qty")
        location = self._make_location(owner, "Main Branch")
        self._open_shift(owner, location)
        item = self._make_item(owner, sku="WEB-FRACTIONAL-QTY", price="100.00", quantity=5)
        StockRecord.objects.create(item=item, location=location, quantity=5)

        self.client.force_login(owner)
        response = self.client.post(
            reverse("cash_register"),
            data={
                "cart": [{"id": item.id, "quantity": "1.5"}],
                "location_id": location.id,
                "discount": "0.00",
                "discount_type": "flat",
                "payment_channel": "cash",
                "amount_paid": "200.00",
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("positive whole number", response.json()["error"])
        self.assertFalse(Sale.objects.filter(owner=owner).exists())
        self.assertEqual(StockRecord.objects.get(item=item, location=location).quantity, 5)

    @override_settings(POS_CART_MAX_LINE_QUANTITY=3)
    def test_web_cash_register_rejects_excessive_quantity_atomically(self):
        owner = self._make_user("web-excessive-qty")
        location = self._make_location(owner, "Main Branch")
        self._open_shift(owner, location)
        item = self._make_item(owner, sku="WEB-EXCESSIVE-QTY", price="100.00", quantity=10)
        StockRecord.objects.create(item=item, location=location, quantity=10)

        self.client.force_login(owner)
        response = self.client.post(
            reverse("cash_register"),
            data={
                "cart": [{"id": item.id, "quantity": "4"}],
                "location_id": location.id,
                "discount": "0.00",
                "discount_type": "flat",
                "payment_channel": "cash",
                "amount_paid": "500.00",
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("cannot exceed 3", response.json()["error"])
        self.assertFalse(Sale.objects.filter(owner=owner).exists())
        self.assertEqual(StockRecord.objects.get(item=item, location=location).quantity, 10)

    def test_web_cash_register_rejects_non_finite_money_values(self):
        owner = self._make_user("web-nonfinite-money")
        location = self._make_location(owner, "Main Branch")
        self._open_shift(owner, location)
        item = self._make_item(owner, sku="WEB-NONFINITE-MONEY", price="100.00", quantity=5)
        StockRecord.objects.create(item=item, location=location, quantity=5)

        self.client.force_login(owner)
        response = self.client.post(
            reverse("cash_register"),
            data={
                "cart": [{"id": item.id, "quantity": "1"}],
                "location_id": location.id,
                "discount": "NaN",
                "discount_type": "flat",
                "payment_channel": "cash",
                "amount_paid": "Infinity",
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Payment or discount amount", response.json()["error"])
        self.assertFalse(Sale.objects.filter(owner=owner).exists())
        self.assertEqual(StockRecord.objects.get(item=item, location=location).quantity, 5)

    def test_web_cash_register_rejects_discount_above_server_total_atomically(self):
        owner = self._make_user("web-discount-above-total")
        location = self._make_location(owner, "Main Branch")
        self._open_shift(owner, location)
        item = self._make_item(owner, sku="WEB-DISCOUNT-ABOVE", price="100.00", quantity=5)
        StockRecord.objects.create(item=item, location=location, quantity=5)

        self.client.force_login(owner)
        response = self.client.post(
            reverse("cash_register"),
            data={
                "cart": [{"id": item.id, "quantity": "1"}],
                "location_id": location.id,
                "discount": "101.00",
                "discount_type": "flat",
                "payment_channel": "cash",
                "amount_paid": "100.00",
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Discount cannot exceed", response.json()["error"])
        self.assertFalse(Sale.objects.filter(owner=owner).exists())
        self.assertEqual(StockRecord.objects.get(item=item, location=location).quantity, 5)

    def test_web_cash_register_rejects_unknown_payment_channel(self):
        owner = self._make_user("web-invalid-tender")
        location = self._make_location(owner, "Main Branch")
        self._open_shift(owner, location)
        item = self._make_item(owner, sku="WEB-INVALID-TENDER", price="100.00", quantity=5)
        StockRecord.objects.create(item=item, location=location, quantity=5)

        self.client.force_login(owner)
        response = self.client.post(
            reverse("cash_register"),
            data={
                "cart": [{"id": item.id, "quantity": 1}],
                "location_id": location.id,
                "discount": "0.00",
                "discount_type": "flat",
                "payment_channel": "invented-channel",
                "amount_paid": "100.00",
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Sale.objects.filter(owner=owner).exists())

    def test_cash_register_checkout_uses_tax_inclusive_totals(self):
        owner = self._make_user("register-owner")
        location = self._make_location(owner, "Main Branch")
        owner.profile.default_location = location
        owner.profile.save(update_fields=["default_location"])
        shift = self._open_shift(owner, location)
        item = self._make_item(owner, sku="SKU-POS", price="230.00", quantity=8)
        StockRecord.objects.create(item=item, location=location, quantity=8)

        self.client.force_login(owner)
        response = self.client.post(
            reverse("cash_register"),
            data=json.dumps(
                {
                    "location_id": location.pk,
                    "discount": 10,
                    "discount_type": "flat",
                    "tender": "card",
                    "cart": [{"id": item.id, "quantity": 2}],
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        sale = Sale.objects.get(pk=response.json()["sale_id"])
        item.refresh_from_db()
        shift.refresh_from_db()

        self.assertEqual(sale.shift_id, shift.id)
        self.assertEqual(sale.subtotal, Decimal("400.00"))
        self.assertEqual(sale.gct_amount, Decimal("60.00"))
        self.assertEqual(sale.total_price, Decimal("450.00"))
        self.assertEqual(shift.total_sales, Decimal("450.00"))
        self.assertEqual(item.quantity, 6)
        self.assertEqual(StockRecord.objects.get(item=item, location=location).quantity, 6)

    def test_cash_register_cash_checkout_returns_amount_paid_and_change_due(self):
        owner = self._make_user("register-cash-owner")
        location = self._make_location(owner, "Main Branch")
        owner.profile.default_location = location
        owner.profile.save(update_fields=["default_location"])
        self._open_shift(owner, location)
        item = self._make_item(owner, sku="SKU-POS-CASH", price="115.00", quantity=5)
        StockRecord.objects.create(item=item, location=location, quantity=5)

        self.client.force_login(owner)
        response = self.client.post(
            reverse("cash_register"),
            data=json.dumps(
                {
                    "location_id": location.pk,
                    "discount": 0,
                    "discount_type": "flat",
                    "payment_channel": "pos",
                    "amount_paid": "200.00",
                    "cart": [{"id": item.id, "quantity": 1}],
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["amount_paid"], "200.00")
        self.assertEqual(payload["change_due"], "85.00")
        sale = Sale.objects.get(pk=payload["sale_id"])
        self.assertEqual(sale.tender, "cash")
        self.assertEqual(sale.total_price, Decimal("115.00"))

    def test_cash_register_ignores_client_price_and_total_tampering(self):
        owner = self._make_user("register-price-tamper-owner")
        location = self._make_location(owner, "Main Branch")
        owner.profile.default_location = location
        owner.profile.save(update_fields=["default_location"])
        self._open_shift(owner, location)
        item = self._make_item(owner, sku="SKU-POS-TAMPER", price="230.00", quantity=5)
        StockRecord.objects.create(item=item, location=location, quantity=5)

        self.client.force_login(owner)
        response = self.client.post(
            reverse("cash_register"),
            data=json.dumps(
                {
                    "location_id": location.pk,
                    "discount": 0,
                    "discount_type": "flat",
                    "payment_channel": "pos",
                    "amount_paid": "230.00",
                    "total_price": "1.00",
                    "cart": [{"id": item.id, "quantity": 1, "price": "1.00", "total": "1.00"}],
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        sale = Sale.objects.get(pk=response.json()["sale_id"])
        line = sale.items.get()
        self.assertEqual(line.unit_price, Decimal("230.00"))
        self.assertEqual(line.total_price, Decimal("230.00"))
        self.assertEqual(sale.total_price, Decimal("230.00"))

    def test_cash_register_rejects_short_cash_before_persisting_sale(self):
        owner = self._make_user("register-short-cash-owner")
        location = self._make_location(owner, "Main Branch")
        owner.profile.default_location = location
        owner.profile.save(update_fields=["default_location"])
        shift = self._open_shift(owner, location)
        item = self._make_item(owner, sku="SKU-POS-SHORT", price="115.00", quantity=5)
        StockRecord.objects.create(item=item, location=location, quantity=5)
        self.client.force_login(owner)

        response = self.client.post(
            reverse("cash_register"),
            data=json.dumps(
                {
                    "location_id": location.pk,
                    "discount": 0,
                    "discount_type": "flat",
                    "payment_channel": "pos",
                    "amount_paid": "100.00",
                    "cart": [{"id": item.id, "quantity": 1}],
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("short by $15.00", response.json()["error"])
        self.assertFalse(Sale.objects.exists())
        self.assertEqual(StockRecord.objects.get(item=item, location=location).quantity, 5)
        shift.refresh_from_db()
        self.assertEqual(shift.total_sales, Decimal("0.00"))

    def test_cash_register_scan2pay_channel_records_card_sale(self):
        owner = self._make_user("register-scan2pay-owner")
        location = self._make_location(owner, "Main Branch")
        owner.profile.default_location = location
        owner.profile.save(update_fields=["default_location"])
        self._open_shift(owner, location)
        item = self._make_item(owner, sku="SKU-SCAN2PAY", price="115.00", quantity=5)
        StockRecord.objects.create(item=item, location=location, quantity=5)

        self.client.force_login(owner)
        response = self.client.post(
            reverse("cash_register"),
            data=json.dumps(
                {
                    "location_id": location.pk,
                    "discount": 0,
                    "discount_type": "flat",
                    "payment_channel": "scan2pay",
                    "cart": [{"id": item.id, "quantity": 1}],
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        sale = Sale.objects.get(pk=payload["sale_id"])
        self.assertEqual(payload["payment_channel"], "scan2pay")
        self.assertEqual(sale.tender, "card")

    def test_cash_register_invoice_channel_creates_issued_invoice(self):
        owner = self._make_user("register-invoice-owner")
        location = self._make_location(owner, "Main Branch")
        owner.profile.default_location = location
        owner.profile.save(update_fields=["default_location"])
        self._open_shift(owner, location)
        item = self._make_item(owner, sku="SKU-POS-INV", price="100.00", quantity=5)
        StockRecord.objects.create(item=item, location=location, quantity=5)

        self.client.force_login(owner)
        response = self.client.post(
            reverse("cash_register"),
            data=json.dumps(
                {
                    "location_id": location.pk,
                    "discount": 10,
                    "discount_type": "flat",
                    "payment_channel": "invoice",
                    "cart": [{"id": item.id, "quantity": 2}],
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        invoice = SalesInvoice.objects.get(pk=payload["invoice_id"])
        stock = StockRecord.objects.get(item=item, location=location)
        self.assertEqual(payload["type"], "invoice")
        self.assertEqual(invoice.status, "issued")
        self.assertEqual(invoice.subtotal, Decimal("200.00"))
        self.assertEqual(invoice.tax_amount, Decimal("24.78"))
        self.assertEqual(invoice.total_amount, Decimal("190.00"))
        self.assertEqual(invoice.items.count(), 1)
        self.assertEqual(Sale.objects.count(), 0)
        self.assertEqual(stock.quantity, 3)

    def test_cash_register_invoice_ignores_client_price_and_total_tampering(self):
        owner = self._make_user("register-invoice-tamper-owner")
        location = self._make_location(owner, "Main Branch")
        owner.profile.default_location = location
        owner.profile.save(update_fields=["default_location"])
        self._open_shift(owner, location)
        item = self._make_item(owner, sku="SKU-POS-INV-TAMPER", price="230.00", quantity=5)
        StockRecord.objects.create(item=item, location=location, quantity=5)

        self.client.force_login(owner)
        response = self.client.post(
            reverse("cash_register"),
            data=json.dumps(
                {
                    "location_id": location.pk,
                    "discount": 0,
                    "discount_type": "flat",
                    "payment_channel": "invoice",
                    "total_price": "1.00",
                    "cart": [{"id": item.id, "quantity": 1, "price": "1.00", "total": "1.00"}],
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        invoice = SalesInvoice.objects.get(pk=response.json()["invoice_id"])
        line = invoice.items.get()
        self.assertEqual(line.unit_price, Decimal("230.00"))
        self.assertEqual(line.line_total, Decimal("230.00"))
        self.assertEqual(invoice.subtotal, Decimal("230.00"))
        self.assertEqual(invoice.tax_amount, Decimal("30.00"))
        self.assertEqual(invoice.total_amount, Decimal("230.00"))

    def test_cash_register_requires_open_shift_for_selected_location(self):
        owner = self._make_user("register-location-owner")
        default_location = self._make_location(owner, "Main Branch")
        secondary_location = self._make_location(owner, "Downtown")
        owner.profile.default_location = default_location
        owner.profile.save(update_fields=["default_location"])
        self._open_shift(owner, default_location)
        item = self._make_item(owner, sku="SKU-POS-2", price="115.00", quantity=5)
        StockRecord.objects.create(item=item, location=secondary_location, quantity=5)

        self.client.force_login(owner)
        response = self.client.post(
            reverse("cash_register"),
            data=json.dumps(
                {
                    "location_id": secondary_location.pk,
                    "discount": 0,
                    "discount_type": "flat",
                    "tender": "cash",
                    "cart": [{"id": item.id, "quantity": 1}],
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(Sale.objects.count(), 0)

    def test_receipt_uses_saved_sale_totals_without_readding_tax(self):
        owner = self._make_user("receipt-owner")
        location = self._make_location(owner, "Main Branch")
        item = self._make_item(owner, sku="SKU-RECEIPT", price="230.00", quantity=8)
        StockRecord.objects.create(item=item, location=location, quantity=8)
        sale = Sale.objects.create(

            owner=owner,
            cashier=owner,
            location=location,
            discount=Decimal("10.00"),
            tender="cash",
        )
        SaleItem.objects.create(
            sale=sale,
            item=item,
            quantity=2,
            unit_price=Decimal("230.00"),
            total_price=Decimal("460.00"),
        )
        sale.recalculate_totals()

        self.client.force_login(owner)
        response = self.client.get(reverse("view_receipt", args=[sale.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["subtotal"], Decimal("400.00"))
        self.assertEqual(response.context["tax_amount"], Decimal("60.00"))
        self.assertEqual(response.context["grand_total"], Decimal("450.00"))

    def test_receipt_shows_amount_paid_and_change_given_for_cash_sale(self):
        owner = self._make_user("receipt-change-owner")
        location = self._make_location(owner, "Main Branch")
        item = self._make_item(owner, sku="SKU-RECEIPT-CHANGE", price="115.00", quantity=8)
        StockRecord.objects.create(item=item, location=location, quantity=8)
        sale = Sale.objects.create(
            owner=owner,
            cashier=owner,
            location=location,
            tender="cash",
            subtotal=Decimal("100.00"),
            gct_amount=Decimal("15.00"),
            total_price=Decimal("115.00"),
            amount_paid=Decimal("200.00"),
            change_due=Decimal("85.00"),
        )
        SaleItem.objects.create(
            sale=sale,
            item=item,
            quantity=1,
            unit_price=Decimal("115.00"),
            net_amount=Decimal("100.00"),
            tax_amount=Decimal("15.00"),
            total_price=Decimal("115.00"),
        )

        self.client.force_login(owner)
        response = self.client.get(reverse("view_receipt", args=[sale.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Amount Paid")
        self.assertContains(response, "$200.00")
        self.assertContains(response, "Change Given")
        self.assertContains(response, "$85.00")

    def test_daily_summary_delivery_collection_uses_real_invoice_items(self):
        owner = self._make_user("daily-real-items-owner")
        location = self._make_location(owner, "Main Branch")
        delivered_item = self._make_item(owner, name="Delivered Widget", sku="SKU-DELIVERED", price="60.00", quantity=8)
        collected_item = self._make_item(owner, name="Collected Widget", sku="SKU-COLLECTED", price="50.00", quantity=8)
        awaiting_item = self._make_item(owner, name="Awaiting Widget", sku="SKU-AWAITING", price="75.00", quantity=8)
        now = timezone.now()

        delivered_invoice = SalesInvoice.objects.create(
            owner=owner,
            location=location,
            status="paid",
            collection_status="collected_after_hold",
            collection_status_changed_at=now,
            collected_at=now,
            collection_recorded_by=owner,
            subtotal=Decimal("120.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("120.00"),
        )
        SalesInvoiceItem.objects.create(
            invoice=delivered_invoice,
            item=delivered_item,
            item_name=delivered_item.name,
            quantity=2,
            unit_price=Decimal("60.00"),
        )

        paid_invoice = SalesInvoice.objects.create(
            owner=owner,
            location=location,
            status="paid",
            collection_status="collected_immediately",
            collection_status_changed_at=now,
            collected_at=now,
            collection_recorded_by=owner,
            subtotal=Decimal("150.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("150.00"),
        )
        SalesInvoiceItem.objects.create(
            invoice=paid_invoice,
            item=collected_item,
            item_name=collected_item.name,
            quantity=3,
            unit_price=Decimal("50.00"),
        )
        SalesInvoicePayment.objects.create(
            invoice=paid_invoice,
            received_by=owner,
            amount=Decimal("150.00"),
            payment_method="cash",
        )

        held_invoice = SalesInvoice.objects.create(
            owner=owner,
            location=location,
            status="paid",
            collection_status="awaiting_collection",
            collection_status_changed_at=now,
            collection_recorded_by=owner,
            subtotal=Decimal("150.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("150.00"),
        )
        SalesInvoiceItem.objects.create(
            invoice=held_invoice,
            item=awaiting_item,
            item_name=awaiting_item.name,
            quantity=2,
            unit_price=Decimal("75.00"),
        )

        self.client.force_login(owner)
        response = self.client.get(reverse("daily_summary"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["delivered_today"], [{"name": "Delivered Widget", "quantity": 2}])
        self.assertEqual(response.context["collected_by_client"], [{"name": "Collected Widget", "quantity": 3}])
        self.assertEqual(response.context["awaiting_collection"], [{"name": "Awaiting Widget", "quantity": 2}])
        self.assertContains(response, "Delivered Widget")
        self.assertContains(response, "Collected Widget")
        self.assertContains(response, "Awaiting Widget")
        self.assertNotContains(response, "Solar Panels")
        self.assertNotContains(response, "Batteries")
        self.assertNotContains(response, "Inverters")

    def test_deliveries_collections_groups_items_under_categories(self):
        owner = self._make_user("deliveries-group-owner")
        location = self._make_location(owner, "Main Branch")
        beverage_item = self._make_item(owner, name="Grape Soda", sku="SKU-GRAPE", price="50.00", quantity=10)
        snack_item = self._make_item(owner, name="Plantain Chips", sku="SKU-CHIPS", price="35.00", quantity=12)

        delivered_invoice = SalesInvoice.objects.create(
            owner=owner,
            location=location,
            status="paid",
            collection_status="collected_after_hold",
            collection_status_changed_at=timezone.now(),
            collected_at=timezone.now(),
            collection_recorded_by=owner,
            subtotal=Decimal("170.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("170.00"),
        )
        SalesInvoiceItem.objects.create(
            invoice=delivered_invoice,
            item=beverage_item,
            item_name=beverage_item.name,
            quantity=2,
            unit_price=Decimal("50.00"),
        )
        SalesInvoiceItem.objects.create(
            invoice=delivered_invoice,
            item=snack_item,
            item_name=snack_item.name,
            quantity=2,
            unit_price=Decimal("35.00"),
        )
        immediate_invoice = SalesInvoice.objects.create(
            owner=owner,
            location=location,
            status="paid",
            collection_status="collected_immediately",
            collection_status_changed_at=timezone.now(),
            collected_at=timezone.now(),
            collection_recorded_by=owner,
            subtotal=Decimal("50.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("50.00"),
        )
        SalesInvoiceItem.objects.create(
            invoice=immediate_invoice,
            item=beverage_item,
            item_name=beverage_item.name,
            quantity=1,
            unit_price=Decimal("50.00"),
        )
        held_invoice = SalesInvoice.objects.create(
            owner=owner,
            location=location,
            status="paid",
            collection_status="awaiting_collection",
            collection_status_changed_at=timezone.now(),
            collection_recorded_by=owner,
            subtotal=Decimal("105.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("105.00"),
        )
        SalesInvoiceItem.objects.create(
            invoice=held_invoice,
            item=snack_item,
            item_name=snack_item.name,
            quantity=3,
            unit_price=Decimal("35.00"),
        )

        self.client.force_login(owner)
        response = self.client.get(reverse("deliveries_collections"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["delivered_today_grouped"],
            [
                {"category": snack_item.category.name, "items": [{"name": "Plantain Chips", "quantity": 2}]},
                {"category": beverage_item.category.name, "items": [{"name": "Grape Soda", "quantity": 2}]},
            ],
        )
        self.assertEqual(
            response.context["collected_by_client_grouped"],
            [
                {"category": beverage_item.category.name, "items": [{"name": "Grape Soda", "quantity": 1}]},
            ],
        )
        self.assertEqual(
            response.context["awaiting_collection_grouped"],
            [
                {"category": snack_item.category.name, "items": [{"name": "Plantain Chips", "quantity": 3}]},
            ],
        )
        self.assertEqual(len(response.context["remaining_in_store_orders"]), 1)
        self.assertEqual(
            response.context["remaining_in_store_orders"][0]["invoice"],
            held_invoice,
        )
        self.assertEqual(response.context["remaining_in_store_orders"][0]["total_quantity"], 3)
        self.assertContains(response, "Mark Picked Up")
        self.assertContains(response, held_invoice.invoice_no)
        self.assertContains(response, beverage_item.category.name)
        self.assertContains(response, snack_item.category.name)
        self.assertContains(response, "Grape Soda")
        self.assertContains(response, "Plantain Chips")

    def test_sales_history_search_matches_receipt_number_text(self):
        owner = self._make_user("history-owner")
        location = self._make_location(owner, "Main Branch")
        item = self._make_item(owner, sku="SKU-HISTORY", price="115.00", quantity=5)
        StockRecord.objects.create(item=item, location=location, quantity=5)
        sale = Sale.objects.create(

            owner=owner,
            cashier=owner,
            location=location,
            receipt_no=12345,
            tender="cash",
            subtotal=Decimal("100.00"),
            gct_amount=Decimal("15.00"),
            total_price=Decimal("115.00"),
        )
        SaleItem.objects.create(
            sale=sale,
            item=item,
            quantity=1,
            unit_price=Decimal("115.00"),
            net_amount=Decimal("100.00"),
            tax_amount=Decimal("15.00"),
            total_price=Decimal("115.00"),
        )

        self.client.force_login(owner)
        response = self.client.get(reverse("sales_history"), {"q": "#12345"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "#12345")


class WeekThreeTransferIntegrityTests(TestCase):
    def _make_user(self, username):
        user = User.objects.create_user(username=username, password="password123")
        profile = UserProfile.for_user(user)
        profile.role = "admin"
        profile.status = "active"
        profile.plan = "PRO"
        profile.pro_expires = timezone.now().date() + timedelta(days=30)
        profile.plan_end = timezone.now() + timedelta(days=30)
        profile.parent_admin = None
        profile.save()
        return user

    def _make_location(self, owner, name):
        return Location.objects.create(owner=owner, name=name)

    def _make_item(self, owner, name="Item A", sku="SKU-1", price="100.00", quantity=10):
        category = Category.objects.create(name=f"Category-{sku}")
        brand = Brand.objects.create(name=f"Brand-{sku}")
        return Item.objects.create(
            owner=owner,
            name=name,
            sku=sku,
            category=category,
            brand=brand,
            cost_price=Decimal("50.00"),
            price=Decimal(price),
        )

    def test_api_transfer_stock_moves_inventory_between_locations(self):
        owner = self._make_user("transfer-owner")
        source = self._make_location(owner, "Warehouse")
        destination = self._make_location(owner, "Storefront")
        item = self._make_item(owner, sku="SKU-XFER", quantity=10)
        StockRecord.objects.create(item=item, location=source, quantity=7)
        StockRecord.objects.create(item=item, location=destination, quantity=1)

        self.client.force_login(owner)
        response = self.client.post(
            reverse("api_transfer_stock"),
            data={
                "sku": "SKU-XFER",
                "from_location_id": source.pk,
                "to_location_id": destination.pk,
                "quantity": 3,
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(StockTransfer.objects.count(), 1)
        self.assertEqual(StockTransfer.objects.get().status, "COMPLETED")
        self.assertEqual(StockRecord.objects.get(item=item, location=source).quantity, 4)
        self.assertEqual(StockRecord.objects.get(item=item, location=destination).quantity, 4)
        item.refresh_from_db()
        self.assertEqual(item.quantity, 8)



class WeekFourReadinessTests(TestCase):
    def _make_user(self, username):
        user = User.objects.create_user(username=username, password="password123")
        profile = UserProfile.for_user(user)
        profile.role = "admin"
        profile.status = "active"
        profile.plan = "PRO"
        profile.pro_expires = timezone.now().date() + timedelta(days=30)
        profile.plan_end = timezone.now() + timedelta(days=30)
        profile.parent_admin = None
        profile.save()
        return user

    def _make_location(self, owner, name):
        return Location.objects.create(owner=owner, name=name)

    def _make_staff_user(self, username, owner, role="manager", default_location=None):
        user = User.objects.create_user(username=username, password="password123")
        profile = UserProfile.for_user(user)
        profile.role = role
        profile.status = "active"
        profile.plan = "PRO"
        profile.pro_expires = timezone.now().date() + timedelta(days=30)
        profile.plan_end = timezone.now() + timedelta(days=30)
        profile.parent_admin = UserProfile.for_user(owner)
        profile.default_location = default_location
        profile.save()
        return user

    def _make_item(self, owner, name="Item A", sku="SKU-INV", price="100.00", quantity=10):
        category = Category.objects.create(name=f"Category-{sku}")
        brand = Brand.objects.create(name=f"Brand-{sku}")
        return Item.objects.create(
            owner=owner,
            name=name,
            sku=sku,
            category=category,
            brand=brand,
            cost_price=Decimal("50.00"),
            price=Decimal(price),
        )


    def _make_sales_invoice(self, owner, total_amount="460.00"):
        location = self._make_location(owner, "Main Branch")
        customer = Customer.objects.create(owner=owner, name="ACME Retail")
        item = self._make_item(owner, sku="SKU-SINV", price="230.00", quantity=10)
        invoice = SalesInvoice.objects.create(
            owner=owner,
            created_by=owner,
            customer=customer,
            location=location,
            subtotal=Decimal("400.00"),
            tax_amount=Decimal("60.00"),
            total_amount=Decimal(total_amount),
        )
        SalesInvoiceItem.objects.create(
            invoice=invoice,
            item=item,
            quantity=2,
            unit_price=Decimal("230.00"),
            line_total=Decimal("460.00"),
        )
        return invoice

    def test_sales_quotation_create_supports_discount_and_tax_removal(self):
        owner = self._make_user("quote-adjust-owner")
        item = self._make_item(owner, sku="SKU-QUOTE-ADJ", price="100.00", quantity=10)
        self.client.force_login(owner)

        response = self.client.post(
            reverse("sales_quotation_create"),
            data={
                "customer_id": "",
                "valid_until": "2026-05-31",
                "notes": "Seasonal offer",
                "tax_mode": "none",
                "tax_rate_percent": "0",
                "discount_type": "percent",
                "discount_value": "10",
                "item_id[]": [str(item.id)],
                "quantity[]": ["2"],
                "unit_price[]": ["100.00"],
            },
        )

        self.assertEqual(response.status_code, 302)
        quotation = SalesQuotation.objects.latest("id")
        self.assertEqual(quotation.subtotal, Decimal("200.00"))
        self.assertEqual(quotation.tax_amount, Decimal("0.00"))
        self.assertEqual(quotation.total_amount, Decimal("180.00"))
        self.assertIn("Seasonal offer", quotation.notes)
        self.assertIn('"discount_type":"percent"', quotation.notes)
        self.assertIn('"tax_mode":"none"', quotation.notes)

    def test_tax_exempt_customer_forces_zero_tax_on_quotation(self):
        owner = self._make_user("quote-tax-exempt-owner")
        customer = Customer.objects.create(owner=owner, name="Tax Exempt Buyer", trn="123456789", is_tax_exempt=True)
        item = self._make_item(owner, sku="SKU-QUOTE-EXEMPT", price="100.00", quantity=10)
        self.client.force_login(owner)

        response = self.client.post(
            reverse("sales_quotation_create"),
            data={
                "customer_id": str(customer.id),
                "valid_until": "2026-05-31",
                "notes": "Exempt customer quote",
                "tax_mode": "custom",
                "tax_rate_percent": "15",
                "discount_type": "flat",
                "discount_value": "0",
                "item_id[]": [str(item.id)],
                "quantity[]": ["2"],
                "unit_price[]": ["100.00"],
            },
        )

        self.assertEqual(response.status_code, 302)
        quotation = SalesQuotation.objects.latest("id")
        self.assertEqual(quotation.customer, customer)
        self.assertEqual(quotation.tax_amount, Decimal("0.00"))
        self.assertEqual(quotation.total_amount, Decimal("200.00"))
        self.assertIn('"customer_tax_exempt":"true"', quotation.notes)

    def test_sales_invoice_create_supports_custom_tax_and_flat_discount(self):
        owner = self._make_user("invoice-adjust-owner")
        location = self._make_location(owner, "Main Branch")
        customer = Customer.objects.create(owner=owner, name="Customer One")
        item = self._make_item(owner, sku="SKU-INV-ADJ", price="80.00", quantity=10)
        StockRecord.objects.create(item=item, location=location, quantity=10)
        self.client.force_login(owner)

        response = self.client.post(
            f"{reverse('sales_invoice_create')}?source=scratch",
            data={
                "source_mode": "scratch",
                "customer_id": str(customer.id),
                "location_id": str(location.id),
                "notes": "Manual invoice terms",
                "due_date": "2026-06-15",
                "tax_mode": "custom",
                "tax_rate_percent": "12.5",
                "discount_type": "flat",
                "discount_value": "20.00",
                "item_id[]": [str(item.id)],
                "quantity[]": ["2"],
                "unit_price[]": ["80.00"],
            },
        )

        self.assertEqual(response.status_code, 302)
        invoice = SalesInvoice.objects.latest("id")
        item.refresh_from_db()
        stock = StockRecord.objects.get(item=item, location=location)
        self.assertEqual(invoice.subtotal, Decimal("160.00"))
        self.assertEqual(invoice.due_date.isoformat(), "2026-06-15")
        self.assertEqual(invoice.tax_amount, Decimal("17.50"))
        self.assertEqual(invoice.total_amount, Decimal("157.50"))
        self.assertEqual(item.quantity, 8)
        self.assertEqual(stock.quantity, 8)
        self.assertIn("Manual invoice terms", invoice.notes)
        self.assertIn('"discount_type":"flat"', invoice.notes)
        self.assertIn('"tax_rate_percent":"12.50"', invoice.notes)

    def test_tax_exempt_customer_forces_zero_tax_on_direct_invoice(self):
        owner = self._make_user("invoice-tax-exempt-owner")
        location = self._make_location(owner, "Main Branch")
        customer = Customer.objects.create(owner=owner, name="GCT Exempt Client", is_tax_exempt=True)
        item = self._make_item(owner, sku="SKU-INV-EXEMPT", price="100.00", quantity=10)
        StockRecord.objects.create(item=item, location=location, quantity=10)
        self.client.force_login(owner)

        response = self.client.post(
            f"{reverse('sales_invoice_create')}?source=scratch",
            data={
                "source_mode": "scratch",
                "customer_id": str(customer.id),
                "location_id": str(location.id),
                "notes": "Exempt invoice",
                "tax_mode": "custom",
                "tax_rate_percent": "15",
                "discount_type": "flat",
                "discount_value": "0",
                "item_id[]": [str(item.id)],
                "quantity[]": ["2"],
                "unit_price[]": ["100.00"],
            },
        )

        self.assertEqual(response.status_code, 302)
        invoice = SalesInvoice.objects.latest("id")
        self.assertEqual(invoice.customer, customer)
        self.assertEqual(invoice.tax_amount, Decimal("0.00"))
        self.assertEqual(invoice.total_amount, Decimal("200.00"))
        self.assertIn('"customer_tax_exempt":"true"', invoice.notes)

    def test_sales_quotation_list_auto_flags_expired_drafts(self):
        owner = self._make_user("quote-expiry-owner")
        customer = Customer.objects.create(owner=owner, name="Expiry Customer")
        expired_quote = SalesQuotation.objects.create(
            owner=owner,
            created_by=owner,
            customer=customer,
            valid_until=timezone.localdate() - timedelta(days=1),
            total_amount=Decimal("100.00"),
        )
        active_quote = SalesQuotation.objects.create(
            owner=owner,
            created_by=owner,
            customer=customer,
            valid_until=timezone.localdate() + timedelta(days=3),
            total_amount=Decimal("200.00"),
        )
        self.client.force_login(owner)

        response = self.client.get(reverse("sales_quotation_list"))

        self.assertEqual(response.status_code, 200)
        expired_quote.refresh_from_db()
        active_quote.refresh_from_db()
        self.assertEqual(expired_quote.status, "expired")
        self.assertEqual(active_quote.status, "draft")
        self.assertEqual(response.context["quotation_summary"]["expired_count"], 1)
        self.assertEqual(response.context["quotation_summary"]["expiring_soon_count"], 1)
        self.assertContains(response, "Expired")

        expired_response = self.client.get(reverse("sales_quotation_list"), {"status": "expired"})
        self.assertEqual(expired_response.status_code, 200)
        self.assertContains(expired_response, expired_quote.quote_no)
        self.assertNotContains(expired_response, active_quote.quote_no)

    def test_expired_quotation_cannot_be_converted_to_invoice(self):
        owner = self._make_user("quote-expiry-convert-owner")
        location = self._make_location(owner, "Main Branch")
        item = self._make_item(owner, sku="SKU-QUOTE-EXPIRE", price="100.00", quantity=5)
        StockRecord.objects.create(item=item, location=location, quantity=5)
        quotation = SalesQuotation.objects.create(
            owner=owner,
            created_by=owner,
            valid_until=timezone.localdate() - timedelta(days=1),
            subtotal=Decimal("100.00"),
            tax_amount=Decimal("15.00"),
            total_amount=Decimal("115.00"),
        )
        SalesQuotationItem.objects.create(
            quotation=quotation,
            item=item,
            item_name=item.name,
            quantity=1,
            unit_price=Decimal("100.00"),
            line_total=Decimal("100.00"),
        )
        self.client.force_login(owner)

        response = self.client.post(
            f"{reverse('sales_invoice_create')}?source=quotation",
            {
                "source_mode": "quotation",
                "quotation_id": str(quotation.id),
                "location_id": str(location.id),
            },
        )

        self.assertEqual(response.status_code, 302)
        quotation.refresh_from_db()
        self.assertEqual(quotation.status, "expired")
        self.assertFalse(SalesInvoice.objects.filter(quotation=quotation).exists())

    def test_quotation_valid_until_becomes_invoice_due_date_and_document_date(self):
        owner = self._make_user("quote-due-date-owner")
        location = self._make_location(owner, "Main Branch")
        customer = Customer.objects.create(owner=owner, name="Due Date Customer")
        item = self._make_item(owner, sku="SKU-QUOTE-DUE", price="100.00", quantity=5)
        StockRecord.objects.create(item=item, location=location, quantity=5)
        quotation = SalesQuotation.objects.create(
            owner=owner,
            created_by=owner,
            customer=customer,
            valid_until=timezone.localdate() + timedelta(days=10),
            subtotal=Decimal("100.00"),
            tax_amount=Decimal("15.00"),
            total_amount=Decimal("115.00"),
        )
        SalesQuotationItem.objects.create(
            quotation=quotation,
            item=item,
            item_name=item.name,
            quantity=1,
            unit_price=Decimal("100.00"),
            line_total=Decimal("100.00"),
        )
        self.client.force_login(owner)

        response = self.client.post(
            f"{reverse('sales_invoice_create')}?source=quotation",
            {
                "source_mode": "quotation",
                "quotation_id": str(quotation.id),
                "location_id": str(location.id),
            },
        )

        self.assertEqual(response.status_code, 302)
        invoice = SalesInvoice.objects.get(quotation=quotation)
        self.assertEqual(invoice.due_date, quotation.valid_until)
        context = _sales_document_email_context(invoice, "invoice")
        self.assertEqual(context["expiry_label"], "Due Date")
        self.assertEqual(context["expiry_date"], quotation.valid_until)

    def test_sales_quotation_edit_updates_existing_quote_lines(self):
        owner = self._make_user("quote-edit-owner")
        customer = Customer.objects.create(owner=owner, name="Original Customer")
        item = self._make_item(owner, sku="SKU-QUOTE-EDIT", price="100.00", quantity=10)
        quotation = SalesQuotation.objects.create(
            owner=owner,
            created_by=owner,
            customer=customer,
            valid_until=timezone.localdate() + timedelta(days=5),
            subtotal=Decimal("100.00"),
            tax_amount=Decimal("15.00"),
            total_amount=Decimal("115.00"),
        )
        SalesQuotationItem.objects.create(
            quotation=quotation,
            item=item,
            item_name=item.name,
            quantity=1,
            unit_price=Decimal("100.00"),
            line_total=Decimal("100.00"),
        )
        self.client.force_login(owner)

        edit_response = self.client.get(reverse("sales_quotation_edit", args=[quotation.id]))
        self.assertEqual(edit_response.status_code, 200)
        self.assertContains(edit_response, "quoteInitialLines")
        self.assertContains(edit_response, "Update Quotation")

        response = self.client.post(
            reverse("sales_quotation_edit", args=[quotation.id]),
            data={
                "customer_id": str(customer.id),
                "valid_until": "2026-07-31",
                "notes": "Updated terms",
                "tax_mode": "none",
                "tax_rate_percent": "0",
                "discount_type": "flat",
                "discount_value": "10.00",
                "item_id[]": [str(item.id)],
                "quantity[]": ["3"],
                "unit_price[]": ["50.00"],
            },
        )

        self.assertRedirects(response, reverse("sales_quotation_detail", args=[quotation.id]))
        quotation.refresh_from_db()
        line = quotation.items.get()
        self.assertEqual(quotation.subtotal, Decimal("150.00"))
        self.assertEqual(quotation.tax_amount, Decimal("0.00"))
        self.assertEqual(quotation.total_amount, Decimal("140.00"))
        self.assertEqual(quotation.valid_until.isoformat(), "2026-07-31")
        self.assertIn("Updated terms", quotation.notes)
        self.assertEqual(line.quantity, 3)
        self.assertEqual(line.unit_price, Decimal("50.00"))

    def test_sales_quotation_copy_creates_new_quote_for_target_customer(self):
        owner = self._make_user("quote-copy-owner")
        source_customer = Customer.objects.create(owner=owner, name="Source Customer")
        target_customer = Customer.objects.create(owner=owner, name="Target Customer")
        item = self._make_item(owner, sku="SKU-QUOTE-COPY", price="75.00", quantity=10)
        quotation = SalesQuotation.objects.create(
            owner=owner,
            created_by=owner,
            customer=source_customer,
            valid_until=timezone.localdate() + timedelta(days=5),
            subtotal=Decimal("150.00"),
            tax_amount=Decimal("22.50"),
            total_amount=Decimal("172.50"),
            notes="Copy these terms",
        )
        SalesQuotationItem.objects.create(
            quotation=quotation,
            item=item,
            item_name=item.name,
            quantity=2,
            unit_price=Decimal("75.00"),
            line_total=Decimal("150.00"),
        )
        self.client.force_login(owner)

        response = self.client.post(
            reverse("sales_quotation_copy", args=[quotation.id]),
            data={"copy_customer_id": str(target_customer.id)},
        )

        copied_quote = SalesQuotation.objects.exclude(id=quotation.id).latest("id")
        self.assertRedirects(response, reverse("sales_quotation_detail", args=[copied_quote.id]))
        self.assertEqual(copied_quote.customer, target_customer)
        self.assertEqual(copied_quote.status, "draft")
        self.assertEqual(copied_quote.total_amount, quotation.total_amount)
        self.assertEqual(copied_quote.items.count(), 1)
        copied_line = copied_quote.items.get()
        self.assertEqual(copied_line.item, item)
        self.assertEqual(copied_line.quantity, 2)

    def test_sales_quotation_can_create_multiple_invoices(self):
        owner = self._make_user("quote-multi-invoice-owner")
        location = self._make_location(owner, "Main Branch")
        item = self._make_item(owner, sku="SKU-QUOTE-MULTI", price="100.00", quantity=5)
        StockRecord.objects.create(item=item, location=location, quantity=5)
        quotation = SalesQuotation.objects.create(
            owner=owner,
            created_by=owner,
            valid_until=timezone.localdate() + timedelta(days=5),
            subtotal=Decimal("100.00"),
            tax_amount=Decimal("15.00"),
            total_amount=Decimal("115.00"),
        )
        SalesQuotationItem.objects.create(
            quotation=quotation,
            item=item,
            item_name=item.name,
            quantity=1,
            unit_price=Decimal("100.00"),
            line_total=Decimal("100.00"),
        )
        self.client.force_login(owner)

        for _ in range(2):
            response = self.client.post(
                reverse("sales_quotation_convert_to_invoice", args=[quotation.id]),
                {"location_id": str(location.id)},
            )
            self.assertEqual(response.status_code, 302)

        quotation.refresh_from_db()
        stock = StockRecord.objects.get(item=item, location=location)
        self.assertEqual(quotation.status, "converted")
        self.assertEqual(SalesInvoice.objects.filter(quotation=quotation).count(), 2)
        self.assertEqual(stock.quantity, 3)

    def test_api_health_reports_runtime_checks(self):
        response = self.client.get(reverse("api_health"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "ok")
        self.assertTrue(payload["checks"]["database"]["ok"])
        self.assertTrue(payload["checks"]["migrations"]["ok"])
        self.assertTrue(payload["checks"]["media_storage"]["ok"])
        self.assertTrue(payload["checks"]["cache"]["ok"])
        self.assertTrue(payload["checks"]["email"]["ok"])
        self.assertTrue(payload["checks"]["logging"]["ok"])

    def test_render_readiness_reports_login_dependencies(self):
        response = self.client.get(reverse("render_readiness"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "ok")
        self.assertTrue(payload["checks"]["database"]["ok"])
        self.assertTrue(payload["checks"]["cache"]["ok"])
        self.assertTrue(payload["checks"]["email"]["ok"])

    @override_settings(
        DEBUG=False,
        EMAIL_PROVIDER="resend",
        EMAIL_BACKEND=CONSOLE_BACKEND,
        RESEND_API_KEY="",
        DEFAULT_FROM_EMAIL="QuickStock JA <noreply@quickstockja.com>",
    )
    def test_api_health_is_degraded_when_render_email_is_not_configured(self):
        response = self.client.get(reverse("api_health"))

        self.assertEqual(response.status_code, 503)
        payload = response.json()
        self.assertEqual(payload["status"], "degraded")
        self.assertFalse(payload["checks"]["email"]["ok"])

    def test_check_runtime_health_command_passes(self):
        call_command("check_runtime_health")

    @override_settings(
        LOGGING={
            "version": 1,
            "disable_existing_loggers": False,
            "handlers": {
                "file": {
                    "class": "logging.FileHandler",
                    "filename": "/proc/quickstock-unwritable.log",
                },
            },
        }
    )
    def test_api_health_reports_degraded_when_log_path_is_not_writable(self):
        response = self.client.get(reverse("api_health"))

        self.assertEqual(response.status_code, 503)
        payload = response.json()
        self.assertEqual(payload["status"], "degraded")
        self.assertFalse(payload["checks"]["logging"]["ok"])

    def test_status_page_reflects_runtime_health(self):
        response = self.client.get(reverse("status"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["status"], "ok")
        self.assertContains(response, "All systems operational")
        self.assertContains(response, "Database:")

    def test_download_software_returns_404_when_unconfigured(self):
        owner = self._make_user("download-unconfigured-owner")
        self.client.force_login(owner)
        response = self.client.get(reverse("download_software"))

        self.assertEqual(response.status_code, 404)

    @override_settings(DOWNLOAD_SOFTWARE_URL="https://downloads.quickstock.test/quickstock.exe")
    def test_download_software_redirects_when_configured(self):
        owner = self._make_user("download-configured-owner")
        self.client.force_login(owner)
        response = self.client.get(reverse("download_software"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "https://downloads.quickstock.test/quickstock.exe")

    def test_download_software_requires_sign_in(self):
        response = self.client.get(reverse("download_software"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

    @override_settings(
        WIPAY_CONTACT_PHONE_DEFAULT="+1-876-555-0100",
        WIPAY_ACCOUNT_NUMBER_SANDBOX="1234567890",
        WIPAY_API_KEY_SANDBOX="123",
    )
    @patch("inventory.views._probe_wipay_availability", return_value=(True, ""))
    def test_wipay_subscription_checkout_uses_configured_phone_fallback(self, _mock_probe):
        owner = self._make_user("wipay-phone-owner")
        self.client.force_login(owner)

        response = self.client.post(
            reverse("create_wipay_checkout_session"),
            data=json.dumps({"billing_cycle": "monthly"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("%2B1-876-555-0100", response.json()["url"])

    @override_settings(
        WIPAY_ACCOUNT_NUMBER_SANDBOX="1234567890",
        WIPAY_API_KEY_SANDBOX="123",
    )
    @patch("inventory.views._probe_wipay_availability", return_value=(True, ""))
    def test_wipay_subscription_checkout_returns_to_verification_handler(self, _mock_probe):
        owner = self._make_user("wipay-response-owner")
        self.client.force_login(owner)

        response = self.client.post(
            reverse("create_wipay_checkout_session"),
            data=json.dumps({"billing_cycle": "monthly"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        checkout_url = response.json()["url"]
        params = parse_qs(urlparse(checkout_url).query)
        self.assertEqual(urlparse(params["response_url"][0]).path, reverse("wipay_response"))
        self.assertEqual(urlparse(params["return_url"][0]).path, reverse("wipay_response"))

    @override_settings(
        WIPAY_CONTACT_PHONE_DEFAULT="+1-876-555-0100",
        WIPAY_ACCOUNT_NUMBER_SANDBOX="1234567890",
        WIPAY_API_KEY_SANDBOX="123",
    )
    @patch("inventory.views._probe_wipay_availability", return_value=(True, ""))
    def test_wipay_subscription_checkout_prefers_profile_phone(self, _mock_probe):
        owner = self._make_user("wipay-profile-phone-owner")
        owner.profile.receipt_contact_phone = "876-777-0000"
        owner.profile.save(update_fields=["receipt_contact_phone"])
        self.client.force_login(owner)

        response = self.client.post(
            reverse("create_wipay_checkout_session"),
            data=json.dumps({"billing_cycle": "monthly"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("876-777-0000", response.json()["url"])

    @override_settings(
        WIPAY_ORIGIN="QuickStock JA POS!!!",
        WIPAY_ACCOUNT_NUMBER_SANDBOX="1234567890",
        WIPAY_API_KEY_SANDBOX="123",
    )
    @patch("inventory.views._probe_wipay_availability", return_value=(True, ""))
    def test_wipay_subscription_checkout_sanitizes_origin(self, _mock_probe):
        owner = self._make_user("wipay-origin-owner")
        self.client.force_login(owner)

        response = self.client.post(
            reverse("create_wipay_checkout_session"),
            data=json.dumps({"billing_cycle": "monthly"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("origin=QuickStock_JA_POS", response.json()["url"])

    @override_settings(
        WIPAY_ORIGIN="QuickStock JA POS!!!",
        WIPAY_ACCOUNT_NUMBER_SANDBOX="1234567890",
        WIPAY_API_KEY_SANDBOX="123",
    )
    @patch("inventory.views._probe_wipay_availability", return_value=(True, ""))
    def test_pos_card_checkout_sanitizes_wipay_origin(self, _mock_probe):
        owner = self._make_user("wipay-pos-origin-owner")
        self.client.force_login(owner)

        response = self.client.post(
            reverse("card_checkout"),
            data=json.dumps({"total": "100.00", "reference": "POS-ORIGIN-1"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("origin=QuickStock_JA_POS", response.json()["checkout_url"])

    def test_sales_invoice_payment_records_partial_payment_and_balance(self):
        owner = self._make_user("invoice-owner")
        invoice = self._make_sales_invoice(owner)
        self.client.force_login(owner)

        response = self.client.post(
            reverse("sales_invoice_payment", args=[invoice.pk]),
            data={
                "amount": "200.00",
                "payment_method": "cash",
                "reference": "PARTIAL-1",
                "notes": "First payment",
            },
        )

        self.assertEqual(response.status_code, 302)
        invoice.refresh_from_db()
        payment = SalesInvoicePayment.objects.get(invoice=invoice)
        self.assertEqual(payment.amount, Decimal("200.00"))
        self.assertEqual(invoice.status, "issued")
        self.assertEqual(invoice.total_paid_amount, Decimal("200.00"))
        self.assertEqual(invoice.balance_due, Decimal("260.00"))

    def test_sales_invoice_payment_requires_reference_for_non_cash_payment(self):
        owner = self._make_user("invoice-owner-noncash-reference")
        invoice = self._make_sales_invoice(owner)
        self.client.force_login(owner)

        response = self.client.post(
            reverse("sales_invoice_payment", args=[invoice.pk]),
            data={
                "amount": "200.00",
                "payment_method": "bank_transfer",
                "reference": "",
                "notes": "Missing bank reference",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(SalesInvoicePayment.objects.filter(invoice=invoice).exists())
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, "issued")

    def test_sales_invoice_payment_stores_overpayment_as_customer_credit(self):
        owner = self._make_user("invoice-owner-credit")
        customer = Customer.objects.create(owner=owner, name="Credit Customer")
        invoice = self._make_sales_invoice(owner)
        invoice.customer = customer
        invoice.save(update_fields=["customer"])
        self.client.force_login(owner)

        response = self.client.post(
            reverse("sales_invoice_payment", args=[invoice.pk]),
            data={
                "amount": "500.00",
                "payment_method": "cash",
                "reference": "OVERPAY-1",
                "notes": "Customer paid extra",
            },
        )

        self.assertEqual(response.status_code, 302)
        invoice.refresh_from_db()
        customer.refresh_from_db()
        payment = SalesInvoicePayment.objects.get(invoice=invoice)
        self.assertEqual(payment.amount, Decimal("500.00"))
        self.assertEqual(invoice.status, "paid")
        self.assertEqual(invoice.balance_due, Decimal("0.00"))
        self.assertEqual(customer.credit_balance, Decimal("40.00"))

    def test_sales_invoice_payment_model_rejects_invalid_ledger_amounts(self):
        owner = self._make_user("invoice-owner-invalid-ledger")
        invoice = self._make_sales_invoice(owner)

        with self.assertRaises(ValidationError):
            SalesInvoicePayment.objects.create(
                invoice=invoice,
                received_by=owner,
                amount=Decimal("0.00"),
                payment_method="cash",
            )

        with self.assertRaises(ValidationError):
            SalesInvoicePayment.objects.create(
                invoice=invoice,
                received_by=owner,
                amount=Decimal("50.00"),
                applied_customer_credit=Decimal("60.00"),
                payment_method="account_credit",
            )

    def test_sales_invoice_payment_model_requires_customer_for_overpayment_credit(self):
        owner = self._make_user("invoice-owner-overpay-no-customer")
        invoice = self._make_sales_invoice(owner)
        invoice.customer = None
        invoice.save(update_fields=["customer"])

        with self.assertRaises(ValidationError):
            SalesInvoicePayment.objects.create(
                invoice=invoice,
                received_by=owner,
                amount=Decimal("500.00"),
                credited_customer_overpayment=Decimal("40.00"),
                payment_method="cash",
            )

    def test_sales_invoice_payment_can_apply_existing_customer_credit(self):
        owner = self._make_user("invoice-owner-apply-credit")
        invoice = self._make_sales_invoice(owner)
        invoice.customer.credit_balance = Decimal("150.00")
        invoice.customer.save(update_fields=["credit_balance"])
        self.client.force_login(owner)

        response = self.client.post(
            reverse("sales_invoice_payment", args=[invoice.pk]),
            data={
                "amount": "310.00",
                "credit_to_apply": "150.00",
                "payment_method": "cash",
                "reference": "CREDIT-APPLY-1",
                "notes": "Apply saved credit",
            },
        )

        self.assertEqual(response.status_code, 302)
        invoice.refresh_from_db()
        invoice.customer.refresh_from_db()
        payments = list(invoice.payments.order_by("created_at"))
        self.assertEqual(invoice.status, "paid")
        self.assertEqual(invoice.balance_due, Decimal("0.00"))
        self.assertEqual(invoice.customer.credit_balance, Decimal("0.00"))
        self.assertEqual(len(payments), 2)
        self.assertEqual(payments[0].payment_method, "account_credit")
        self.assertEqual(payments[0].amount, Decimal("150.00"))
        self.assertEqual(payments[1].payment_method, "cash")
        self.assertEqual(payments[1].amount, Decimal("310.00"))

    def test_sales_invoice_payment_credit_only_leaves_remaining_balance_due(self):
        owner = self._make_user("invoice-owner-credit-default-amount")
        invoice = self._make_sales_invoice(owner)
        invoice.customer.credit_balance = Decimal("150.00")
        invoice.customer.save(update_fields=["credit_balance"])
        self.client.force_login(owner)

        response = self.client.post(
            reverse("sales_invoice_payment", args=[invoice.pk]),
            data={
                "amount": "460.00",
                "credit_to_apply": "150.00",
                "payment_method": "cash",
                "reference": "CREDIT-DEFAULT-1",
                "notes": "Default amount left unchanged while applying credit",
            },
        )

        self.assertEqual(response.status_code, 302)
        invoice.refresh_from_db()
        invoice.customer.refresh_from_db()
        payments = list(invoice.payments.order_by("created_at"))
        self.assertEqual(invoice.status, "issued")
        self.assertEqual(invoice.balance_due, Decimal("310.00"))
        self.assertEqual(invoice.customer.credit_balance, Decimal("0.00"))
        self.assertEqual(len(payments), 1)
        self.assertEqual(payments[0].payment_method, "account_credit")
        self.assertEqual(payments[0].amount, Decimal("150.00"))

    def test_sales_invoice_detail_shows_balance_after_available_customer_credit(self):
        owner = self._make_user("invoice-owner-credit-remaining")
        invoice = self._make_sales_invoice(owner)
        invoice.customer.credit_balance = Decimal("150.00")
        invoice.customer.save(update_fields=["credit_balance"])
        self.client.force_login(owner)

        response = self.client.get(reverse("sales_invoice_detail", args=[invoice.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["balance_due"], Decimal("460.00"))
        self.assertEqual(response.context["balance_after_customer_credit"], Decimal("310.00"))
        self.assertContains(response, "Balance After Customer Credit")
        self.assertContains(response, "$310.00")

    def test_sales_invoice_payment_screen_does_not_prefill_cash_amount(self):
        owner = self._make_user("invoice-owner-payment-default-zero")
        invoice = self._make_sales_invoice(owner)
        self.client.force_login(owner)

        response = self.client.get(reverse("sales_invoice_payment", args=[invoice.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="payment-amount-input"')
        self.assertContains(response, 'value="0.00"')
        self.assertContains(response, 'id="pay-full-balance-button"')
        self.assertContains(response, 'id="collected-now-radio"')
        self.assertContains(response, 'id="leave-in-store-radio"')
        self.assertContains(response, 'id="payment-method-select"')
        self.assertContains(response, 'id="payment-reference-input"')

        script_path = Path(__file__).resolve().parent / "static" / "inventory" / "js" / "sales_invoice_payment.js"
        script = script_path.read_text()
        self.assertIn("payFullBalanceButton", script)
        self.assertIn("updatePickupState", script)
        self.assertIn("updateReferenceRequirement", script)

    def test_sales_invoice_account_credit_payment_method_depletes_customer_credit(self):
        owner = self._make_user("invoice-owner-account-credit-method")
        invoice = self._make_sales_invoice(owner)
        invoice.customer.credit_balance = Decimal("200.00")
        invoice.customer.save(update_fields=["credit_balance"])
        self.client.force_login(owner)

        response = self.client.post(
            reverse("sales_invoice_payment", args=[invoice.pk]),
            data={
                "amount": "125.00",
                "payment_method": "account_credit",
                "reference": "ACCOUNT-CREDIT-1",
                "notes": "Use account credit selected as tender",
            },
        )

        self.assertEqual(response.status_code, 302)
        invoice.refresh_from_db()
        invoice.customer.refresh_from_db()
        payment = SalesInvoicePayment.objects.get(invoice=invoice)
        self.assertEqual(payment.payment_method, "account_credit")
        self.assertEqual(payment.amount, Decimal("125.00"))
        self.assertEqual(payment.applied_customer_credit, Decimal("125.00"))
        self.assertEqual(invoice.customer.credit_balance, Decimal("75.00"))
        self.assertEqual(invoice.balance_due, Decimal("335.00"))

    def test_admin_can_revert_invoice_payment_and_remove_overpayment_credit(self):
        owner = self._make_user("invoice-owner-revert")
        invoice = self._make_sales_invoice(owner)
        customer = invoice.customer
        payment = SalesInvoicePayment.objects.create(
            invoice=invoice,
            received_by=owner,
            amount=Decimal("500.00"),
            payment_method="cash",
            credited_customer_overpayment=Decimal("40.00"),
            reference="REV-CASH-1",
        )
        customer.credit_balance = Decimal("40.00")
        customer.save(update_fields=["credit_balance"])
        invoice.status = "paid"
        invoice.save(update_fields=["status"])
        self.client.force_login(owner)

        response = self.client.post(
            reverse("sales_invoice_payment_revert", args=[invoice.pk, payment.pk]),
            {"reversal_reason": "Duplicate cash collection."},
        )

        self.assertEqual(response.status_code, 302)
        invoice.refresh_from_db()
        customer.refresh_from_db()
        self.assertEqual(invoice.status, "issued")
        self.assertEqual(invoice.balance_due, Decimal("460.00"))
        self.assertEqual(customer.credit_balance, Decimal("0.00"))
        self.assertTrue(SalesInvoicePayment.objects.filter(pk=payment.pk).exists())
        self.assertTrue(SalesInvoicePaymentReversal.objects.filter(payment=payment).exists())

    def test_admin_can_revert_account_credit_payment_and_restore_customer_credit(self):
        owner = self._make_user("invoice-owner-revert-credit")
        invoice = self._make_sales_invoice(owner)
        customer = invoice.customer
        customer.credit_balance = Decimal("0.00")
        customer.save(update_fields=["credit_balance"])
        credit_payment = SalesInvoicePayment.objects.create(
            invoice=invoice,
            received_by=owner,
            amount=Decimal("150.00"),
            applied_customer_credit=Decimal("150.00"),
            payment_method="account_credit",
            reference="REV-CREDIT-1",
        )
        self.client.force_login(owner)

        response = self.client.post(
            reverse("sales_invoice_payment_revert", args=[invoice.pk, credit_payment.pk]),
            {"reversal_reason": "Credit was applied to the wrong invoice."},
        )

        self.assertEqual(response.status_code, 302)
        invoice.refresh_from_db()
        customer.refresh_from_db()
        self.assertEqual(invoice.status, "issued")
        self.assertEqual(invoice.balance_due, Decimal("460.00"))
        self.assertEqual(customer.credit_balance, Decimal("150.00"))
        self.assertTrue(SalesInvoicePayment.objects.filter(pk=credit_payment.pk).exists())
        self.assertTrue(SalesInvoicePaymentReversal.objects.filter(payment=credit_payment).exists())

    def test_manager_cannot_revert_invoice_payment(self):
        owner = self._make_user("invoice-owner-manager-block")
        invoice = self._make_sales_invoice(owner)
        payment = SalesInvoicePayment.objects.create(
            invoice=invoice,
            received_by=owner,
            amount=Decimal("200.00"),
            payment_method="cash",
            reference="REV-BLOCK-1",
        )
        manager = self._make_staff_user("manager-block", owner, role="manager", default_location=invoice.location)
        self.client.force_login(manager)

        response = self.client.post(
            reverse("sales_invoice_payment_revert", args=[invoice.pk, payment.pk]),
            {"reversal_reason": "Manager should not be allowed."},
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(SalesInvoicePayment.objects.filter(pk=payment.pk).exists())

    def test_posted_payment_rejects_financial_mutation_and_deletion(self):
        owner = self._make_user("invoice-owner-immutable-payment")
        invoice = self._make_sales_invoice(owner)
        payment = SalesInvoicePayment.objects.create(
            invoice=invoice,
            received_by=owner,
            amount=Decimal("200.00"),
            payment_method="cash",
            reference="IMMUTABLE-1",
        )

        payment.amount = Decimal("225.00")
        with self.assertRaises(ValidationError):
            payment.save(update_fields=["amount"])
        with self.assertRaises(ValidationError):
            SalesInvoicePayment.objects.filter(pk=payment.pk).update(amount=Decimal("225.00"))
        with self.assertRaises(ProtectedError):
            payment.delete()
        with self.assertRaises(ProtectedError):
            invoice.delete()

        payment.refresh_from_db()
        self.assertEqual(payment.amount, Decimal("200.00"))

    def test_payment_reversal_requires_reason(self):
        owner = self._make_user("invoice-owner-reversal-reason")
        invoice = self._make_sales_invoice(owner)
        payment = SalesInvoicePayment.objects.create(
            invoice=invoice,
            received_by=owner,
            amount=Decimal("200.00"),
            payment_method="cash",
        )
        self.client.force_login(owner)

        response = self.client.post(
            reverse("sales_invoice_payment_revert", args=[invoice.pk, payment.pk]),
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(SalesInvoicePaymentReversal.objects.filter(payment=payment).exists())
        self.assertEqual(invoice.balance_due, Decimal("260.00"))

    def test_repeated_payment_reversal_is_safely_rejected(self):
        owner = self._make_user("invoice-owner-reversal-retry")
        invoice = self._make_sales_invoice(owner)
        payment = SalesInvoicePayment.objects.create(
            invoice=invoice,
            received_by=owner,
            amount=Decimal("200.00"),
            payment_method="cash",
        )
        self.client.force_login(owner)
        url = reverse("sales_invoice_payment_revert", args=[invoice.pk, payment.pk])

        first_response = self.client.post(url, {"reversal_reason": "Duplicate payment."})
        second_response = self.client.post(url, {"reversal_reason": "Retry duplicate payment."})

        self.assertEqual(first_response.status_code, 302)
        self.assertEqual(second_response.status_code, 302)
        self.assertEqual(SalesInvoicePaymentReversal.objects.filter(payment=payment).count(), 1)
        invoice.refresh_from_db()
        self.assertEqual(invoice.balance_due, Decimal("460.00"))

    @patch("inventory.views._refresh_sales_invoice_status", side_effect=RuntimeError("forced rollback"))
    def test_payment_reversal_rolls_back_every_effect_on_midway_failure(self, _mock_refresh):
        owner = self._make_user("invoice-owner-reversal-rollback")
        invoice = self._make_sales_invoice(owner)
        customer = invoice.customer
        customer.credit_balance = Decimal("40.00")
        customer.save(update_fields=["credit_balance"])
        payment = SalesInvoicePayment.objects.create(
            invoice=invoice,
            received_by=owner,
            amount=Decimal("500.00"),
            credited_customer_overpayment=Decimal("40.00"),
            payment_method="cash",
        )
        invoice.status = "paid"
        invoice.save(update_fields=["status"])
        movement_count = CustomerCreditMovement.objects.filter(customer=customer).count()
        self.client.force_login(owner)

        with self.assertRaises(RuntimeError):
            self.client.post(
                reverse("sales_invoice_payment_revert", args=[invoice.pk, payment.pk]),
                {"reversal_reason": "Trigger rollback coverage."},
            )

        customer.refresh_from_db()
        invoice.refresh_from_db()
        self.assertEqual(customer.credit_balance, Decimal("40.00"))
        self.assertEqual(invoice.status, "paid")
        self.assertEqual(CustomerCreditMovement.objects.filter(customer=customer).count(), movement_count)
        self.assertFalse(SalesInvoicePaymentReversal.objects.filter(payment=payment).exists())
        self.assertTrue(SalesInvoicePayment.objects.unreversed().filter(pk=payment.pk).exists())

    def test_cross_tenant_payment_reversal_is_rejected(self):
        owner = self._make_user("invoice-owner-reversal-tenant-a")
        intruder = self._make_user("invoice-owner-reversal-tenant-b")
        invoice = self._make_sales_invoice(owner)
        payment = SalesInvoicePayment.objects.create(
            invoice=invoice,
            received_by=owner,
            amount=Decimal("100.00"),
            payment_method="cash",
        )
        self.client.force_login(intruder)

        response = self.client.post(
            reverse("sales_invoice_payment_revert", args=[invoice.pk, payment.pk]),
            {"reversal_reason": "Cross-tenant attempt."},
        )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(SalesInvoicePaymentReversal.objects.filter(payment=payment).exists())

    def test_customer_with_payment_history_is_preserved_and_reversal_stays_consistent(self):
        owner = self._make_user("invoice-owner-customer-history")
        invoice = self._make_sales_invoice(owner)
        customer = invoice.customer
        payment = SalesInvoicePayment.objects.create(
            invoice=invoice,
            received_by=owner,
            amount=Decimal("100.00"),
            payment_method="cash",
        )
        self.client.force_login(owner)

        delete_response = self.client.post(reverse("delete_customer", args=[customer.pk]))

        self.assertEqual(delete_response.status_code, 302)
        self.assertTrue(Customer.objects.filter(pk=customer.pk).exists())
        reversal_response = self.client.post(
            reverse("sales_invoice_payment_revert", args=[invoice.pk, payment.pk]),
            {"reversal_reason": "Payment attached to the wrong invoice."},
        )
        self.assertEqual(reversal_response.status_code, 302)
        self.assertTrue(SalesInvoicePaymentReversal.objects.filter(payment=payment).exists())
        self.assertEqual(invoice.balance_due, Decimal("460.00"))

    def test_invoice_balance_reconciles_to_unreversed_payment_ledger(self):
        owner = self._make_user("invoice-owner-balance-reconcile")
        invoice = self._make_sales_invoice(owner)
        reversed_payment = SalesInvoicePayment.objects.create(
            invoice=invoice,
            received_by=owner,
            amount=Decimal("100.00"),
            payment_method="cash",
        )
        SalesInvoicePayment.objects.create(
            invoice=invoice,
            received_by=owner,
            amount=Decimal("160.00"),
            payment_method="bank_transfer",
        )
        self.client.force_login(owner)
        self.client.post(
            reverse("sales_invoice_payment_revert", args=[invoice.pk, reversed_payment.pk]),
            {"reversal_reason": "Duplicate deposit."},
        )

        valid_total = sum(
            SalesInvoicePayment.objects.unreversed()
            .filter(invoice=invoice)
            .values_list("amount", flat=True),
            Decimal("0.00"),
        )
        invoice.refresh_from_db()
        self.assertEqual(valid_total, Decimal("160.00"))
        self.assertEqual(invoice.balance_due, invoice.effective_total_amount - valid_total)

    def test_customer_credit_balance_reconciles_to_append_only_movements(self):
        owner = self._make_user("invoice-owner-credit-reconcile")
        invoice = self._make_sales_invoice(owner)
        customer = invoice.customer
        customer.credit_balance = Decimal("100.00")
        customer.save(update_fields=["credit_balance"])
        self.client.force_login(owner)

        self.client.post(
            reverse("sales_invoice_payment", args=[invoice.pk]),
            {
                "amount": "40.00",
                "payment_method": "account_credit",
                "reference": "CREDIT-LEDGER-1",
            },
        )
        payment = SalesInvoicePayment.objects.get(invoice=invoice)
        self.client.post(
            reverse("sales_invoice_payment_revert", args=[invoice.pk, payment.pk]),
            {"reversal_reason": "Credit applied to the wrong invoice."},
        )

        customer.refresh_from_db()
        movement_total = sum(
            CustomerCreditMovement.objects.filter(customer=customer).values_list("amount", flat=True),
            Decimal("0.00"),
        )
        self.assertEqual(customer.credit_balance, Decimal("100.00"))
        self.assertEqual(customer.credit_balance, movement_total)
        self.assertEqual(customer.ledger_credit_balance, movement_total)

    def test_reversal_removes_payment_from_register_reconciliation_totals(self):
        owner = self._make_user("invoice-owner-register-reconcile")
        invoice = self._make_sales_invoice(owner)
        payment = SalesInvoicePayment.objects.create(
            invoice=invoice,
            received_by=owner,
            amount=Decimal("125.00"),
            payment_method="cash",
            payment_date=timezone.now(),
        )
        before = _daily_reconciliation_context(owner, timezone.localdate())
        self.client.force_login(owner)

        self.client.post(
            reverse("sales_invoice_payment_revert", args=[invoice.pk, payment.pk]),
            {"reversal_reason": "Cash was entered twice."},
        )

        after = _daily_reconciliation_context(owner, timezone.localdate())
        self.assertEqual(before["expected_cash"], Decimal("125.00"))
        self.assertEqual(after["expected_cash"], Decimal("0.00"))

    def test_payment_and_reversal_audit_evidence_remains_linked(self):
        owner = self._make_user("invoice-owner-reversal-audit")
        invoice = self._make_sales_invoice(owner)
        self.client.force_login(owner)
        self.client.post(
            reverse("sales_invoice_payment", args=[invoice.pk]),
            {
                "amount": "100.00",
                "payment_method": "cash",
                "reference": "AUDIT-PAY-1",
            },
        )
        payment = SalesInvoicePayment.objects.get(invoice=invoice)

        self.client.post(
            reverse("sales_invoice_payment_revert", args=[invoice.pk, payment.pk]),
            {"reversal_reason": "Duplicate receipt AUDIT-PAY-1."},
        )

        reversal = SalesInvoicePaymentReversal.objects.get(payment=payment)
        original_audit = AuditLog.objects.get(message="Sales invoice payment recorded")
        reversal_audit = AuditLog.objects.get(message="Sales invoice payment reversed")
        self.assertIn(payment.id, original_audit.metadata["payment_ids"])
        self.assertEqual(reversal_audit.metadata["payment_id"], payment.id)
        self.assertEqual(reversal_audit.metadata["reversal_id"], reversal.id)
        self.assertEqual(reversal.reason, "Duplicate receipt AUDIT-PAY-1.")
        self.assertTrue(SalesInvoicePayment.objects.filter(pk=payment.pk).exists())

    def test_admin_can_create_credit_note_and_reduce_invoice_balance(self):
        owner = self._make_user("invoice-owner-credit-admin")
        invoice = self._make_sales_invoice(owner)
        self.client.force_login(owner)

        response = self.client.post(
            reverse("sales_invoice_create_credit_note", args=[invoice.pk]),
            data={
                "amount": "60.00",
                "reason": "billing_error",
                "notes": "Adjusted line pricing discrepancy.",
            },
        )

        self.assertEqual(response.status_code, 302)
        invoice.refresh_from_db()
        credit_note = SalesInvoiceCreditNote.objects.get(invoice=invoice)
        self.assertEqual(credit_note.amount, Decimal("60.00"))
        self.assertEqual(credit_note.reason, "billing_error")
        self.assertEqual(invoice.effective_total_amount, Decimal("400.00"))
        self.assertEqual(invoice.balance_due, Decimal("400.00"))
        self.assertEqual(invoice.status, "issued")

    def test_manager_can_create_credit_note(self):
        owner = self._make_user("invoice-owner-credit-manager")
        invoice = self._make_sales_invoice(owner)
        manager = self._make_staff_user("invoice-credit-manager", owner, role="manager", default_location=invoice.location)
        self.client.force_login(manager)

        response = self.client.post(
            reverse("sales_invoice_create_credit_note", args=[invoice.pk]),
            data={"amount": "40.00", "reason": "goodwill", "notes": "Manager-approved service recovery."},
        )

        self.assertEqual(response.status_code, 302)
        invoice.refresh_from_db()
        self.assertTrue(SalesInvoiceCreditNote.objects.filter(invoice=invoice, created_by=manager).exists())
        self.assertEqual(invoice.effective_total_amount, Decimal("420.00"))

    def test_cashier_cannot_create_credit_note(self):
        owner = self._make_user("invoice-owner-credit-cashier")
        invoice = self._make_sales_invoice(owner)
        cashier = self._make_staff_user("invoice-credit-cashier", owner, role="cashier", default_location=invoice.location)
        self.client.force_login(cashier)

        response = self.client.post(
            reverse("sales_invoice_create_credit_note", args=[invoice.pk]),
            data={"amount": "25.00", "reason": "other", "notes": "Should be blocked."},
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(SalesInvoiceCreditNote.objects.filter(invoice=invoice).exists())

    def test_credit_note_overpayment_is_added_to_customer_credit(self):
        owner = self._make_user("invoice-owner-credit-overpay")
        invoice = self._make_sales_invoice(owner)
        customer = invoice.customer
        SalesInvoicePayment.objects.create(
            invoice=invoice,
            received_by=owner,
            amount=Decimal("460.00"),
            payment_method="cash",
            reference="FULLPAY",
        )
        invoice.status = "paid"
        invoice.save(update_fields=["status"])
        self.client.force_login(owner)

        response = self.client.post(
            reverse("sales_invoice_create_credit_note", args=[invoice.pk]),
            data={"amount": "100.00", "reason": "returned_items", "notes": "Returned two units."},
        )

        self.assertEqual(response.status_code, 302)
        invoice.refresh_from_db()
        customer.refresh_from_db()
        credit_note = SalesInvoiceCreditNote.objects.get(invoice=invoice)
        self.assertEqual(credit_note.credited_customer_amount, Decimal("100.00"))
        self.assertEqual(customer.credit_balance, Decimal("100.00"))
        self.assertEqual(invoice.effective_total_amount, Decimal("360.00"))
        self.assertEqual(invoice.balance_due, Decimal("0.00"))
        self.assertEqual(invoice.status, "paid")

    def test_sales_invoice_mark_paid_records_final_payment(self):
        owner = self._make_user("invoice-owner-paid")
        invoice = self._make_sales_invoice(owner)
        SalesInvoicePayment.objects.create(
            invoice=invoice,
            received_by=owner,
            amount=Decimal("160.00"),
            payment_method="cash",
            reference="DEPOSIT",
        )
        self.client.force_login(owner)

        response = self.client.post(
            reverse("sales_invoice_mark_paid", args=[invoice.pk]),
            {"pickup_status": "collected_now"},
        )

        self.assertEqual(response.status_code, 302)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, "paid")
        self.assertEqual(invoice.total_paid_amount, Decimal("460.00"))
        self.assertEqual(invoice.balance_due, Decimal("0.00"))
        self.assertEqual(invoice.payments.count(), 2)
        final_payment = invoice.payments.order_by("created_at").last()
        self.assertEqual(final_payment.amount, Decimal("300.00"))

    def test_api_transfer_stock_rolls_back_when_source_stock_is_insufficient(self):
        owner = self._make_user("transfer-rollback")
        source = self._make_location(owner, "Warehouse")
        destination = self._make_location(owner, "Storefront")
        item = self._make_item(owner, sku="SKU-XFER-FAIL", quantity=10)
        StockRecord.objects.create(item=item, location=source, quantity=2)

        self.client.force_login(owner)
        response = self.client.post(
            reverse("api_transfer_stock"),
            data={
                "sku": "SKU-XFER-FAIL",
                "from_location_id": source.pk,
                "to_location_id": destination.pk,
                "quantity": 5,
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(StockTransfer.objects.count(), 0)
        self.assertEqual(StockRecord.objects.get(item=item, location=source).quantity, 2)
        self.assertFalse(StockRecord.objects.filter(item=item, location=destination).exists())
        item.refresh_from_db()
        self.assertEqual(item.quantity, 2)


class SupplierLedgerIntegrityTests(TestCase):
    def _make_user(self, username, *, role="admin", owner=None, location=None):
        user = User.objects.create_user(username=username, password="password123")
        profile = UserProfile.for_user(user)
        profile.role = role
        profile.status = "active"
        profile.plan = "PRO"
        profile.pro_expires = timezone.now().date() + timedelta(days=30)
        profile.plan_end = timezone.now() + timedelta(days=30)
        profile.parent_admin = UserProfile.for_user(owner) if owner else None
        profile.default_location = location
        profile.save()
        return user

    def _make_invoice(
        self,
        owner,
        *,
        amount="100.00",
        suffix="1",
        date_issued=None,
        payment_terms=SupplierInvoice.TERMS_DUE_ON_RECEIPT,
    ):
        location = Location.objects.create(owner=owner, name=f"Supplier Branch {suffix}")
        owner.profile.default_location = location
        owner.profile.save(update_fields=["default_location"])
        supplier = Supplier.objects.create(owner=owner, name=f"Supplier {suffix}")
        invoice = SupplierInvoice.objects.create(
            supplier=supplier,
            location=location,
            invoice_no=f"#SUP-{owner.pk}-{suffix}",
            amount=Decimal(amount),
            paid_amount=Decimal("0.00"),
            status="Pending",
            date_issued=date_issued or timezone.localdate(),
            payment_terms=payment_terms,
        )
        return invoice

    def _record_payment(self, invoice, actor, amount):
        self.client.force_login(actor)
        response = self.client.post(
            reverse("mark_invoice_paid", args=[invoice.pk]),
            {
                "payment_amount": amount,
                "reference": f"PAY-{invoice.pk}-{amount}",
            },
        )
        self.assertEqual(response.status_code, 302)
        return invoice.payments.order_by("-id").first()

    def _reverse_payment(self, invoice, payment, actor, reason="Supplier payment correction."):
        self.client.force_login(actor)
        return self.client.post(
            reverse(
                "reverse_supplier_invoice_payment",
                args=[invoice.pk, payment.pk],
            ),
            {"reversal_reason": reason},
        )

    def test_direct_mutation_and_deletion_of_posted_supplier_payment_are_rejected(self):
        owner = self._make_user("supplier-ledger-mutation")
        invoice = self._make_invoice(owner)
        payment = SupplierInvoicePayment.objects.create(
            invoice=invoice,
            paid_by=owner,
            amount=Decimal("25.00"),
            reference="IMMUTABLE-1",
        )

        payment.amount = Decimal("30.00")
        with self.assertRaises(ValidationError):
            payment.save()
        with self.assertRaises(ValidationError):
            SupplierInvoicePayment.objects.filter(pk=payment.pk).update(
                amount=Decimal("30.00")
            )
        with self.assertRaises(ProtectedError):
            payment.delete()
        with self.assertRaises(ProtectedError):
            invoice.delete()

        payment.refresh_from_db()
        self.assertEqual(payment.amount, Decimal("25.00"))

    def test_unauthorized_supplier_payment_reversal_is_rejected(self):
        owner = self._make_user("supplier-ledger-auth-owner")
        invoice = self._make_invoice(owner)
        payment = self._record_payment(invoice, owner, "40.00")
        manager = self._make_user(
            "supplier-ledger-auth-manager",
            role="manager",
            owner=owner,
            location=invoice.location,
        )

        response = self._reverse_payment(invoice, payment, manager)

        self.assertRedirects(response, reverse("dashboard"))
        self.assertFalse(
            SupplierInvoicePaymentReversal.objects.filter(payment=payment).exists()
        )
        self.assertTrue(
            SupplierInvoicePayment.objects.unreversed().filter(pk=payment.pk).exists()
        )

    def test_valid_reversal_restores_supplier_invoice_balance(self):
        owner = self._make_user("supplier-ledger-valid-reversal")
        invoice = self._make_invoice(owner)
        payment = self._record_payment(invoice, owner, "40.00")
        invoice.refresh_from_db()
        self.assertEqual(invoice.paid_amount, Decimal("40.00"))
        self.assertEqual(invoice.balance_due, Decimal("60.00"))

        response = self._reverse_payment(
            invoice,
            payment,
            owner,
            "Payment posted to the wrong supplier invoice.",
        )

        self.assertEqual(response.status_code, 302)
        invoice.refresh_from_db()
        self.assertEqual(invoice.paid_amount, Decimal("0.00"))
        self.assertEqual(invoice.total_paid_amount, Decimal("0.00"))
        self.assertEqual(invoice.balance_due, Decimal("100.00"))
        self.assertEqual(invoice.status, "Pending")
        self.assertTrue(
            SupplierInvoicePaymentReversal.objects.filter(payment=payment).exists()
        )
        self.assertTrue(SupplierInvoicePayment.objects.filter(pk=payment.pk).exists())

    def test_repeated_reversal_does_not_apply_twice(self):
        owner = self._make_user("supplier-ledger-repeat")
        invoice = self._make_invoice(owner)
        payment = self._record_payment(invoice, owner, "35.00")

        first = self._reverse_payment(invoice, payment, owner, "Duplicate payment.")
        second = self._reverse_payment(invoice, payment, owner, "Retry of duplicate payment.")

        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        self.assertEqual(
            SupplierInvoicePaymentReversal.objects.filter(payment=payment).count(),
            1,
        )
        invoice.refresh_from_db()
        self.assertEqual(invoice.balance_due, Decimal("100.00"))

    def test_competing_reversal_insert_has_one_effective_winner(self):
        owner = self._make_user("supplier-ledger-concurrent")
        invoice = self._make_invoice(owner)
        payment = self._record_payment(invoice, owner, "45.00")
        self._reverse_payment(invoice, payment, owner, "First reversal wins.")

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                SupplierInvoicePaymentReversal.objects.bulk_create(
                    [
                        SupplierInvoicePaymentReversal(
                            payment=payment,
                            reversed_by=owner,
                            reversed_by_username=owner.username,
                            reason="Concurrent losing reversal.",
                        )
                    ]
                )

        self.assertEqual(
            SupplierInvoicePaymentReversal.objects.filter(payment=payment).count(),
            1,
        )
        invoice.refresh_from_db()
        self.assertEqual(invoice.paid_amount, Decimal("0.00"))

    def test_partial_payments_reconcile_after_one_reversal(self):
        owner = self._make_user("supplier-ledger-partial")
        invoice = self._make_invoice(owner)
        first_payment = self._record_payment(invoice, owner, "30.00")
        self._record_payment(invoice, owner, "20.00")

        invoice.refresh_from_db()
        self.assertEqual(invoice.total_paid_amount, Decimal("50.00"))
        self.assertEqual(invoice.paid_amount, Decimal("50.00"))
        self.assertEqual(invoice.balance_due, Decimal("50.00"))

        self._reverse_payment(invoice, first_payment, owner, "First installment duplicated.")

        invoice.refresh_from_db()
        self.assertEqual(invoice.total_paid_amount, Decimal("20.00"))
        self.assertEqual(invoice.paid_amount, Decimal("20.00"))
        self.assertEqual(invoice.balance_due, Decimal("80.00"))

    def test_reversal_failure_rolls_back_every_effect(self):
        owner = self._make_user("supplier-ledger-rollback")
        invoice = self._make_invoice(owner)
        payment = self._record_payment(invoice, owner, "40.00")
        audit_count = AuditLog.objects.count()

        with patch(
            "inventory.views._refresh_supplier_invoice_financial_state",
            side_effect=RuntimeError("forced supplier reversal rollback"),
        ):
            with self.assertRaises(RuntimeError):
                self._reverse_payment(
                    invoice,
                    payment,
                    owner,
                    "Exercise the transaction rollback boundary.",
                )

        invoice.refresh_from_db()
        self.assertEqual(invoice.paid_amount, Decimal("40.00"))
        self.assertTrue(
            SupplierInvoicePayment.objects.unreversed().filter(pk=payment.pk).exists()
        )
        self.assertFalse(
            SupplierInvoicePaymentReversal.objects.filter(payment=payment).exists()
        )
        self.assertEqual(AuditLog.objects.count(), audit_count)

    def test_cross_tenant_supplier_payment_reversal_is_rejected(self):
        owner = self._make_user("supplier-ledger-tenant-owner")
        intruder = self._make_user("supplier-ledger-tenant-intruder")
        invoice = self._make_invoice(owner)
        payment = self._record_payment(invoice, owner, "25.00")

        response = self._reverse_payment(
            invoice,
            payment,
            intruder,
            "Cross-tenant reversal attempt.",
        )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(
            SupplierInvoicePaymentReversal.objects.filter(payment=payment).exists()
        )

    def test_supplier_with_financial_history_cannot_be_deleted(self):
        owner = self._make_user("supplier-ledger-delete")
        invoice = self._make_invoice(owner)
        payment = self._record_payment(invoice, owner, "15.00")
        self.client.force_login(owner)

        response = self.client.post(
            reverse("supplier_delete", args=[invoice.supplier_id]),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "protected invoice, payment, or receipt history")
        self.assertTrue(Supplier.objects.filter(pk=invoice.supplier_id).exists())
        self.assertTrue(SupplierInvoice.objects.filter(pk=invoice.pk).exists())
        self.assertTrue(SupplierInvoicePayment.objects.filter(pk=payment.pk).exists())

    def test_ap_totals_use_only_unreversed_payments(self):
        owner = self._make_user("supplier-ledger-ap")
        first_invoice = self._make_invoice(owner, amount="100.00", suffix="ap-1")
        second_invoice = self._make_invoice(owner, amount="200.00", suffix="ap-2")
        self._record_payment(first_invoice, owner, "60.00")
        reversed_payment = self._record_payment(second_invoice, owner, "50.00")
        self._reverse_payment(
            second_invoice,
            reversed_payment,
            owner,
            "Second invoice payment was duplicated.",
        )
        self.client.force_login(owner)

        response = self.client.get(reverse("advanced_reports"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["supplier_total_purchases"],
            Decimal("300.00"),
        )
        self.assertEqual(
            response.context["supplier_current_debt"],
            Decimal("240.00"),
        )
        self.assertEqual(response.context["supplier_payment_ratio"], 20)

    def test_supplier_invoice_net_terms_drive_due_date_and_aging_context(self):
        owner = self._make_user("supplier-ledger-aging")
        issue_date = timezone.localdate() - timedelta(days=40)
        invoice = self._make_invoice(
            owner,
            amount="150.00",
            suffix="aging",
            date_issued=issue_date,
            payment_terms=SupplierInvoice.TERMS_NET_30,
        )
        self.client.force_login(owner)

        response = self.client.get(reverse("supplier_ledger", args=[invoice.supplier_id]))

        self.assertEqual(response.status_code, 200)
        invoice.refresh_from_db()
        self.assertEqual(invoice.due_date, issue_date + timedelta(days=30))
        self.assertEqual(invoice.aging_bucket, "1-30 Days")
        self.assertEqual(response.context["aging_buckets"]["1-30 Days"]["count"], 1)
        self.assertEqual(response.context["aging_buckets"]["1-30 Days"]["total"], Decimal("150.00"))
        self.assertContains(response, "Net 30")
        self.assertContains(response, "1-30 Days")

    def test_record_supplier_invoice_accepts_standard_trade_credit_terms(self):
        owner = self._make_user("supplier-ledger-net-form")
        supplier = Supplier.objects.create(owner=owner, name="Net Terms Supplier")
        location = Location.objects.create(owner=owner, name="Net Terms Branch")
        self.client.force_login(owner)

        response = self.client.post(
            reverse("add_invoice", args=[supplier.pk]),
            {
                "invoice_no": "NET-15",
                "location": str(location.pk),
                "amount": "2300.00",
                "date_issued": "2026-07-01",
                "payment_terms": SupplierInvoice.TERMS_NET_15,
            },
        )

        self.assertEqual(response.status_code, 302)
        invoice = SupplierInvoice.objects.get(supplier=supplier)
        self.assertEqual(invoice.payment_terms, SupplierInvoice.TERMS_NET_15)
        self.assertEqual(invoice.due_date.isoformat(), "2026-07-16")
        self.assertEqual(invoice.status, "Pending")

    def test_supplier_ledger_exposes_manage_panel_and_pdf_statement(self):
        owner = self._make_user("supplier-ledger-statement")
        invoice = self._make_invoice(owner, amount="250.00", suffix="statement")
        self.client.force_login(owner)

        ledger_response = self.client.get(reverse("supplier_ledger", args=[invoice.supplier_id]))
        self.assertEqual(ledger_response.status_code, 200)
        self.assertContains(ledger_response, "Manage Invoice")
        self.assertContains(ledger_response, "Download Vendor Statement")

        pdf_response = self.client.get(
            f"{reverse('supplier_ledger', args=[invoice.supplier_id])}?statement=vendor&format=pdf"
        )

        self.assertEqual(pdf_response.status_code, 200)
        self.assertEqual(pdf_response["Content-Type"], "application/pdf")
        self.assertIn("vendor-statement", pdf_response["Content-Disposition"])
        self.assertTrue(pdf_response.content.startswith(b"%PDF-1.4"))

    def test_payables_pulse_chart_uses_current_net_invoice_figures(self):
        owner = self._make_user("supplier-ledger-chart")
        invoice = self._make_invoice(owner, amount="1000000.00", suffix="chart")
        self._record_payment(invoice, owner, "1000.00")
        self.client.force_login(owner)

        response = self.client.get(reverse("advanced_reports"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["supplier_current_debt"], Decimal("999000.00"))
        self.assertEqual(json.loads(response.context["payables_debit_json"]), [1000000.0])
        self.assertEqual(json.loads(response.context["payables_credit_json"]), [1000.0])
        self.assertEqual(json.loads(response.context["payables_balance_json"]), [999000.0])

    def test_void_preserves_payment_and_creates_compensating_evidence(self):
        owner = self._make_user("supplier-ledger-void")
        invoice = self._make_invoice(owner)
        payment = self._record_payment(invoice, owner, "40.00")
        self.client.force_login(owner)

        response = self.client.post(
            reverse("void_invoice", args=[invoice.pk]),
            {"void_reason": "Supplier withdrew this invoice."},
        )

        self.assertEqual(response.status_code, 302)
        invoice.refresh_from_db()
        adjustment = SupplierInvoiceAdjustment.objects.get(
            invoice=invoice,
            adjustment_type=SupplierInvoiceAdjustment.TYPE_VOID_CREDIT,
        )
        self.assertEqual(invoice.status, "Void")
        self.assertEqual(invoice.paid_amount, Decimal("40.00"))
        self.assertEqual(invoice.total_paid_amount, Decimal("40.00"))
        self.assertEqual(invoice.total_credit_amount, Decimal("100.00"))
        self.assertEqual(invoice.balance_due, Decimal("0.00"))
        self.assertEqual(invoice.supplier_credit_balance, Decimal("40.00"))
        self.assertEqual(adjustment.reason, "Supplier withdrew this invoice.")
        self.assertTrue(SupplierInvoicePayment.objects.filter(pk=payment.pk).exists())
        self.assertFalse(
            SupplierInvoicePaymentReversal.objects.filter(payment=payment).exists()
        )
        self.assertTrue(
            AuditLog.objects.filter(
                message="Supplier invoice voided with compensating adjustment",
                metadata__adjustment_id=adjustment.id,
            ).exists()
        )

    def test_original_and_reversal_audit_evidence_remain_linked(self):
        owner = self._make_user("supplier-ledger-audit")
        invoice = self._make_invoice(owner)
        payment = self._record_payment(invoice, owner, "55.00")

        self._reverse_payment(
            invoice,
            payment,
            owner,
            "Bank reference was assigned to the wrong invoice.",
        )

        reversal = SupplierInvoicePaymentReversal.objects.get(payment=payment)
        original_audit = AuditLog.objects.get(
            message="Supplier invoice payment recorded",
            metadata__payment_id=payment.id,
        )
        reversal_audit = AuditLog.objects.get(
            message="Supplier invoice payment reversed",
            metadata__reversal_id=reversal.id,
        )
        self.assertEqual(original_audit.metadata["owner_id"], owner.id)
        self.assertEqual(original_audit.metadata["location_id"], invoice.location_id)
        self.assertEqual(reversal_audit.metadata["payment_id"], payment.id)
        self.assertEqual(
            reversal.reason,
            "Bank reference was assigned to the wrong invoice.",
        )
        self.assertTrue(SupplierInvoicePayment.objects.filter(pk=payment.pk).exists())


class QS003HistoricalFinancialIntegrityTests(TestCase):
    def _make_user(self, username, *, role="admin", owner=None, location=None):
        user = User.objects.create_user(username=username, password="password123")
        profile = UserProfile.for_user(user)
        profile.role = role
        profile.status = "active"
        profile.plan = "PRO"
        profile.pro_expires = timezone.now().date() + timedelta(days=30)
        profile.plan_end = timezone.now() + timedelta(days=30)
        profile.parent_admin = UserProfile.for_user(owner) if owner else None
        profile.default_location = location
        profile.save()
        return user

    def _make_sales_invoice(self, owner, *, suffix="sales"):
        location = Location.objects.create(owner=owner, name=f"Sales Branch {suffix}")
        customer = Customer.objects.create(owner=owner, name=f"Customer {suffix}")
        category = Category.objects.create(owner=owner, name=f"Category {suffix}")
        brand = Brand.objects.create(owner=owner, name=f"Brand {suffix}")
        item = Item.objects.create(
            owner=owner,
            name=f"Item {suffix}",
            sku=f"SKU-{owner.pk}-{suffix}",
            category=category,
            brand=brand,
            cost_price=Decimal("5.00"),
            price=Decimal("20.00"),
        )
        invoice = SalesInvoice.objects.create(
            owner=owner,
            created_by=owner,
            customer=customer,
            location=location,
            status="draft",
        )
        line = SalesInvoiceItem.objects.create(
            invoice=invoice,
            item=item,
            item_name=item.name,
            quantity=1,
            unit_price=Decimal("20.00"),
        )
        invoice.refresh_from_db()
        return invoice, line, customer, location, item

    def _make_supplier_invoice(self, owner, *, suffix="supplier", amount="100.00"):
        location = Location.objects.create(owner=owner, name=f"Supplier Branch {suffix}")
        supplier = Supplier.objects.create(owner=owner, name=f"Supplier {suffix}")
        invoice = SupplierInvoice.objects.create(
            supplier=supplier,
            location=location,
            invoice_no=f"#QS3-{owner.pk}-{suffix}",
            amount=Decimal(amount),
            date_issued=timezone.localdate(),
        )
        return invoice, supplier, location

    def test_posted_invoice_lines_and_totals_are_immutable(self):
        owner = self._make_user("qs3-sales-posted")
        invoice, line, _customer, _location, _item = self._make_sales_invoice(owner)
        SalesInvoicePayment.objects.create(invoice=invoice, received_by=owner, amount=Decimal("20.00"))

        line.quantity = 2
        with self.assertRaises(ValidationError):
            line.save()
        with self.assertRaises(ValidationError):
            SalesInvoiceItem.objects.filter(pk=line.pk).update(quantity=2)
        with self.assertRaises(ProtectedError):
            line.delete()
        with self.assertRaises(ValidationError):
            SalesInvoice.objects.filter(pk=invoice.pk).update(total_amount=Decimal("99.00"))

        invoice.notes = '[[QS_META]] {"discount_value": "1.00"}'
        with self.assertRaises(ValidationError):
            invoice.recalculate_totals()

    def test_unposted_draft_invoice_remains_editable(self):
        owner = self._make_user("qs3-sales-draft")
        invoice, line, _customer, _location, _item = self._make_sales_invoice(owner, suffix="draft")

        line.quantity = 2
        line.save()
        invoice.refresh_from_db()

        self.assertEqual(line.line_total, Decimal("40.00"))
        self.assertEqual(invoice.total_amount, Decimal("40.00"))

    def test_supplier_refund_is_append_only_reconciled_and_idempotent(self):
        owner = self._make_user("qs3-supplier-refund")
        invoice, supplier, _location = self._make_supplier_invoice(owner)
        self.client.force_login(owner)
        payment_response = self.client.post(
            reverse("mark_invoice_paid", args=[invoice.pk]),
            {"payment_amount": "100.00", "reference": "PAY-QS3"},
        )
        self.assertEqual(payment_response.status_code, 302)

        refund_data = {
            "refund_amount": "20.00",
            "refund_reference": "REF-QS3-1",
            "refund_reason": "Supplier returned the overpayment.",
        }
        first = self.client.post(reverse("supplier_invoice_refund", args=[invoice.pk]), refund_data)
        second = self.client.post(reverse("supplier_invoice_refund", args=[invoice.pk]), refund_data)

        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        refund = SupplierInvoiceRefund.objects.get(invoice=invoice)
        self.assertEqual(refund.supplier_id, supplier.id)
        self.assertEqual(refund.received_by_username, owner.username)
        self.assertEqual(SupplierInvoiceRefund.objects.filter(invoice=invoice).count(), 1)
        refund.amount = Decimal("25.00")
        with self.assertRaises(ValidationError):
            refund.save()
        with self.assertRaises(ProtectedError):
            refund.delete()

        invoice.refresh_from_db()
        self.assertEqual(invoice.total_paid_amount, Decimal("100.00"))
        self.assertEqual(invoice.total_refund_amount, Decimal("20.00"))
        self.assertEqual(invoice.net_paid_amount, Decimal("80.00"))
        self.assertEqual(invoice.balance_due, Decimal("20.00"))

        report = self.client.get(reverse("advanced_reports"))
        self.assertEqual(report.status_code, 200)
        self.assertEqual(report.context["supplier_total_purchases"], Decimal("100.00"))
        self.assertEqual(report.context["supplier_current_debt"], Decimal("20.00"))

    def test_supplier_invoice_facts_and_refund_transaction_boundary_are_protected(self):
        owner = self._make_user("qs3-supplier-fact-guard")
        invoice, _supplier, _location = self._make_supplier_invoice(owner, suffix="fact")
        self.client.force_login(owner)
        self.client.post(
            reverse("mark_invoice_paid", args=[invoice.pk]),
            {"payment_amount": "40.00", "reference": "PAY-QS3-FACT"},
        )

        invoice.amount = Decimal("125.00")
        with self.assertRaises(ValidationError):
            invoice.save()
        with self.assertRaises(ValidationError):
            SupplierInvoice.objects.filter(pk=invoice.pk).update(amount=Decimal("125.00"))

        with patch(
            "inventory.views._refresh_supplier_invoice_financial_state",
            side_effect=RuntimeError("forced supplier refund rollback"),
        ):
            with self.assertRaises(RuntimeError):
                self.client.post(
                    reverse("supplier_invoice_refund", args=[invoice.pk]),
                    {
                        "refund_amount": "10.00",
                        "refund_reference": "REF-QS3-ROLLBACK",
                        "refund_reason": "Exercise refund rollback.",
                    },
                )

        self.assertFalse(SupplierInvoiceRefund.objects.filter(invoice=invoice).exists())
        invoice.refresh_from_db()
        self.assertEqual(invoice.paid_amount, Decimal("40.00"))

    def test_supplier_credit_note_is_a_compensating_movement(self):
        owner = self._make_user("qs3-supplier-credit")
        invoice, _supplier, _location = self._make_supplier_invoice(owner, suffix="credit")
        self.client.force_login(owner)

        response = self.client.post(
            reverse("supplier_invoice_credit_note", args=[invoice.pk]),
            {"credit_amount": "15.00", "credit_reason": "Pricing correction."},
        )

        self.assertEqual(response.status_code, 302)
        invoice.refresh_from_db()
        adjustment = SupplierInvoiceAdjustment.objects.get(
            invoice=invoice,
            adjustment_type=SupplierInvoiceAdjustment.TYPE_SUPPLIER_CREDIT,
        )
        self.assertEqual(adjustment.amount, Decimal("15.00"))
        self.assertEqual(invoice.effective_amount, Decimal("85.00"))
        self.assertEqual(invoice.balance_due, Decimal("85.00"))

    def test_archived_supplier_customer_location_and_actor_remain_reconcilable(self):
        owner = self._make_user("qs3-archive-owner")
        invoice, supplier, supplier_location = self._make_supplier_invoice(owner, suffix="archive")
        supplier_payment = SupplierInvoicePayment.objects.create(
            invoice=invoice,
            paid_by=owner,
            amount=Decimal("10.00"),
        )
        sales_invoice, _line, customer, sales_location, _item = self._make_sales_invoice(owner, suffix="archive")
        sales_payment = SalesInvoicePayment.objects.create(
            invoice=sales_invoice,
            received_by=owner,
            amount=Decimal("20.00"),
        )
        self.client.force_login(owner)

        supplier_response = self.client.post(reverse("supplier_delete", args=[supplier.pk]))
        customer_response = self.client.post(reverse("delete_customer", args=[customer.pk]))
        location_response = self.client.post(reverse("delete_location", args=[sales_location.pk]))

        self.assertEqual(supplier_response.status_code, 302)
        self.assertEqual(customer_response.status_code, 302)
        self.assertEqual(location_response.status_code, 302)
        supplier.refresh_from_db()
        customer.refresh_from_db()
        sales_location.refresh_from_db()
        self.assertTrue(supplier.is_archived)
        self.assertTrue(customer.is_archived)
        self.assertTrue(sales_location.is_archived)
        self.assertTrue(SupplierInvoicePayment.objects.filter(pk=supplier_payment.pk).exists())
        self.assertTrue(SalesInvoicePayment.objects.filter(pk=sales_payment.pk, customer=customer).exists())
        self.assertEqual(sales_payment.location_id, sales_location.id)
        self.assertTrue(AuditLog.objects.filter(message="Supplier archived instead of deleted").exists())

    def test_staff_archive_preserves_actor_snapshot(self):
        owner = self._make_user("qs3-archive-staff-owner")
        staff = self._make_user("qs3-archive-staff", role="cashier", owner=owner)
        invoice, _line, _customer, _location, _item = self._make_sales_invoice(owner, suffix="staff")
        payment = SalesInvoicePayment.objects.create(
            invoice=invoice,
            received_by=staff,
            amount=Decimal("20.00"),
        )
        self.client.force_login(owner)

        response = self.client.post(
            reverse("manage_staff"),
            {"action": "delete", "user_id": staff.pk},
        )

        self.assertEqual(response.status_code, 302)
        staff.refresh_from_db()
        staff.profile.refresh_from_db()
        payment.refresh_from_db()
        self.assertTrue(User.objects.filter(pk=staff.pk).exists())
        self.assertTrue(staff.profile.is_archived)
        self.assertFalse(staff.is_active)
        self.assertEqual(payment.received_by_id, staff.pk)
        self.assertEqual(payment.received_by_username, staff.username)

    def test_account_purge_returns_structured_block_and_archives_history(self):
        owner = self._make_user("qs3-purge-owner")
        invoice, _line, _customer, _location, _item = self._make_sales_invoice(owner, suffix="purge")
        payment = SalesInvoicePayment.objects.create(
            invoice=invoice,
            received_by=owner,
            amount=Decimal("20.00"),
        )
        self.client.force_login(owner)

        response = self.client.post(
            reverse("delete_account"),
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "financial_history_protected")
        self.assertTrue(response.json()["archived"])
        self.assertTrue(User.objects.filter(pk=owner.pk).exists())
        self.assertTrue(SalesInvoicePayment.objects.filter(pk=payment.pk).exists())
        owner.refresh_from_db()
        owner.profile.refresh_from_db()
        self.assertTrue(owner.profile.is_archived)

    def test_account_delete_discards_drafts_instead_of_archiving(self):
        owner = self._make_user("qs3-draft-delete-owner")
        invoice, line, customer, location, item = self._make_sales_invoice(owner, suffix="draft-delete")
        quotation = SalesQuotation.objects.create(
            owner=owner,
            created_by=owner,
            customer=customer,
            status="draft",
        )
        quotation_line = SalesQuotationItem.objects.create(
            quotation=quotation,
            item=item,
            item_name=item.name,
            quantity=1,
            unit_price=Decimal("20.00"),
        )
        self.client.force_login(owner)

        response = self.client.post(reverse("delete_account"))

        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse("index"))
        self.assertFalse(User.objects.filter(pk=owner.pk).exists())
        self.assertFalse(UserProfile.objects.filter(user_id=owner.pk).exists())
        self.assertFalse(SalesInvoice.objects.filter(pk=invoice.pk).exists())
        self.assertFalse(SalesInvoiceItem.objects.filter(pk=line.pk).exists())
        self.assertFalse(SalesQuotation.objects.filter(pk=quotation.pk).exists())
        self.assertFalse(SalesQuotationItem.objects.filter(pk=quotation_line.pk).exists())
        self.assertFalse(Location.objects.filter(pk=location.pk).exists())


class SupplierOriginMovementTests(TestCase):
    def _make_user(self, username, *, role="admin", owner=None):
        user = User.objects.create_user(username=username, password="password123")
        profile = UserProfile.for_user(user)
        profile.role = role
        profile.status = "active"
        profile.plan = "PRO"
        profile.pro_expires = timezone.now().date() + timedelta(days=30)
        profile.plan_end = timezone.now() + timedelta(days=30)
        profile.parent_admin = UserProfile.for_user(owner) if owner else None
        profile.save()
        return user

    def _make_item(self, owner, *, name, sku):
        category = Category.objects.create(owner=owner, name=f"Category {sku}")
        brand = Brand.objects.create(owner=owner, name=f"Brand {sku}")
        return Item.objects.create(
            owner=owner,
            name=name,
            sku=sku,
            category=category,
            brand=brand,
            cost_price=Decimal("50.00"),
            price=Decimal("115.00"),
        )

    def test_manager_can_save_and_render_international_supplier_location_for_owner(self):
        owner = self._make_user("supplier-owner")
        manager = self._make_user("supplier-manager", role="manager", owner=owner)
        self.client.force_login(manager)

        form_page = self.client.get(reverse("supplier_add"))
        self.assertEqual(form_page.status_code, 200)
        self.assertContains(form_page, "Choose supplier origin")
        self.assertContains(form_page, "Country / Market")

        response = self.client.post(
            reverse("supplier_add"),
            {
                "supplier_type": Supplier.TYPE_INTERNATIONAL,
                "name": "Caribbean Export Partners",
                "contact_name": "Maya Singh",
                "phone": "+1 868 555 0110",
                "email": "MAYA@EXPORT.EXAMPLE",
                "country_code": "TT",
                "address": "Port of Spain, Trinidad",
            },
        )

        self.assertRedirects(response, reverse("supplier_list"))
        supplier = Supplier.objects.get(name="Caribbean Export Partners")
        self.assertEqual(supplier.owner, owner)
        self.assertEqual(supplier.supplier_type, Supplier.TYPE_INTERNATIONAL)
        self.assertEqual(supplier.country_code, "TT")
        self.assertEqual(supplier.address, "Port of Spain, Trinidad")

        page = self.client.get(reverse("supplier_list"))
        self.assertContains(page, reverse("supplier_edit", args=[supplier.pk]))
        self.assertContains(page, "International supplier")
        self.assertContains(page, "Trinidad &amp; Tobago")
        self.assertContains(page, "Port of Spain, Trinidad")

        export = self.client.get(reverse("export_suppliers_csv"), {"type": "international"})
        self.assertEqual(export.status_code, 200)
        self.assertContains(export, "International supplier")
        self.assertContains(export, "Port of Spain, Trinidad")

    def test_manager_can_edit_supplier_for_owner(self):
        owner = self._make_user("supplier-edit-owner")
        manager = self._make_user("supplier-edit-manager", role="manager", owner=owner)
        supplier = Supplier.objects.create(
            owner=owner,
            name="Old Wholesale",
            supplier_type=Supplier.TYPE_LOCAL,
            country_code="JM",
            contact_name="Old Contact",
            phone="876-000-0000",
            email="old@example.com",
            address="Kingston",
        )
        self.client.force_login(manager)

        response = self.client.post(
            reverse("supplier_edit", args=[supplier.pk]),
            data={
                "supplier_type": Supplier.TYPE_INTERNATIONAL,
                "name": "Updated Imports",
                "contact_name": "New Contact",
                "phone": "+1 868 555 0202",
                "email": "NEW@IMPORTS.EXAMPLE",
                "country_code": "TT",
                "address": "Port of Spain",
            },
        )

        self.assertRedirects(response, reverse("supplier_list"))
        supplier.refresh_from_db()
        self.assertEqual(supplier.name, "Updated Imports")
        self.assertEqual(supplier.supplier_type, Supplier.TYPE_INTERNATIONAL)
        self.assertEqual(supplier.contact_name, "New Contact")
        self.assertEqual(supplier.email, "new@imports.example")
        self.assertEqual(supplier.country_code, "TT")
        self.assertEqual(supplier.address, "Port of Spain")

    def test_supplier_edit_is_owner_scoped(self):
        owner = self._make_user("supplier-edit-scope-owner")
        other_owner = self._make_user("supplier-edit-scope-other")
        supplier = Supplier.objects.create(
            owner=other_owner,
            name="Private Supplier",
            supplier_type=Supplier.TYPE_LOCAL,
            country_code="JM",
        )
        self.client.force_login(owner)

        response = self.client.get(reverse("supplier_edit", args=[supplier.pk]))

        self.assertEqual(response.status_code, 404)

    def test_supplier_directory_filters_local_and_international_suppliers(self):
        owner = self._make_user("supplier-filter-owner")
        Supplier.objects.create(
            owner=owner,
            name="Kingston Wholesale",
            supplier_type=Supplier.TYPE_LOCAL,
            country_code="JM",
            address="Kingston 10",
        )
        Supplier.objects.create(
            owner=owner,
            name="Global Components",
            supplier_type=Supplier.TYPE_INTERNATIONAL,
            country_code="INT",
            address="Overseas market",
        )
        self.client.force_login(owner)

        local_page = self.client.get(reverse("supplier_list"), {"type": "local"})
        international_page = self.client.get(reverse("supplier_list"), {"type": "international"})

        self.assertContains(local_page, "Kingston Wholesale")
        self.assertNotContains(local_page, "Global Components")
        self.assertEqual(local_page.context["supplier_counts"], {"all": 2, "local": 1, "international": 1})
        self.assertContains(international_page, "Global Components")
        self.assertNotContains(international_page, "Kingston Wholesale")

    def test_supplier_directory_uses_paginated_annotated_order_counts(self):
        owner = self._make_user("supplier-performance-owner")
        location = Location.objects.create(owner=owner, name="Receiving Bay")
        item = self._make_item(owner, name="Counted Item", sku="COUNTED")
        suppliers = [
            Supplier.objects.create(
                owner=owner,
                name=f"Supplier {index:02d}",
                supplier_type=Supplier.TYPE_LOCAL,
                country_code="JM",
            )
            for index in range(30)
        ]
        for _ in range(3):
            PurchaseOrder.objects.create(
                item=item,
                supplier=suppliers[0],
                location=location,
                quantity_received=1,
                unit_cost=Decimal("10.00"),
            )
        for _ in range(2):
            PurchaseOrder.objects.create(
                item=item,
                supplier=suppliers[1],
                location=location,
                quantity_received=1,
                unit_cost=Decimal("10.00"),
            )

        self.client.force_login(owner)
        first_page = self.client.get(reverse("supplier_list"))
        second_page = self.client.get(reverse("supplier_list"), {"page": 2})
        sorted_page = self.client.get(reverse("supplier_list"), {"sort": "orders", "dir": "desc"})

        self.assertEqual(first_page.status_code, 200)
        self.assertEqual(second_page.status_code, 200)
        self.assertEqual(sorted_page.status_code, 200)
        self.assertEqual(len(first_page.context["suppliers"]), 25)
        self.assertEqual(len(second_page.context["suppliers"]), 5)
        self.assertEqual(first_page.context["page_obj"].paginator.count, 30)
        sorted_suppliers = list(sorted_page.context["suppliers"])
        self.assertEqual(sorted_suppliers[0].order_count, 3)
        self.assertEqual(sorted_suppliers[1].order_count, 2)
        self.assertContains(sorted_page, "3 Orders")

    def test_inventory_overview_separates_local_and_international_receipts(self):
        owner = self._make_user("receipt-origin-owner")
        location = Location.objects.create(owner=owner, name="Receiving Bay")
        local_supplier = Supplier.objects.create(
            owner=owner,
            name="Local Farm Supply",
            supplier_type=Supplier.TYPE_LOCAL,
            country_code="JM",
        )
        international_supplier = Supplier.objects.create(
            owner=owner,
            name="Overseas Equipment Co",
            supplier_type=Supplier.TYPE_INTERNATIONAL,
            country_code="INT",
        )
        local_item = self._make_item(owner, name="Local Packing Tape", sku="LOCAL-TAPE")
        imported_item = self._make_item(owner, name="Imported Scanner", sku="IMPORT-SCAN")
        PurchaseOrder.objects.create(
            item=local_item,
            supplier=local_supplier,
            location=location,
            quantity_received=7,
            unit_cost=Decimal("50.00"),
        )
        PurchaseOrder.objects.create(
            item=imported_item,
            supplier=international_supplier,
            location=location,
            quantity_received=3,
            unit_cost=Decimal("80.00"),
        )
        self.client.force_login(owner)

        response = self.client.get(reverse("inventory_overview"))

        local_names = [row["item_name"] for row in response.context["local_received_rows"]]
        imported_names = [row["item_name"] for row in response.context["imported_received_rows"]]
        self.assertIn("Local Packing Tape", local_names)
        self.assertNotIn("Imported Scanner", local_names)
        self.assertIn("Imported Scanner", imported_names)
        self.assertNotIn("Local Packing Tape", imported_names)

    def test_overview_shows_sale_at_exact_location_and_completed_transfer_at_both_ends(self):
        owner = self._make_user("movement-owner")
        source = Location.objects.create(owner=owner, name="Downtown Shop")
        destination = Location.objects.create(owner=owner, name="North Branch")
        item = self._make_item(owner, name="USB Barcode Scanner", sku="SCAN-MOVE")
        StockRecord.objects.create(item=item, location=source, quantity=20)
        sale = Sale.objects.create(
            owner=owner,
            cashier=owner,
            location=source,
            receipt_no=501,
            receipt_status="finalized",
        )
        SaleItem.objects.create(
            sale=sale,
            item=item,
            quantity=2,
            unit_price=Decimal("115.00"),
            unit_cost=Decimal("50.00"),
        )
        StockTransfer.objects.create(
            item=item,
            from_location=source,
            to_location=destination,
            quantity=4,
            status="COMPLETED",
            user=owner,
        )
        self.client.force_login(owner)

        response = self.client.get(reverse("inventory_overview"))

        transfer_in = response.context["transfer_in_rows"]
        transfer_out = response.context["transfer_out_rows"]
        self.assertTrue(any(row["location_name"] == "North Branch" and row["status"] == "Transferred" for row in transfer_in))
        self.assertTrue(any(row["location_name"] == "Downtown Shop" and row["reference"] == "To North Branch" for row in transfer_out))
        self.assertTrue(any(row["location_name"] == "Downtown Shop" and row["reference"] == "Receipt #501" and row["status"] == "Customer purchase" for row in transfer_out))

    def test_overview_scopes_transfer_direction_to_each_users_default_location(self):
        owner = self._make_user("movement-location-owner")
        main_store = Location.objects.create(owner=owner, name="Main Store")
        unassigned = Location.objects.create(owner=owner, name="Unassigned")
        owner.profile.default_location = main_store
        owner.profile.save(update_fields=["default_location"])
        receiving_manager = self._make_user(
            "movement-location-manager",
            role="manager",
            owner=owner,
        )
        receiving_manager.profile.default_location = unassigned
        receiving_manager.profile.save(update_fields=["default_location"])
        item = self._make_item(owner, name="Location Routed Item", sku="LOC-ROUTE")
        StockTransfer.objects.create(
            item=item,
            from_location=main_store,
            to_location=unassigned,
            quantity=3,
            status="COMPLETED",
            user=owner,
        )

        self.client.force_login(owner)
        source_response = self.client.get(reverse("inventory_overview"))
        self.assertEqual(source_response.status_code, 200)
        self.assertEqual(source_response.context["transfer_location_name"], "Main Store")
        self.assertEqual(source_response.context["transfer_in_rows"], [])
        self.assertTrue(
            any(
                row["item_name"] == "Location Routed Item"
                and row["reference"] == "To Unassigned"
                for row in source_response.context["transfer_out_rows"]
            )
        )
        self.assertContains(source_response, "From Main Store")

        self.client.force_login(receiving_manager)
        destination_response = self.client.get(reverse("inventory_overview"))
        self.assertEqual(destination_response.status_code, 200)
        self.assertEqual(destination_response.context["transfer_location_name"], "Unassigned")
        self.assertEqual(destination_response.context["transfer_out_rows"], [])
        self.assertTrue(
            any(
                row["item_name"] == "Location Routed Item"
                and row["reference"] == "From Main Store"
                for row in destination_response.context["transfer_in_rows"]
            )
        )
        self.assertContains(destination_response, "Into Unassigned")

    def test_staff_settings_assigns_the_real_unassigned_location(self):
        owner = self._make_user("unassigned-location-owner")
        unassigned = Location.objects.create(owner=owner, name="Unassigned")
        manager = self._make_user(
            "unassigned-location-manager",
            role="manager",
            owner=owner,
        )
        self.client.force_login(owner)

        settings_page = self.client.get(reverse("settings"))
        self.assertEqual(settings_page.status_code, 200)
        self.assertContains(settings_page, "No location assigned")
        self.assertContains(settings_page, f'value="{unassigned.id}"')
        self.assertContains(settings_page, "Unassigned")

        response = self.client.post(
            reverse("manage_staff"),
            {
                "action": "update_location",
                "user_id": manager.id,
                "new_location": unassigned.id,
            },
        )

        self.assertRedirects(response, reverse("settings"))
        manager.profile.refresh_from_db()
        self.assertEqual(manager.profile.default_location, unassigned)

    def test_overview_scopes_customer_purchase_outbound_rows_to_default_location(self):
        owner = self._make_user("purchase-location-owner")
        main_store = Location.objects.create(owner=owner, name="Main Store")
        unassigned = Location.objects.create(owner=owner, name="Unassigned")
        owner.profile.default_location = main_store
        owner.profile.save(update_fields=["default_location"])
        manager = self._make_user(
            "purchase-location-manager",
            role="manager",
            owner=owner,
        )
        manager.profile.default_location = unassigned
        manager.profile.save(update_fields=["default_location"])
        item = self._make_item(owner, name="Location Sale Item", sku="LOC-SALE")
        StockRecord.objects.create(item=item, location=main_store, quantity=10)
        StockRecord.objects.create(item=item, location=unassigned, quantity=10)

        main_sale = Sale.objects.create(
            owner=owner,
            cashier=owner,
            location=main_store,
            receipt_no=701,
            receipt_status="finalized",
        )
        SaleItem.objects.create(
            sale=main_sale,
            item=item,
            quantity=1,
            unit_price=Decimal("115.00"),
            unit_cost=Decimal("50.00"),
        )
        unassigned_sale = Sale.objects.create(
            owner=owner,
            cashier=owner,
            location=unassigned,
            receipt_no=702,
            receipt_status="finalized",
        )
        SaleItem.objects.create(
            sale=unassigned_sale,
            item=item,
            quantity=2,
            unit_price=Decimal("115.00"),
            unit_cost=Decimal("50.00"),
        )

        self.client.force_login(owner)
        main_response = self.client.get(reverse("inventory_overview"))
        main_references = [row["reference"] for row in main_response.context["transfer_out_rows"]]
        self.assertIn("Receipt #701", main_references)
        self.assertNotIn("Receipt #702", main_references)

        self.client.force_login(manager)
        unassigned_response = self.client.get(reverse("inventory_overview"))
        unassigned_references = [
            row["reference"] for row in unassigned_response.context["transfer_out_rows"]
        ]
        self.assertIn("Receipt #702", unassigned_references)
        self.assertNotIn("Receipt #701", unassigned_references)

    def test_only_cash_register_sales_invoice_lines_appear_as_customer_purchases(self):
        owner = self._make_user("invoice-movement-owner")
        location = Location.objects.create(owner=owner, name="Invoice Counter")
        item = self._make_item(owner, name="Receipt Printer", sku="PRINT-MOVE")
        ordinary_invoice = SalesInvoice.objects.create(
            owner=owner,
            created_by=owner,
            location=location,
            notes="Ordinary invoice that did not change stock.",
        )
        SalesInvoiceItem.objects.create(invoice=ordinary_invoice, item=item, quantity=1, unit_price=Decimal("115.00"))
        pos_invoice = SalesInvoice.objects.create(
            owner=owner,
            created_by=owner,
            location=location,
            notes=_compose_document_notes("POS invoice", {"source": "cash_register"}),
        )
        SalesInvoiceItem.objects.create(invoice=pos_invoice, item=item, quantity=2, unit_price=Decimal("115.00"))
        self.client.force_login(owner)

        response = self.client.get(reverse("inventory_overview"))
        references = [row["reference"] for row in response.context["transfer_out_rows"]]

        self.assertIn(pos_invoice.invoice_no, references)
        self.assertNotIn(ordinary_invoice.invoice_no, references)

    def test_html_transfer_creates_completed_stock_transfer(self):
        owner = self._make_user("html-transfer-owner")
        source = Location.objects.create(owner=owner, name="Warehouse")
        destination = Location.objects.create(owner=owner, name="Retail Floor")
        item = self._make_item(owner, name="Transfer Item", sku="HTML-XFER")
        StockRecord.objects.create(item=item, location=source, quantity=9)
        self.client.force_login(owner)

        response = self.client.post(
            reverse("transfer_stock"),
            {
                "item_id": item.id,
                "from_location": source.name,
                "to_location": destination.id,
                "quantity": 4,
            },
        )

        self.assertRedirects(response, reverse("transfer_stock"))
        transfer = StockTransfer.objects.get(item=item)
        self.assertEqual(transfer.status, "COMPLETED")
        self.assertEqual(transfer.from_location, source)
        self.assertEqual(transfer.to_location, destination)
        self.assertEqual(StockRecord.objects.get(item=item, location=source).quantity, 5)
        self.assertEqual(StockRecord.objects.get(item=item, location=destination).quantity, 4)

    def test_html_transfer_allows_manager_to_transfer_into_default_location(self):
        owner = self._make_user("html-transfer-in-owner")
        source = Location.objects.create(owner=owner, name="Warehouse")
        destination = Location.objects.create(owner=owner, name="My Branch")
        manager = self._make_user(
            "html-transfer-in-manager",
            role="manager",
            owner=owner,
        )
        manager.profile.default_location = destination
        manager.profile.save(update_fields=["default_location"])
        item = self._make_item(owner, name="Transfer In Item", sku="HTML-IN")
        StockRecord.objects.create(item=item, location=source, quantity=6)
        self.client.force_login(manager)

        response = self.client.post(
            reverse("transfer_stock"),
            {
                "item_id": item.id,
                "from_location": source.id,
                "to_location": destination.id,
                "quantity": 2,
            },
        )

        self.assertRedirects(response, reverse("transfer_stock"))
        transfer = StockTransfer.objects.get(item=item)
        self.assertEqual(transfer.from_location, source)
        self.assertEqual(transfer.to_location, destination)
        self.assertEqual(StockRecord.objects.get(item=item, location=source).quantity, 4)
        self.assertEqual(StockRecord.objects.get(item=item, location=destination).quantity, 2)


class SalesInvoiceCollectionLifecycleTests(TestCase):
    def setUp(self):
        super().setUp()
        seed_patcher = patch("inventory.views._seed_inventory_from_shared_json", return_value=False)
        seed_patcher.start()
        self.addCleanup(seed_patcher.stop)

    def _make_user(self, username, *, role="admin", owner=None, default_location=None):
        user = User.objects.create_user(username=username, password="password123")
        profile = UserProfile.for_user(user)
        profile.role = role
        profile.status = "active"
        profile.plan = "PRO"
        profile.pro_expires = timezone.now().date() + timedelta(days=30)
        profile.plan_end = timezone.now() + timedelta(days=30)
        profile.parent_admin = UserProfile.for_user(owner) if owner else None
        profile.default_location = default_location
        profile.save()
        return user

    def _make_item(self, owner, *, name, sku, price="100.00"):
        category = Category.objects.create(owner=owner, name=f"Category {sku}")
        brand = Brand.objects.create(owner=owner, name=f"Brand {sku}")
        return Item.objects.create(
            owner=owner,
            name=name,
            sku=sku,
            category=category,
            brand=brand,
            cost_price=Decimal("40.00"),
            price=Decimal(price),
        )

    def _make_invoice(
        self,
        owner,
        location,
        *,
        item_name,
        sku,
        total="100.00",
        collection_status="pending_payment",
        status="issued",
    ):
        item = self._make_item(owner, name=item_name, sku=sku, price=total)
        customer = Customer.objects.create(owner=owner, name=f"Customer {sku}")
        invoice = SalesInvoice.objects.create(
            owner=owner,
            created_by=owner,
            customer=customer,
            location=location,
            status=status,
            collection_status=collection_status,
            collection_status_changed_at=(
                timezone.now() if collection_status != "pending_payment" else None
            ),
            collected_at=(
                timezone.now()
                if collection_status in {"collected_immediately", "collected_after_hold"}
                else None
            ),
            collection_recorded_by=(
                owner if collection_status != "pending_payment" else None
            ),
            subtotal=Decimal(total),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal(total),
        )
        SalesInvoiceItem.objects.create(
            invoice=invoice,
            item=item,
            item_name=item.name,
            quantity=1,
            unit_price=Decimal(total),
        )
        return invoice, item

    def _record_payment(self, invoice, amount, pickup_status):
        return self.client.post(
            reverse("sales_invoice_payment", args=[invoice.pk]),
            {
                "amount": str(amount),
                "payment_method": "cash",
                "reference": f"PAY-{invoice.pk}",
                "pickup_status": pickup_status,
            },
        )

    @staticmethod
    def _panel_item_names(response, context_key):
        return [row["item_name"] for row in response.context[context_key]]

    def test_partial_payment_stays_pending_and_out_of_collection_panels(self):
        owner = self._make_user("pickup-partial-owner")
        location = Location.objects.create(owner=owner, name="Main Store")
        owner.profile.default_location = location
        owner.profile.save(update_fields=["default_location"])
        invoice, _item = self._make_invoice(
            owner,
            location,
            item_name="Partially Paid Blender",
            sku="PICKUP-PARTIAL",
        )
        self.client.force_login(owner)

        response = self._record_payment(invoice, "40.00", "leave_in_store")

        self.assertEqual(response.status_code, 302)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, "issued")
        self.assertEqual(invoice.collection_status, "pending_payment")
        self.assertIsNone(invoice.collection_status_changed_at)
        self.assertIsNone(invoice.collected_at)
        overview = self.client.get(reverse("inventory_overview"))
        self.assertNotIn(
            "Partially Paid Blender",
            self._panel_item_names(overview, "remaining_in_store_rows"),
        )
        self.assertNotIn(
            "Partially Paid Blender",
            self._panel_item_names(overview, "delivered_from_uncollected_rows"),
        )
        self.assertNotIn(
            "Partially Paid Blender",
            self._panel_item_names(overview, "collected_rows"),
        )

    def test_final_payment_left_in_store_appears_only_in_remaining_panel(self):
        owner = self._make_user("pickup-held-owner")
        location = Location.objects.create(owner=owner, name="Main Store")
        owner.profile.default_location = location
        owner.profile.save(update_fields=["default_location"])
        invoice, _item = self._make_invoice(
            owner,
            location,
            item_name="Held Television",
            sku="PICKUP-HELD",
        )
        self.client.force_login(owner)

        response = self._record_payment(invoice, "100.00", "leave_in_store")

        self.assertEqual(response.status_code, 302)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, "paid")
        self.assertEqual(invoice.collection_status, "awaiting_collection")
        self.assertIsNotNone(invoice.collection_status_changed_at)
        self.assertIsNone(invoice.collected_at)
        self.assertEqual(invoice.collection_recorded_by, owner)
        overview = self.client.get(reverse("inventory_overview"))
        self.assertIn(
            "Held Television",
            self._panel_item_names(overview, "remaining_in_store_rows"),
        )
        self.assertNotIn(
            "Held Television",
            self._panel_item_names(overview, "delivered_from_uncollected_rows"),
        )
        self.assertNotIn(
            "Held Television",
            self._panel_item_names(overview, "collected_rows"),
        )

    def test_mark_collected_moves_held_invoice_to_delivered_from_uncollected(self):
        owner = self._make_user("pickup-later-owner")
        location = Location.objects.create(owner=owner, name="Main Store")
        owner.profile.default_location = location
        owner.profile.save(update_fields=["default_location"])
        invoice, _item = self._make_invoice(
            owner,
            location,
            item_name="Later Pickup Freezer",
            sku="PICKUP-LATER",
        )
        self.client.force_login(owner)
        self._record_payment(invoice, "100.00", "leave_in_store")

        response = self.client.post(
            reverse("sales_invoice_mark_collected", args=[invoice.pk]),
        )

        self.assertRedirects(response, reverse("sales_invoice_detail", args=[invoice.pk]))
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, "paid")
        self.assertEqual(invoice.collection_status, "collected_after_hold")
        self.assertIsNotNone(invoice.collected_at)
        self.assertEqual(invoice.collection_recorded_by, owner)
        overview = self.client.get(reverse("inventory_overview"))
        self.assertIn(
            "Later Pickup Freezer",
            self._panel_item_names(overview, "delivered_from_uncollected_rows"),
        )
        self.assertNotIn(
            "Later Pickup Freezer",
            self._panel_item_names(overview, "remaining_in_store_rows"),
        )
        self.assertNotIn(
            "Later Pickup Freezer",
            self._panel_item_names(overview, "collected_rows"),
        )

    def test_final_payment_collected_now_appears_only_in_collected_panel(self):
        owner = self._make_user("pickup-now-owner")
        location = Location.objects.create(owner=owner, name="Main Store")
        owner.profile.default_location = location
        owner.profile.save(update_fields=["default_location"])
        invoice, _item = self._make_invoice(
            owner,
            location,
            item_name="Immediate Pickup Microwave",
            sku="PICKUP-NOW",
        )
        self.client.force_login(owner)

        response = self._record_payment(invoice, "100.00", "collected_now")

        self.assertEqual(response.status_code, 302)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, "paid")
        self.assertEqual(invoice.collection_status, "collected_immediately")
        self.assertIsNotNone(invoice.collected_at)
        self.assertEqual(invoice.collection_recorded_by, owner)
        overview = self.client.get(reverse("inventory_overview"))
        self.assertIn(
            "Immediate Pickup Microwave",
            self._panel_item_names(overview, "collected_rows"),
        )
        self.assertNotIn(
            "Immediate Pickup Microwave",
            self._panel_item_names(overview, "remaining_in_store_rows"),
        )
        self.assertNotIn(
            "Immediate Pickup Microwave",
            self._panel_item_names(overview, "delivered_from_uncollected_rows"),
        )

    def test_finalized_pos_sale_appears_as_immediate_collection(self):
        owner = self._make_user("pickup-pos-owner")
        location = Location.objects.create(owner=owner, name="POS Counter")
        owner.profile.default_location = location
        owner.profile.save(update_fields=["default_location"])
        item = self._make_item(
            owner,
            name="Counter Pickup Kettle",
            sku="PICKUP-POS",
            price="80.00",
        )
        StockRecord.objects.create(item=item, location=location, quantity=5)
        sale = Sale.objects.create(
            owner=owner,
            cashier=owner,
            location=location,
            receipt_no=8101,
            receipt_status="finalized",
            subtotal=Decimal("80.00"),
            total_price=Decimal("80.00"),
            amount_paid=Decimal("80.00"),
        )
        SaleItem.objects.create(
            sale=sale,
            item=item,
            quantity=1,
            unit_price=Decimal("80.00"),
            unit_cost=Decimal("40.00"),
            total_price=Decimal("80.00"),
        )
        self.client.force_login(owner)

        overview = self.client.get(reverse("inventory_overview"))

        collected_rows = overview.context["collected_rows"]
        self.assertTrue(
            any(
                row["item_name"] == "Counter Pickup Kettle"
                and row["reference"] == "Receipt #8101"
                and row["status"] == "Picked up at purchase"
                for row in collected_rows
            )
        )
        self.assertNotIn(
            "Counter Pickup Kettle",
            self._panel_item_names(overview, "remaining_in_store_rows"),
        )
        self.assertNotIn(
            "Counter Pickup Kettle",
            self._panel_item_names(overview, "delivered_from_uncollected_rows"),
        )

    def test_collection_panels_honor_default_location_and_tenant_scope(self):
        owner = self._make_user("pickup-scope-owner")
        main_store = Location.objects.create(owner=owner, name="Main Store")
        branch = Location.objects.create(owner=owner, name="Branch Store")
        owner.profile.default_location = main_store
        owner.profile.save(update_fields=["default_location"])
        self._make_invoice(
            owner,
            main_store,
            item_name="Visible Held Radio",
            sku="PICKUP-SCOPE-MAIN",
            status="paid",
            collection_status="awaiting_collection",
        )
        self._make_invoice(
            owner,
            branch,
            item_name="Other Branch Radio",
            sku="PICKUP-SCOPE-BRANCH",
            status="paid",
            collection_status="awaiting_collection",
        )

        other_owner = self._make_user("pickup-scope-other")
        other_location = Location.objects.create(owner=other_owner, name="Other Tenant Store")
        foreign_invoice, _foreign_item = self._make_invoice(
            other_owner,
            other_location,
            item_name="Other Tenant Radio",
            sku="PICKUP-SCOPE-TENANT",
            status="paid",
            collection_status="awaiting_collection",
        )
        self.client.force_login(owner)

        overview = self.client.get(reverse("inventory_overview"))

        remaining_names = self._panel_item_names(overview, "remaining_in_store_rows")
        self.assertIn("Visible Held Radio", remaining_names)
        self.assertNotIn("Other Branch Radio", remaining_names)
        self.assertNotIn("Other Tenant Radio", remaining_names)
        forbidden = self.client.post(
            reverse("sales_invoice_mark_collected", args=[foreign_invoice.pk]),
        )
        self.assertEqual(forbidden.status_code, 404)
        foreign_invoice.refresh_from_db()
        self.assertEqual(foreign_invoice.collection_status, "awaiting_collection")

    def test_payment_reversal_resets_collection_classification_to_pending(self):
        owner = self._make_user("pickup-reversal-owner")
        location = Location.objects.create(owner=owner, name="Main Store")
        owner.profile.default_location = location
        owner.profile.save(update_fields=["default_location"])
        invoice, _item = self._make_invoice(
            owner,
            location,
            item_name="Reversed Pickup Washer",
            sku="PICKUP-REVERSE",
        )
        self.client.force_login(owner)
        self._record_payment(invoice, "100.00", "collected_now")
        payment = SalesInvoicePayment.objects.get(invoice=invoice)

        response = self.client.post(
            reverse("sales_invoice_payment_revert", args=[invoice.pk, payment.pk]),
            {"reversal_reason": "Pickup payment was recorded in error."},
        )

        self.assertEqual(response.status_code, 302)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, "issued")
        self.assertEqual(invoice.collection_status, "pending_payment")
        self.assertIsNone(invoice.collected_at)
        self.assertIsNone(invoice.collection_recorded_by)
        overview = self.client.get(reverse("inventory_overview"))
        for context_key in (
            "remaining_in_store_rows",
            "delivered_from_uncollected_rows",
            "collected_rows",
        ):
            self.assertNotIn(
                "Reversed Pickup Washer",
                self._panel_item_names(overview, context_key),
            )

    def test_second_final_payment_cannot_duplicate_money_or_change_pickup_choice(self):
        owner = self._make_user("pickup-duplicate-payment-owner")
        location = Location.objects.create(owner=owner, name="Main Store")
        owner.profile.default_location = location
        owner.profile.save(update_fields=["default_location"])
        invoice, _item = self._make_invoice(
            owner,
            location,
            item_name="Single Settlement Dryer",
            sku="PICKUP-DUPLICATE-PAYMENT",
        )
        self.client.force_login(owner)
        self._record_payment(invoice, "100.00", "leave_in_store")

        response = self._record_payment(invoice, "25.00", "collected_now")

        self.assertEqual(response.status_code, 302)
        invoice.refresh_from_db()
        self.assertEqual(invoice.payments.count(), 1)
        self.assertEqual(invoice.total_paid_amount, Decimal("100.00"))
        self.assertEqual(invoice.collection_status, "awaiting_collection")

    def test_mark_collected_rejects_invoice_not_awaiting_pickup(self):
        owner = self._make_user("pickup-invalid-owner")
        location = Location.objects.create(owner=owner, name="Main Store")
        invoice, _item = self._make_invoice(
            owner,
            location,
            item_name="Already Collected Toaster",
            sku="PICKUP-INVALID",
            status="paid",
            collection_status="collected_immediately",
        )
        original_collected_at = invoice.collected_at
        self.client.force_login(owner)

        response = self.client.post(
            reverse("sales_invoice_mark_collected", args=[invoice.pk]),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "not currently listed as remaining in store")
        invoice.refresh_from_db()
        self.assertEqual(invoice.collection_status, "collected_immediately")
        self.assertEqual(invoice.collected_at, original_collected_at)

    def test_paid_legacy_invoice_can_be_classified_as_remaining_in_store(self):
        owner = self._make_user("pickup-legacy-owner")
        location = Location.objects.create(owner=owner, name="Main Store")
        owner.profile.default_location = location
        owner.profile.save(update_fields=["default_location"])
        invoice, _item = self._make_invoice(
            owner,
            location,
            item_name="Legacy Held Cooker",
            sku="PICKUP-LEGACY",
            status="paid",
            collection_status="untracked",
        )
        invoice.collection_status_changed_at = None
        invoice.collection_recorded_by = None
        invoice.save(update_fields=["collection_status_changed_at", "collection_recorded_by"])
        self.client.force_login(owner)

        response = self.client.post(
            reverse("sales_invoice_classify_collection", args=[invoice.pk]),
            {"collection_status": "awaiting_collection"},
        )

        self.assertRedirects(response, reverse("sales_invoice_detail", args=[invoice.pk]))
        invoice.refresh_from_db()
        self.assertEqual(invoice.collection_status, "awaiting_collection")
        self.assertIsNotNone(invoice.collection_status_changed_at)
        self.assertEqual(invoice.collection_recorded_by, owner)
        overview = self.client.get(reverse("inventory_overview"))
        self.assertIn(
            "Legacy Held Cooker",
            self._panel_item_names(overview, "remaining_in_store_rows"),
        )

    def test_cashier_cannot_record_pickup_for_another_location(self):
        owner = self._make_user("pickup-location-guard-owner")
        main_store = Location.objects.create(owner=owner, name="Main Store")
        branch = Location.objects.create(owner=owner, name="Branch Store")
        cashier = self._make_user(
            "pickup-location-guard-cashier",
            role="cashier",
            owner=owner,
            default_location=main_store,
        )
        invoice, _item = self._make_invoice(
            owner,
            branch,
            item_name="Branch Held Fan",
            sku="PICKUP-LOCATION-GUARD",
            status="paid",
            collection_status="awaiting_collection",
        )
        self.client.force_login(cashier)

        response = self.client.post(
            reverse("sales_invoice_mark_collected", args=[invoice.pk]),
        )

        self.assertEqual(response.status_code, 404)
        invoice.refresh_from_db()
        self.assertEqual(invoice.collection_status, "awaiting_collection")

    def test_historical_delivery_report_excludes_holds_created_after_selected_date(self):
        owner = self._make_user("pickup-report-date-owner")
        location = Location.objects.create(owner=owner, name="Main Store")
        owner.profile.default_location = location
        owner.profile.save(update_fields=["default_location"])
        invoice, _item = self._make_invoice(
            owner,
            location,
            item_name="Future Held Speaker",
            sku="PICKUP-FUTURE-HOLD",
            status="paid",
            collection_status="awaiting_collection",
        )
        invoice.collection_status_changed_at = timezone.now() + timedelta(days=1)
        invoice.save(update_fields=["collection_status_changed_at"])
        self.client.force_login(owner)

        response = self.client.get(
            reverse("deliveries_collections"),
            {"date": timezone.localdate().isoformat()},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["remaining_in_store_orders"], [])
        self.assertNotContains(response, "Future Held Speaker")


class InventoryOverviewCategoryTests(TestCase):
    def _make_user(self, username, role="admin"):
        user = User.objects.create_user(username=username, password="password123")
        profile = UserProfile.for_user(user)
        profile.role = role
        profile.status = "active"
        profile.plan = "PRO"
        profile.pro_expires = timezone.now().date() + timedelta(days=30)
        profile.plan_end = timezone.now() + timedelta(days=30)
        profile.save()
        return user

    def test_category_groups_include_item_counts_and_stock_totals(self):
        owner = self._make_user("category-overview-owner")
        warehouse = Location.objects.create(owner=owner, name="Warehouse")
        storefront = Location.objects.create(owner=owner, name="Storefront")
        beverages = Category.objects.create(owner=owner, name="Beverages")
        snacks = Category.objects.create(owner=owner, name="Snacks")
        brand = Brand.objects.create(owner=owner, name="QuickStock")
        water = Item.objects.create(
            owner=owner,
            name="Mineral Water",
            sku="WATER-01",
            category=beverages,
            brand=brand,
            cost_price=Decimal("50.00"),
            price=Decimal("80.00"),
        )
        juice = Item.objects.create(
            owner=owner,
            name="Orange Juice",
            sku="JUICE-01",
            category=beverages,
            brand=brand,
            cost_price=Decimal("70.00"),
            price=Decimal("110.00"),
        )
        chips = Item.objects.create(
            owner=owner,
            name="Plantain Chips",
            sku="CHIPS-01",
            category=snacks,
            brand=brand,
            cost_price=Decimal("40.00"),
            price=Decimal("75.00"),
        )
        StockRecord.objects.create(item=water, location=warehouse, quantity=4)
        StockRecord.objects.create(item=water, location=storefront, quantity=3)
        StockRecord.objects.create(item=juice, location=storefront, quantity=5)
        StockRecord.objects.create(item=chips, location=warehouse, quantity=9)
        self.client.force_login(owner)

        response = self.client.get(reverse("inventory_overview"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_items"], 3)
        self.assertEqual(response.context["total_categories"], 2)
        self.assertEqual(response.context["total_inventory_units"], 21)
        self.assertEqual(
            [group["category"] for group in response.context["grouped_inventory"]],
            ["Beverages", "Snacks"],
        )
        groups = {group["category"]: group for group in response.context["grouped_inventory"]}
        self.assertEqual(groups["Beverages"]["item_count"], 2)
        self.assertEqual(groups["Beverages"]["total_quantity"], 12)
        self.assertEqual(groups["Snacks"]["item_count"], 1)
        self.assertEqual(groups["Snacks"]["total_quantity"], 9)
        beverage_rows = {row["sku"]: row for row in groups["Beverages"]["items"]}
        self.assertEqual(
            [row["name"] for row in groups["Beverages"]["items"]],
            ["Mineral Water", "Orange Juice"],
        )
        self.assertEqual(beverage_rows["WATER-01"]["quantity"], 7)
        self.assertEqual(beverage_rows["JUICE-01"]["quantity"], 5)
        self.assertContains(response, "Catalogue by Category")
        self.assertContains(response, "Item / Location stock")
        self.assertContains(response, 'class="inventory-overview-summary"')
        self.assertContains(response, "inventory_overview.js?v=category-toggle-fix")

        script_path = Path(__file__).resolve().parent / "static" / "inventory" / "js" / "inventory_overview.js"
        style_path = Path(__file__).resolve().parent / "static" / "inventory" / "css" / "style.css"
        self.assertIn("classList.toggle(\"is-collapsed\"", script_path.read_text())
        self.assertIn(".inventory-overview-item-list[hidden]", style_path.read_text())

    @patch("inventory.views._seed_inventory_from_shared_json", return_value=False)
    def test_overview_normalizes_category_aliases_and_renders_location_badges(self, _seed_inventory):
        owner = self._make_user("category-normalized-overview-owner")
        main_store = Location.objects.create(owner=owner, name="Main Store")
        depot = Location.objects.create(owner=owner, name="3 FELIX FOX BOULEVARD, KINGSTON")
        snack = Category.objects.create(owner=owner, name="Snack")
        snacks = Category.objects.create(owner=owner, name="Snacks")
        brand = Brand.objects.create(owner=owner, name="QuickStock")
        button = Item.objects.create(
            owner=owner,
            name="Police Button",
            sku="SNK-BUTTON",
            category=snack,
            brand=brand,
            cost_price=Decimal("10.00"),
            price=Decimal("25.00"),
        )
        chips = Item.objects.create(
            owner=owner,
            name="Banana Chips",
            sku="SNK-BCHIP",
            category=snacks,
            brand=brand,
            cost_price=Decimal("80.00"),
            price=Decimal("150.00"),
        )
        StockRecord.objects.create(item=button, location=main_store, quantity=1399)
        StockRecord.objects.create(item=chips, location=depot, quantity=8000)
        self.client.force_login(owner)

        response = self.client.get(reverse("inventory_overview"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_categories"], 1)
        group = response.context["grouped_inventory"][0]
        self.assertEqual(group["category"], "Snacks")
        self.assertTrue(group["has_merged_aliases"])
        self.assertEqual(group["item_count"], 2)
        self.assertEqual(group["total_quantity"], 9399)
        self.assertContains(response, "Merged: Snack, Snacks")
        self.assertContains(response, "inventory-overview-location-badge")
        self.assertContains(response, "Main Store: 1,399")
        self.assertContains(response, "3 Felix Fox Boulevard: 8,000")
        self.assertContains(response, 'href="#inventory-overview-local-received"')

    @patch("inventory.views._seed_inventory_from_shared_json", return_value=False)
    def test_empty_catalogue_uses_full_width_category_empty_state(self, _seed_inventory):
        owner = self._make_user("empty-category-overview-owner")
        self.client.force_login(owner)

        response = self.client.get(reverse("inventory_overview"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_categories"], 0)
        self.assertEqual(response.context["total_inventory_units"], 0)
        self.assertContains(response, "No inventory items available to group by category.")
        self.assertContains(response, "inventory-overview-empty-state")


@override_settings(
    QUICKSTOCK_IDLE_TIMEOUT_SECONDS=300,
    QUICKSTOCK_SECURITY_HEARTBEAT_SECONDS=120,
    QUICKSTOCK_ENFORCE_SESSION_FINGERPRINT=False,
)
class SessionSecurityTests(TestCase):
    def _make_user(self, username, role="admin"):
        user = User.objects.create_user(username=username, password="password123")
        profile = UserProfile.for_user(user)
        profile.role = role
        profile.status = "active"
        profile.plan = "PRO"
        profile.pro_expires = timezone.now().date() + timedelta(days=30)
        profile.plan_end = timezone.now() + timedelta(days=30)
        profile.save()
        return user

    def _session_fingerprint(self, user_agent="UnitTestBrowser/1.0", accept_language="en-US,en;q=0.9"):
        return hashlib.sha256(user_agent.encode("utf-8")).hexdigest()

    def _legacy_session_fingerprint(self, user_agent="UnitTestBrowser/1.0", accept_language="en-US,en;q=0.9"):
        return hashlib.sha256(f"{user_agent}|{accept_language}".encode("utf-8")).hexdigest()

    def test_idle_session_is_logged_out_on_next_request(self):
        user = self._make_user("idle-user")
        self.client.force_login(user)
        session = self.client.session
        session["qs_session_fingerprint"] = self._session_fingerprint()
        session["qs_last_activity_at"] = (timezone.now() - timedelta(minutes=10)).isoformat()
        session.save()

        response = self.client.get(
            reverse("profile"),
            HTTP_USER_AGENT="UnitTestBrowser/1.0",
            HTTP_ACCEPT_LANGUAGE="en-US,en;q=0.9",
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("login"))

    def test_idle_session_can_still_open_public_index_page(self):
        user = self._make_user("idle-index-user")
        self.client.force_login(user)
        session = self.client.session
        session["qs_session_fingerprint"] = self._session_fingerprint()
        session["qs_last_activity_at"] = (timezone.now() - timedelta(minutes=10)).isoformat()
        session.save()

        response = self.client.get(
            reverse("index"),
            HTTP_USER_AGENT="UnitTestBrowser/1.0",
            HTTP_ACCEPT_LANGUAGE="en-US,en;q=0.9",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "QuickStock JA")
        self.assertIn("_auth_user_id", self.client.session)

    def test_authenticated_cashier_logo_home_opens_index_without_role_redirect(self):
        cashier = self._make_user("cashier-logo-home", role="cashier")
        self.client.force_login(cashier)

        response = self.client.get(reverse("index"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "QuickStock JA")

    def test_fingerprint_mismatch_does_not_logout_session_by_default(self):
        user = self._make_user("fingerprint-user")
        self.client.force_login(user)
        session = self.client.session
        session["qs_session_fingerprint"] = self._session_fingerprint(user_agent="OriginalBrowser/1.0")
        session["qs_session_fingerprint_version"] = 2
        session["qs_last_activity_at"] = timezone.now().isoformat()
        session.save()

        response = self.client.get(
            reverse("profile"),
            HTTP_USER_AGENT="DifferentBrowser/2.0",
            HTTP_ACCEPT_LANGUAGE="en-US,en;q=0.9",
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("_auth_user_id", self.client.session)

    @override_settings(QUICKSTOCK_ENFORCE_SESSION_FINGERPRINT=True)
    def test_fingerprint_mismatch_logs_out_when_enforcement_enabled(self):
        user = self._make_user("strict-fingerprint-user")
        self.client.force_login(user)
        session = self.client.session
        session["qs_session_fingerprint"] = self._session_fingerprint(user_agent="OriginalBrowser/1.0")
        session["qs_session_fingerprint_version"] = 2
        session["qs_last_activity_at"] = timezone.now().isoformat()
        session.save()

        response = self.client.get(
            reverse("profile"),
            HTTP_USER_AGENT="DifferentBrowser/2.0",
            HTTP_ACCEPT_LANGUAGE="en-US,en;q=0.9",
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("login"))

    def test_legacy_fingerprint_survives_accept_language_change(self):
        user = self._make_user("legacy-fingerprint-user")
        self.client.force_login(user)
        session = self.client.session
        session["qs_session_fingerprint"] = self._legacy_session_fingerprint(
            user_agent="UnitTestBrowser/1.0",
            accept_language="en-US,en;q=0.9",
        )
        session["qs_last_activity_at"] = timezone.now().isoformat()
        session.save()

        response = self.client.get(
            reverse("profile"),
            HTTP_USER_AGENT="UnitTestBrowser/1.0",
            HTTP_ACCEPT_LANGUAGE="en-GB,en;q=0.8",
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("_auth_user_id", self.client.session)
        self.assertEqual(
            self.client.session["qs_session_fingerprint"],
            self._session_fingerprint(user_agent="UnitTestBrowser/1.0"),
        )
        self.assertEqual(self.client.session["qs_session_fingerprint_version"], 2)

    def test_session_heartbeat_returns_ok_for_active_session(self):
        user = self._make_user("heartbeat-user")
        self.client.force_login(user)

        response = self.client.post(
            reverse("session_heartbeat"),
            HTTP_USER_AGENT="UnitTestBrowser/1.0",
            HTTP_ACCEPT_LANGUAGE="en-US,en;q=0.9",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(
            response.content,
            {"ok": True, "seconds_remaining": 300},
        )


class ReceiptWorkflowTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        post_save.disconnect(create_user_verification, sender=User)
        post_save.disconnect(send_welcome_email, sender=User)

    @classmethod
    def tearDownClass(cls):
        post_save.connect(create_user_verification, sender=User)
        post_save.connect(send_welcome_email, sender=User)
        super().tearDownClass()

    def _make_user(self, username, role="admin", owner=None, default_location=None):
        user = User.objects.create_user(
            username=username,
            password="password123",
            email=f"{username}@example.com",
        )
        profile = UserProfile.for_user(user)
        profile.role = role
        profile.status = "active"
        profile.plan = "PRO"
        profile.pro_expires = timezone.now().date() + timedelta(days=30)
        profile.plan_end = timezone.now() + timedelta(days=30)
        profile.parent_admin = UserProfile.for_user(owner) if owner else None
        profile.default_location = default_location
        profile.save()
        user.is_active = True
        user.save(update_fields=["is_active"])
        return user

    def _make_sale_fixture(self):
        owner = self._make_user("receipt-owner", role="admin")
        location = Location.objects.create(owner=owner, name="Main Branch")
        cashier = self._make_user("receipt-cashier", role="cashier", owner=owner, default_location=location)
        manager = self._make_user("receipt-manager", role="manager", owner=owner, default_location=location)
        category = Category.objects.create(owner=owner, name="Beverages")
        brand = Brand.objects.create(owner=owner, name="QuickStock")
        item = Item.objects.create(
            owner=owner,
            name="Sparkling Water",
            sku="WATER-1",
            category=category,
            brand=brand,
            cost_price=Decimal("100.00"),
            price=Decimal("230.00"),
            is_taxable=True,
        )
        StockRecord.objects.create(item=item, location=location, quantity=20)
        sale = Sale(
            owner=owner,
            cashier=cashier,
            location=location,
            receipt_no=1201,
            receipt_status="finalized",
            amount_paid=Decimal("500.00"),
            payment_reference="CASH-1201",
        )
        sale.save(skip_validation=True)
        SaleItem.objects.create(
            sale=sale,
            item=item,
            quantity=2,
            unit_price=Decimal("230.00"),
            unit_cost=Decimal("100.00"),
            total_price=Decimal("460.00"),
        )
        sale.recalculate_totals(save=True)
        sale.amount_paid = Decimal("500.00")
        sale.change_due = Decimal("40.00")
        sale.save(update_fields=["amount_paid", "change_due"], skip_validation=True)
        return owner, manager, cashier, sale

    def test_receipt_detail_page_renders_actions(self):
        owner, _manager, cashier, sale = self._make_sale_fixture()
        self.client.force_login(cashier)

        response = self.client.get(reverse("view_receipt", args=[sale.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Download PDF")
        self.assertContains(response, "Email Receipt")
        self.assertContains(response, f"Receipt #{sale.receipt_no}")
        self.assertContains(response, 'class="receipt-side-column receipt-proof-column"')
        self.assertContains(response, 'class="receipt-management-grid no-print"')

    def test_manager_edit_creates_revision_history(self):
        owner, manager, _cashier, sale = self._make_sale_fixture()
        self.client.force_login(manager)

        response = self.client.post(
            reverse("receipt_edit", args=[sale.id]),
            {
                "customer_name": "Alice Brown",
                "customer_email": "alice@example.com",
                "customer_phone": "876-555-0101",
                "payment_reference": "REF-991",
                "discount": "25.00",
                "amount_paid": "500.00",
                "notes": "Corrected customer details.",
                "edit_reason": "Customer requested email and final discount correction.",
            },
        )

        self.assertEqual(response.status_code, 302)
        sale.refresh_from_db()
        self.assertEqual(sale.receipt_status, "revised")
        self.assertEqual(sale.revision_number, 2)
        self.assertEqual(sale.customer_name, "Alice Brown")
        self.assertTrue(ReceiptRevision.objects.filter(sale=sale, revision_number=2).exists())

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="receipts@quickstockja.com",
    )
    def test_cashier_can_email_receipt_with_pdf_attachment(self):
        owner, _manager, cashier, sale = self._make_sale_fixture()
        self.client.force_login(cashier)

        response = self.client.post(
            reverse("receipt_email", args=[sale.id]),
            {"recipient_email": "customer@example.com"},
        )

        self.assertEqual(response.status_code, 302)
        sale.refresh_from_db()
        self.assertEqual(sale.receipt_status, "emailed")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["customer@example.com"])
        self.assertEqual(mail.outbox[0].attachments[0][0], f"receipt-{sale.receipt_no}.pdf")
        self.assertTrue(ReceiptEmailLog.objects.filter(sale=sale, status="sent").exists())

    def test_admin_can_void_receipt_with_reason(self):
        owner, _manager, _cashier, sale = self._make_sale_fixture()
        self.client.force_login(owner)

        response = self.client.post(
            reverse("receipt_void", args=[sale.id]),
            {"void_reason": "Customer checkout duplicated in error."},
        )

        self.assertEqual(response.status_code, 302)
        sale.refresh_from_db()
        self.assertEqual(sale.receipt_status, "voided")
        self.assertEqual(sale.void_reason, "Customer checkout duplicated in error.")


class CS006ProductionReadinessTests(TestCase):
    """
    CS-006 Production Readiness Comprehensive Test Suite
    Validates end-to-end accounting, tenant isolation, offline sync idempotency,
    supplier ledger lifecycles, and role permission boundary checks.
    """

    def setUp(self):
        self.owner_a = User.objects.create_user("owner_a", "owner_a@example.com", "pass123")
        self.owner_a.profile.role = "admin"
        self.owner_a.profile.status = "active"
        self.owner_a.profile.plan = "PRO"
        self.owner_a.profile.pro_expires = timezone.localdate() + timedelta(days=30)
        self.location_a = Location.objects.create(name="Warehouse Alpha", owner=self.owner_a, inventory_capacity=10000)
        self.owner_a.profile.default_location = self.location_a
        self.owner_a.profile.save()

        self.owner_b = User.objects.create_user("owner_b", "owner_b@example.com", "pass123")
        self.owner_b.profile.role = "admin"
        self.owner_b.profile.status = "active"
        self.owner_b.profile.plan = "PRO"
        self.owner_b.profile.pro_expires = timezone.localdate() + timedelta(days=30)
        self.location_b = Location.objects.create(name="Store Beta", owner=self.owner_b, inventory_capacity=5000)
        self.owner_b.profile.default_location = self.location_b
        self.owner_b.profile.save()

        self.brand_a = Brand.objects.create(owner=self.owner_a, name="Blue Mountain")
        self.category_a = Category.objects.create(owner=self.owner_a, name="Beverages")
        self.item_a = Item.objects.create(
            name="Jamaican Coffee Premium",
            sku="CS006-COFFEE",
            price=Decimal("450.00"),
            cost_price=Decimal("250.00"),
            brand=self.brand_a,
            category=self.category_a,
            owner=self.owner_a,
        )
        StockRecord.objects.create(item=self.item_a, location=self.location_a, quantity=100)

        self.customer_a = Customer.objects.create(
            name="Grace Imports Ltd",
            email="grace@example.com",
            phone="876-555-0199",
            owner=self.owner_a,
        )

        self.supplier_a = Supplier.objects.create(
            name="Blue Mountain Bean Farmers",
            email="farmers@example.com",
            phone="876-555-0200",
            owner=self.owner_a,
        )

    def test_cs006_accounting_multi_location_stock_and_payment_reconciliation(self):
        """Validates stock transfer, sales invoice issuance, payment application, and inventory balance."""
        self.client.force_login(self.owner_a)
        loc_b = Location.objects.create(name="Retail Counter Alpha", owner=self.owner_a, inventory_capacity=5000)

        # 1. Transfer stock from Warehouse to Retail Counter
        transfer_resp = self.client.post(
            reverse("transfer_stock"),
            {
                "item_id": self.item_a.id,
                "from_location": self.location_a.id,
                "to_location": loc_b.id,
                "quantity": 30,
            },
        )
        self.assertEqual(transfer_resp.status_code, 302)
        stock_wh = StockRecord.objects.get(item=self.item_a, location=self.location_a)
        stock_rc = StockRecord.objects.get(item=self.item_a, location=loc_b)
        self.assertEqual(stock_wh.quantity, 70)
        self.assertEqual(stock_rc.quantity, 30)

        # 2. Issue Invoice for 10 units at Retail Counter
        invoice = SalesInvoice.objects.create(
            owner=self.owner_a,
            created_by=self.owner_a,
            customer=self.customer_a,
            location=loc_b,
            subtotal=Decimal("4500.00"),
            total_amount=Decimal("4500.00"),
            status="issued",
        )
        SalesInvoiceItem.objects.create(
            invoice=invoice,
            item=self.item_a,
            quantity=10,
            unit_price=Decimal("450.00"),
            line_total=Decimal("4500.00"),
        )

        # 3. Apply full payment
        pay_resp = self.client.post(
            reverse("sales_invoice_payment", args=[invoice.id]),
            {
                "amount": "4500.00",
                "payment_method": "cash",
                "payment_reference": "PAY-CS006-FULL",
            },
        )
        self.assertEqual(pay_resp.status_code, 302)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, "paid")
        self.assertEqual(invoice.balance_due, Decimal("0.00"))

    def test_cs006_tenant_isolation_cross_tenant_access_rejection(self):
        """Verifies strict multi-tenant security: Owner B cannot view or modify Owner A's records."""
        self.client.force_login(self.owner_b)

        # 1. Attempt to view Owner A's customer
        cust_resp = self.client.get(reverse("customer_detail", args=[self.customer_a.id]))
        self.assertIn(cust_resp.status_code, [302, 403, 404])

        # 2. Attempt to edit Owner A's product
        edit_resp = self.client.post(
            reverse("inventory_update", args=[self.item_a.id]),
            {"name": "Hacked Name", "price": "1.00", "quantity": 999},
        )
        self.assertEqual(edit_resp.status_code, 404)
        self.item_a.refresh_from_db()
        self.assertEqual(self.item_a.name, "Jamaican Coffee Premium")

    def test_cs006_offline_sync_idempotency_and_duplicate_prevention(self):
        """Verifies offline sync tokens prevent duplicate sales postings on retry."""
        self.client.force_login(self.owner_a)
        CashShift.objects.create(cashier=self.owner_a, owner=self.owner_a, location=self.location_a, opening_cash=Decimal("100.00"))

        sync_token = "SYNC-CS006-OFFLINE-UUID-9999"
        payload = {
            "offline_client_ref": sync_token,
            "location_id": self.location_a.id,
            "payment_channel": "pos",
            "tender": "cash",
            "amount_paid": "500.00",
            "cart": [
                {"id": self.item_a.id, "quantity": 1, "price": "450.00"}
            ],
        }

        # First submit
        r1 = self.client.post(
            reverse("cash_register"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(r1.status_code, 200)
        data1 = r1.json()
        self.assertTrue(data1["success"])
        sale_id = data1["sale_id"]

        # Duplicate re-submit with same sync_token
        r2 = self.client.post(
            reverse("cash_register"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(r2.status_code, 200)
        data2 = r2.json()
        self.assertTrue(data2["success"])
        self.assertTrue(data2.get("idempotent_replay"))
        self.assertEqual(data2["sale_id"], sale_id)

    def test_cs006_supplier_invoice_credit_note_and_refund_lifecycle(self):
        """Verifies supplier invoice creation, credit note adjustment, and payment recording."""
        self.client.force_login(self.owner_a)

        # Create Supplier Invoice
        sup_inv = SupplierInvoice.objects.create(
            supplier=self.supplier_a,
            location=self.location_a,
            invoice_no="SUP-INV-001",
            date_issued=timezone.now().date(),
            amount=Decimal("1000.00"),
            status="Pending",
        )

        # Post Supplier Payment
        pay_resp = self.client.post(
            reverse("supplier_invoice_payment", args=[sup_inv.id]),
            {"amount": "600.00", "reference": "BANK-TRF-001"},
        )
        self.assertEqual(pay_resp.status_code, 302)
        sup_inv.refresh_from_db()
        self.assertEqual(sup_inv.balance_due, Decimal("400.00"))

    def test_cs006_role_permission_boundary_checks(self):
        """Verifies cashier role cannot access admin settings or update store locations."""
        cashier = User.objects.create_user("cashier_a", "cashier@example.com", "pass123")
        cashier.profile.role = "cashier"
        cashier.profile.parent_admin = self.owner_a.profile
        cashier.profile.default_location = self.location_a
        cashier.profile.save()
        self.client.force_login(cashier)

        # Cashier attempting admin-only location switch
        loc_resp = self.client.post(
            reverse("cash_register"),
            {"action": "update_location", "new_location": self.location_a.id},
        )
        self.assertEqual(loc_resp.status_code, 302)

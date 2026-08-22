import copy
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.authtoken.models import Token

from inventory import _get_tax_rate_for_location
from inventory.models import (
    AuditLog,
    Brand,
    CashShift,
    Category,
    Customer,
    Item,
    Location,
    Payment,
    PurchaseOrder,
    Sale,
    SaleItem,
    StockRecord,
    StockTransfer,
    Supplier,
    UserProfile,
)
from inventory import storage


class SyncPullInventoryTests(TestCase):
    def _make_user(self, username: str) -> User:
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

    def _auth_headers(self, user: User) -> dict:
        token, _ = Token.objects.get_or_create(user=user)
        return {"HTTP_AUTHORIZATION": f"Token {token.key}"}

    def _make_staff(self, username: str, owner: User, location: Location) -> User:
        user = User.objects.create_user(username=username, password="password123")
        profile = UserProfile.for_user(user)
        profile.role = "cashier"
        profile.status = "active"
        profile.parent_admin = UserProfile.for_user(owner)
        profile.default_location = location
        profile.save()
        return user

    def _make_item(self, owner: User, sku: str, quantity: int) -> Item:
        category = Category.objects.create(owner=owner, name=f"Category-{sku}")
        brand = Brand.objects.create(owner=owner, name=f"Brand-{sku}")
        return Item.objects.create(
            owner=owner,
            name=f"Item {sku}",
            sku=sku,
            category=category,
            brand=brand,
            cost_price=Decimal("50.00"),
            price=Decimal("100.00"),
            is_taxable=True,
        )

    def _open_shift(self, user: User, location: Location) -> CashShift:
        return CashShift.objects.create(
            cashier=user,
            location=location,
            opening_cash=Decimal("1000.00"),
        )

    def _desktop_checkout_payload(
        self,
        *,
        operator: User,
        owner: User,
        location: Location,
        shift: CashShift,
        lines: list[tuple[Item, int]],
        reference: str,
        tender: str = "cash",
        discount: Decimal = Decimal("0.00"),
        amount_tendered: Decimal | None = None,
        customer: Customer | None = None,
        account_credit: Decimal = Decimal("0.00"),
    ) -> dict:
        tax_rate = _get_tax_rate_for_location(location).quantize(Decimal("0.0001"))
        net_subtotal = Decimal("0.00")
        tax_total = Decimal("0.00")
        gross_total = Decimal("0.00")
        line_payloads = []
        for item, quantity in lines:
            line_gross = (item.price * quantity).quantize(Decimal("0.01"))
            if item.is_taxable:
                line_net = (line_gross / (Decimal("1.0000") + tax_rate)).quantize(Decimal("0.01"))
                line_tax = (line_gross - line_net).quantize(Decimal("0.01"))
                line_rate = tax_rate
            else:
                line_net = line_gross
                line_tax = Decimal("0.00")
                line_rate = Decimal("0.0000")
            gross_total += line_gross
            net_subtotal += line_net
            tax_total += line_tax
            line_payloads.append(
                {
                    "product_id": item.id,
                    "sku": item.sku,
                    "quantity": quantity,
                    "unit_price": str(item.price.quantize(Decimal("0.01"))),
                    "unit_cost": str(item.cost_price.quantize(Decimal("0.01"))),
                    "is_taxable": item.is_taxable,
                    "tax_rate": str(line_rate),
                    "status": item.status,
                    "is_deleted": item.is_deleted,
                }
            )
        total = (gross_total - discount).quantize(Decimal("0.01"))
        tendered = total if amount_tendered is None else amount_tendered.quantize(Decimal("0.01"))
        funding = (tendered + account_credit).quantize(Decimal("0.01"))
        overage = max(Decimal("0.00"), funding - total).quantize(Decimal("0.01"))
        change_due = overage if tender == "cash" else Decimal("0.00")
        credited_overpayment = overage if tender != "cash" else Decimal("0.00")
        role = "superuser" if operator.is_superuser else UserProfile.for_user(operator).role
        return {
            "contract_version": 1,
            "offline_client_ref": reference,
            "occurred_at": timezone.now().isoformat(),
            "operator": {"id": operator.id, "role": role},
            "tenant": {"id": owner.id},
            "location": {"id": location.id},
            "register": {
                "id": shift.id,
                "location_id": location.id,
                "opened_at": shift.opened_at.isoformat(),
            },
            "customer": {
                "id": customer.id if customer else None,
                "name": customer.name if customer else "",
            },
            "currency": "JMD",
            "payment": {
                "method": tender,
                "amount_tendered": str(tendered),
                "account_credit": str(account_credit.quantize(Decimal("0.01"))),
                "change_due": str(change_due),
                "credited_overpayment": str(credited_overpayment),
            },
            "totals": {
                "subtotal": str(net_subtotal.quantize(Decimal("0.01"))),
                "discount": str(discount.quantize(Decimal("0.01"))),
                "tax": str(tax_total.quantize(Decimal("0.01"))),
                "total": str(total),
            },
            "tax": {
                "label": "GCT" if location.country_code == "JM" else "VAT",
                "rate": str(tax_rate),
                "inclusive": True,
            },
            "line_items": line_payloads,
            "metadata": {"source": "desktop", "queue_schema_version": 1},
        }

    def _assert_checkout_has_no_effects(
        self,
        *,
        owner: User,
        stock_expectations: list[tuple[Item, Location, int]],
        shift: CashShift | None = None,
        customer: Customer | None = None,
        customer_credit: Decimal | None = None,
    ) -> None:
        self.assertFalse(Sale.objects.filter(owner=owner).exists())
        self.assertFalse(SaleItem.objects.filter(sale__owner=owner).exists())
        self.assertFalse(
            AuditLog.objects.filter(user__profile__parent_admin__user=owner, message="Desktop checkout committed").exists()
        )
        self.assertFalse(AuditLog.objects.filter(user=owner, message="Desktop checkout committed").exists())
        self.assertFalse(Payment.objects.filter(user=owner).exists())
        for item, location, expected_quantity in stock_expectations:
            self.assertEqual(
                StockRecord.objects.get(item=item, location=location).quantity,
                expected_quantity,
            )
        if shift:
            shift.refresh_from_db()
            self.assertEqual(shift.total_sales, Decimal("0.00"))
        if customer:
            customer.refresh_from_db()
            self.assertEqual(customer.credit_balance, customer_credit)

    def test_desktop_register_open_rejects_another_tenants_location(self):
        owner = self._make_user("register-owner")
        other_owner = self._make_user("register-other-owner")
        foreign_location = Location.objects.create(owner=other_owner, name="Foreign Store")

        response = self.client.post(
            reverse("desktop_open_register"),
            {"location_id": foreign_location.id, "opening_cash": "100.00"},
            content_type="application/json",
            **self._auth_headers(owner),
        )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(CashShift.objects.filter(cashier=owner).exists())

    def test_desktop_register_open_rejects_cashiers_unassigned_location(self):
        owner = self._make_user("register-location-owner")
        assigned = Location.objects.create(owner=owner, name="Assigned Store")
        unassigned = Location.objects.create(owner=owner, name="Other Store")
        cashier = self._make_staff("register-location-cashier", owner, assigned)

        response = self.client.post(
            reverse("desktop_open_register"),
            {"location_id": unassigned.id, "opening_cash": "100.00"},
            content_type="application/json",
            **self._auth_headers(cashier),
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(CashShift.objects.filter(cashier=cashier).exists())

    def test_desktop_register_close_persists_reconciliation_and_audit(self):
        owner = self._make_user("register-close-owner")
        location = Location.objects.create(owner=owner, name="Main Store")
        shift = self._open_shift(owner, location)

        response = self.client.post(
            reverse("desktop_close_register"),
            {
                "register_id": shift.id,
                "closing_cash": "1000.00",
                "notes": "Desktop close",
            },
            content_type="application/json",
            **self._auth_headers(owner),
        )

        self.assertEqual(response.status_code, 200)
        shift.refresh_from_db()
        self.assertTrue(shift.is_closed)
        self.assertEqual(shift.actual_cash, Decimal("1000.00"))
        self.assertEqual(shift.reconciliation.counted_cash, Decimal("1000.00"))
        self.assertTrue(
            AuditLog.objects.filter(
                user=owner,
                action="register",
                metadata__shift_id=shift.id,
            ).exists()
        )

    def test_desktop_inventory_operations_are_available_online(self):
        owner = self._make_user("desktop-operations-owner")
        source = Location.objects.create(owner=owner, name="Main Store")
        destination = Location.objects.create(owner=owner, name="Warehouse")
        item = self._make_item(owner, sku="OPS-1", quantity=0)
        StockRecord.objects.create(item=item, location=source, quantity=10)
        headers = self._auth_headers(owner)

        category_response = self.client.post(
            reverse("desktop_create_category"),
            {"name": "Online Category"},
            content_type="application/json",
            **headers,
        )
        self.assertEqual(category_response.status_code, 201)

        supplier_response = self.client.post(
            reverse("desktop_create_supplier"),
            {"name": "Online Supplier", "email": "supplier@example.com"},
            content_type="application/json",
            **headers,
        )
        self.assertEqual(supplier_response.status_code, 201)
        supplier = Supplier.objects.get(pk=supplier_response.json()["id"])

        receive_response = self.client.post(
            reverse("desktop_receive_stock"),
            {
                "sku": item.sku,
                "supplier_id": supplier.id,
                "location_id": destination.id,
                "quantity": 3,
                "unit_cost": "42.50",
            },
            content_type="application/json",
            **headers,
        )
        self.assertEqual(receive_response.status_code, 201)
        self.assertEqual(
            StockRecord.objects.get(item=item, location=destination).quantity,
            3,
        )
        self.assertTrue(PurchaseOrder.objects.filter(item=item, supplier=supplier).exists())

        transfer_response = self.client.post(
            reverse("desktop_transfer_stock"),
            {
                "sku": item.sku,
                "from_location_id": source.id,
                "to_location_id": destination.id,
                "quantity": 4,
            },
            content_type="application/json",
            **headers,
        )
        self.assertEqual(transfer_response.status_code, 201)
        self.assertEqual(StockRecord.objects.get(item=item, location=source).quantity, 6)
        self.assertEqual(StockRecord.objects.get(item=item, location=destination).quantity, 7)
        self.assertTrue(StockTransfer.objects.filter(item=item, status="COMPLETED").exists())

        delete_response = self.client.delete(
            reverse("api_inventory_item", args=[item.sku]),
            **headers,
        )
        self.assertEqual(delete_response.status_code, 200)
        item.refresh_from_db()
        self.assertTrue(item.is_deleted)
        self.assertEqual(item.status, "archived")

    def test_inventory_collection_accepts_desktop_post_alias(self):
        owner = self._make_user("inventory-post-owner")
        location = Location.objects.create(owner=owner, name="Main Store")

        response = self.client.post(
            reverse("api_inventory_pull"),
            {
                "sku": "POST-ALIAS-1",
                "name": "Posted inventory item",
                "price": "100.00",
                "cost_price": "50.00",
                "quantity": 8,
                "category": "General",
                "location_id": location.id,
            },
            content_type="application/json",
            **self._auth_headers(owner),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        item = Item.objects.get(owner=owner, sku="POST-ALIAS-1")
        self.assertEqual(StockRecord.objects.get(item=item, location=location).quantity, 8)

    def test_sync_pull_inventory_defaults_missing_stock_records_to_zero(self):
        owner = self._make_user("legacy-owner")
        location = Location.objects.create(owner=owner, name="Main Store")
        item = self._make_item(owner, sku="LEGACY-1", quantity=42)

        response = self.client.get(
            reverse("api_inventory_pull"),
            {"location_id": location.id, "client_id": "desktop-test"},
            **self._auth_headers(owner),
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["items"][0]["sku"], item.sku)
        self.assertEqual(payload["items"][0]["quantity"], 0)

    def test_sync_pull_inventory_prefers_selected_location_stock_quantity(self):
        owner = self._make_user("scoped-owner")
        location = Location.objects.create(owner=owner, name="Main Store")
        other_location = Location.objects.create(owner=owner, name="Warehouse")
        item = self._make_item(owner, sku="SCOPED-1", quantity=99)
        StockRecord.objects.create(item=item, location=location, quantity=7)
        StockRecord.objects.create(item=item, location=other_location, quantity=12)

        response = self.client.get(
            reverse("api_inventory_pull"),
            {"location_id": location.id, "client_id": "desktop-test"},
            **self._auth_headers(owner),
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["items"][0]["sku"], item.sku)
        self.assertEqual(payload["items"][0]["quantity"], 7)

    def test_staff_sync_uses_company_owner_inventory_scope(self):
        owner = self._make_user("staff-sync-owner")
        location = Location.objects.create(owner=owner, name="Main Store")
        staff = self._make_staff("staff-sync-cashier", owner, location)
        item = self._make_item(owner, sku="STAFF-SCOPE-1", quantity=0)
        StockRecord.objects.create(item=item, location=location, quantity=9)

        other_owner = self._make_user("staff-sync-other-owner")
        other_location = Location.objects.create(owner=other_owner, name="Other Store")
        other_item = self._make_item(other_owner, sku="FOREIGN-SCOPE-1", quantity=0)
        StockRecord.objects.create(item=other_item, location=other_location, quantity=20)

        response = self.client.get(
            reverse("api_inventory_pull"),
            {"location_id": location.id, "client_id": "desktop-staff-test"},
            **self._auth_headers(staff),
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["items"][0]["sku"], "STAFF-SCOPE-1")
        self.assertEqual(payload["items"][0]["quantity"], 9)

    def test_staff_api_profile_reports_company_entitlement(self):
        owner = self._make_user("staff-profile-owner")
        location = Location.objects.create(owner=owner, name="Main Store")
        staff = self._make_staff("staff-profile-cashier", owner, location)

        response = self.client.get(reverse("api_profile"), **self._auth_headers(staff))

        self.assertEqual(response.status_code, 200)
        user_payload = response.json()["user"]
        self.assertEqual(user_payload["business_name"], owner.username)
        self.assertEqual(user_payload["license_status"], "active")
        self.assertEqual(user_payload["plan"], "PRO")
        self.assertEqual(user_payload["status"], "active")
        owner_profile = UserProfile.objects.get(user=owner)
        self.assertEqual(user_payload["pro_expiry"], owner_profile.pro_expires.isoformat())

    def test_sync_push_inventory_accepts_single_desktop_item_payload(self):
        owner = self._make_user("desktop-push-owner")
        location = Location.objects.create(owner=owner, name="Main Store")

        response = self.client.post(
            reverse("api_inventory_push"),
            {
                "sku": "PUSH-1",
                "name": "Pushed Item",
                "price": "120.00",
                "cost_price": "70.00",
                "quantity": 11,
                "category": "Desktop Push",
                "brand": "Desktop Brand",
                "location_id": location.id,
                "client_id": "desktop-test",
            },
            content_type="application/json",
            **self._auth_headers(owner),
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        item = Item.objects.get(owner=owner, sku="PUSH-1")
        self.assertEqual(item.quantity, 11)
        self.assertTrue(StockRecord.objects.filter(item=item, location=location, quantity=11).exists())

    def test_sync_push_inventory_rejects_negative_values_atomically(self):
        owner = self._make_user("desktop-negative-inventory")
        location = Location.objects.create(owner=owner, name="Main Store")
        item = self._make_item(owner, sku="NEGATIVE-ITEM", quantity=0)
        StockRecord.objects.create(item=item, location=location, quantity=7)

        response = self.client.post(
            reverse("api_inventory_push"),
            {
                "items": [
                    {
                        "sku": item.sku,
                        "name": item.name,
                        "price": "-100.00",
                        "cost_price": "50.00",
                        "quantity": -4,
                        "category": item.category.name,
                        "location_id": location.id,
                    }
                ]
            },
            content_type="application/json",
            **self._auth_headers(owner),
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["success"])
        item.refresh_from_db()
        self.assertEqual(item.price, Decimal("100.00"))
        self.assertEqual(StockRecord.objects.get(item=item, location=location).quantity, 7)

    def test_sync_push_sales_rejects_negative_quantity_without_partial_sale(self):
        owner = self._make_user("desktop-negative-sale")
        location = Location.objects.create(owner=owner, name="Main Store")
        item = self._make_item(owner, sku="SALE-NEGATIVE", quantity=0)
        StockRecord.objects.create(item=item, location=location, quantity=5)

        response = self.client.post(
            reverse("api_sales_push"),
            {
                "sales": [
                    {
                        "receipt_no": 1001,
                        "tender": "cash",
                        "items": [
                            {"item_sku": item.sku, "quantity": -3, "unit_price": "100.00"}
                        ],
                    }
                ]
            },
            content_type="application/json",
            **self._auth_headers(owner),
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["success"])
        self.assertFalse(Sale.objects.filter(owner=owner, receipt_no=1001).exists())
        self.assertEqual(StockRecord.objects.get(item=item, location=location).quantity, 5)

    def test_sync_push_sales_recomputes_totals_from_validated_lines(self):
        owner = self._make_user("desktop-valid-sale")
        location = Location.objects.create(owner=owner, name="Main Store")
        item = self._make_item(owner, sku="SALE-VALID", quantity=0)
        StockRecord.objects.create(item=item, location=location, quantity=5)

        response = self.client.post(
            reverse("api_sales_push"),
            {
                "sales": [
                    {
                        "receipt_no": 1002,
                        "tender": "cash",
                        "total_price": "1.00",
                        "discount": "10.00",
                        "items": [
                            {"item_sku": item.sku, "quantity": 2, "unit_price": "100.00"}
                        ],
                    }
                ]
            },
            content_type="application/json",
            **self._auth_headers(owner),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"], response.json())
        sale = Sale.objects.get(owner=owner, receipt_no=1002)
        self.assertEqual(sale.total_price, Decimal("190.00"))
        self.assertEqual(sale.items.get().unit_cost, Decimal("50.00"))
        self.assertEqual(StockRecord.objects.get(item=item, location=location).quantity, 3)

    def test_desktop_sales_post_contract_is_idempotent_and_uses_server_prices(self):
        owner = self._make_user("desktop-live-sale")
        location = Location.objects.create(owner=owner, name="Main Store")
        item = self._make_item(owner, sku="DESKTOP-LIVE", quantity=0)
        StockRecord.objects.create(item=item, location=location, quantity=5)
        shift = self._open_shift(owner, location)
        payload = self._desktop_checkout_payload(
            operator=owner,
            owner=owner,
            location=location,
            shift=shift,
            lines=[(item, 2)],
            reference="DESKTOP-IDEMPOTENT-001",
            tender="bank_transfer",
            discount=Decimal("10.00"),
        )

        first_response = self.client.post(
            reverse("api_sales_pull"),
            payload,
            content_type="application/json",
            **self._auth_headers(owner),
        )
        second_response = self.client.post(
            reverse("api_sales_pull"),
            payload,
            content_type="application/json",
            **self._auth_headers(owner),
        )

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 200)
        self.assertFalse(first_response.json()["idempotent_replay"])
        self.assertTrue(second_response.json()["idempotent_replay"])
        self.assertEqual(first_response.json()["sale_id"], second_response.json()["sale_id"])
        self.assertEqual(first_response.json()["receipt_no"], second_response.json()["receipt_no"])
        self.assertEqual(second_response.json()["client_reference"], payload["offline_client_ref"])
        sale = Sale.objects.get(owner=owner, sync_token="DESKTOP-IDEMPOTENT-001")
        self.assertEqual(sale.tender, "bank_transfer")
        self.assertEqual(sale.total_price, Decimal("190.00"))
        self.assertEqual(sale.amount_paid, Decimal("190.00"))
        self.assertEqual(sale.change_due, Decimal("0.00"))
        self.assertEqual(sale.shift, shift)
        self.assertEqual(Sale.objects.filter(owner=owner).count(), 1)
        self.assertEqual(SaleItem.objects.filter(sale=sale).count(), 1)
        self.assertEqual(sale.items.get().quantity, 2)
        self.assertEqual(StockRecord.objects.get(item=item, location=location).quantity, 3)
        self.assertEqual(
            AuditLog.objects.filter(user=owner, message="Desktop checkout committed").count(),
            1,
        )

    def test_desktop_sale_requires_client_reference_before_any_effects(self):
        owner = self._make_user("desktop-missing-reference")
        location = Location.objects.create(owner=owner, name="Main Store")
        item = self._make_item(owner, sku="MISSING-REF", quantity=0)
        StockRecord.objects.create(item=item, location=location, quantity=5)
        shift = self._open_shift(owner, location)
        payload = self._desktop_checkout_payload(
            operator=owner,
            owner=owner,
            location=location,
            shift=shift,
            lines=[(item, 2)],
            reference="DESKTOP-REMOVE-ME",
        )
        payload.pop("offline_client_ref")

        response = self.client.post(
            reverse("api_sales_pull"),
            payload,
            content_type="application/json",
            **self._auth_headers(owner),
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["ok"])
        self.assertEqual(response.json()["code"], "MISSING_CLIENT_REFERENCE")
        self.assertIn("offline_client_ref", response.json()["error"])
        self.assertFalse(Sale.objects.filter(owner=owner).exists())
        self.assertFalse(SaleItem.objects.filter(sale__owner=owner).exists())
        self.assertFalse(AuditLog.objects.filter(user=owner, message="Desktop checkout committed").exists())
        self.assertFalse(Payment.objects.filter(user=owner).exists())
        self.assertEqual(StockRecord.objects.get(item=item, location=location).quantity, 5)

    def test_desktop_sale_reference_is_tenant_scoped(self):
        first_owner = self._make_user("desktop-ref-owner-one")
        first_location = Location.objects.create(owner=first_owner, name="Main Store")
        first_item = self._make_item(first_owner, sku="SHARED-REF-SKU", quantity=0)
        StockRecord.objects.create(item=first_item, location=first_location, quantity=4)
        first_shift = self._open_shift(first_owner, first_location)

        second_owner = self._make_user("desktop-ref-owner-two")
        second_location = Location.objects.create(owner=second_owner, name="Main Store")
        second_item = self._make_item(second_owner, sku="SHARED-REF-SKU", quantity=0)
        StockRecord.objects.create(item=second_item, location=second_location, quantity=6)
        second_shift = self._open_shift(second_owner, second_location)

        reference = "DESKTOP-TENANT-SCOPED-001"
        first_payload = self._desktop_checkout_payload(
            operator=first_owner,
            owner=first_owner,
            location=first_location,
            shift=first_shift,
            lines=[(first_item, 1)],
            reference=reference,
        )
        second_payload = self._desktop_checkout_payload(
            operator=second_owner,
            owner=second_owner,
            location=second_location,
            shift=second_shift,
            lines=[(second_item, 2)],
            reference=reference,
        )
        first_response = self.client.post(
            reverse("api_sales_pull"),
            first_payload,
            content_type="application/json",
            **self._auth_headers(first_owner),
        )
        second_response = self.client.post(
            reverse("api_sales_pull"),
            second_payload,
            content_type="application/json",
            **self._auth_headers(second_owner),
        )

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(Sale.objects.filter(sync_token=reference).count(), 2)
        self.assertEqual(StockRecord.objects.get(item=first_item, location=first_location).quantity, 3)
        self.assertEqual(StockRecord.objects.get(item=second_item, location=second_location).quantity, 4)

    def test_desktop_sale_reference_cannot_be_reused_by_another_cashier(self):
        owner = self._make_user("desktop-ref-cashier-owner")
        location = Location.objects.create(owner=owner, name="Main Store")
        first_cashier = self._make_staff("desktop-first-cashier", owner, location)
        second_cashier = self._make_staff("desktop-second-cashier", owner, location)
        item = self._make_item(owner, sku="CASHIER-REF", quantity=0)
        StockRecord.objects.create(item=item, location=location, quantity=5)
        first_shift = self._open_shift(first_cashier, location)
        second_shift = self._open_shift(second_cashier, location)
        payload = self._desktop_checkout_payload(
            operator=first_cashier,
            owner=owner,
            location=location,
            shift=first_shift,
            lines=[(item, 1)],
            reference="DESKTOP-CASHIER-SCOPED-001",
        )
        conflicting_payload = self._desktop_checkout_payload(
            operator=second_cashier,
            owner=owner,
            location=location,
            shift=second_shift,
            lines=[(item, 1)],
            reference="DESKTOP-CASHIER-SCOPED-001",
        )

        first_response = self.client.post(
            reverse("api_sales_pull"),
            payload,
            content_type="application/json",
            **self._auth_headers(first_cashier),
        )
        conflicting_response = self.client.post(
            reverse("api_sales_pull"),
            conflicting_payload,
            content_type="application/json",
            **self._auth_headers(second_cashier),
        )

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(conflicting_response.status_code, 409)
        self.assertEqual(Sale.objects.filter(owner=owner).count(), 1)
        self.assertEqual(SaleItem.objects.filter(sale__owner=owner).count(), 1)
        self.assertEqual(StockRecord.objects.get(item=item, location=location).quantity, 4)

    def test_desktop_sale_validation_rolls_back_all_financial_and_stock_effects(self):
        owner = self._make_user("desktop-atomic-sale")
        location = Location.objects.create(owner=owner, name="Main Store")
        available_item = self._make_item(owner, sku="ATOMIC-AVAILABLE", quantity=0)
        unavailable_item = self._make_item(owner, sku="ATOMIC-UNAVAILABLE", quantity=0)
        StockRecord.objects.create(item=available_item, location=location, quantity=5)
        StockRecord.objects.create(item=unavailable_item, location=location, quantity=0)
        shift = self._open_shift(owner, location)
        payload = self._desktop_checkout_payload(
            operator=owner,
            owner=owner,
            location=location,
            shift=shift,
            lines=[(available_item, 2), (unavailable_item, 1)],
            reference="DESKTOP-ATOMIC-001",
        )

        response = self.client.post(
            reverse("api_sales_pull"),
            payload,
            content_type="application/json",
            **self._auth_headers(owner),
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Sale.objects.filter(owner=owner).exists())
        self.assertFalse(SaleItem.objects.filter(sale__owner=owner).exists())
        self.assertEqual(StockRecord.objects.get(item=available_item, location=location).quantity, 5)
        self.assertEqual(StockRecord.objects.get(item=unavailable_item, location=location).quantity, 0)

    def test_invalid_checkout_contracts_return_structured_errors_with_zero_effects(self):
        owner = self._make_user("desktop-invalid-contracts")
        location = Location.objects.create(owner=owner, name="Main Store")
        item = self._make_item(owner, sku="INVALID-CONTRACT", quantity=0)
        StockRecord.objects.create(item=item, location=location, quantity=5)
        shift = self._open_shift(owner, location)
        base_payload = self._desktop_checkout_payload(
            operator=owner,
            owner=owner,
            location=location,
            shift=shift,
            lines=[(item, 1)],
            reference="DESKTOP-INVALID-BASE",
        )
        cases = (
            (
                "negative-quantity",
                "INVALID_INTEGER",
                lambda payload: payload["line_items"][0].__setitem__("quantity", -1),
            ),
            (
                "invalid-total",
                "INVALID_TOTAL",
                lambda payload: payload["totals"].__setitem__("total", "99.00"),
            ),
            (
                "invalid-tax",
                "INVALID_TAX",
                lambda payload: payload["tax"].__setitem__("rate", "0.1000"),
            ),
            (
                "invalid-precision",
                "INVALID_PRECISION",
                lambda payload: payload["payment"].__setitem__("amount_tendered", "100.001"),
            ),
            (
                "malformed-line",
                "INVALID_PAYLOAD",
                lambda payload: payload["line_items"].__setitem__(0, "not-an-object"),
            ),
        )

        for index, (label, expected_code, mutate) in enumerate(cases, start=1):
            with self.subTest(label=label):
                payload = copy.deepcopy(base_payload)
                payload["offline_client_ref"] = f"DESKTOP-INVALID-{index}"
                mutate(payload)

                response = self.client.post(
                    reverse("api_sales_pull"),
                    payload,
                    content_type="application/json",
                    **self._auth_headers(owner),
                )

                self.assertEqual(response.status_code, 400)
                body = response.json()
                self.assertEqual(body["code"], expected_code)
                self.assertFalse(body["ok"])
                self.assertFalse(body["success"])
                self.assertTrue(body["field"])
                self.assertTrue(body["message"])
                self.assertEqual(body["error"], body["message"])
                self._assert_checkout_has_no_effects(
                    owner=owner,
                    stock_expectations=[(item, location, 5)],
                    shift=shift,
                )

    def test_malformed_checkout_root_has_zero_database_effects(self):
        owner = self._make_user("desktop-malformed-root")
        location = Location.objects.create(owner=owner, name="Main Store")
        item = self._make_item(owner, sku="MALFORMED-ROOT", quantity=0)
        StockRecord.objects.create(item=item, location=location, quantity=5)
        shift = self._open_shift(owner, location)

        response = self.client.post(
            reverse("api_sales_pull"),
            ["not", "a", "checkout"],
            content_type="application/json",
            **self._auth_headers(owner),
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "INVALID_PAYLOAD")
        self._assert_checkout_has_no_effects(
            owner=owner,
            stock_expectations=[(item, location, 5)],
            shift=shift,
        )

    def test_customer_credit_validation_rejects_missing_and_foreign_customers_without_effects(self):
        owner = self._make_user("desktop-credit-owner")
        location = Location.objects.create(owner=owner, name="Main Store")
        item = self._make_item(owner, sku="CREDIT-VALIDATION", quantity=0)
        StockRecord.objects.create(item=item, location=location, quantity=5)
        shift = self._open_shift(owner, location)
        payload = self._desktop_checkout_payload(
            operator=owner,
            owner=owner,
            location=location,
            shift=shift,
            lines=[(item, 1)],
            reference="DESKTOP-CREDIT-NO-CUSTOMER",
            amount_tendered=Decimal("90.00"),
            account_credit=Decimal("10.00"),
        )

        missing_response = self.client.post(
            reverse("api_sales_pull"),
            payload,
            content_type="application/json",
            **self._auth_headers(owner),
        )

        self.assertEqual(missing_response.status_code, 400)
        self.assertEqual(missing_response.json()["code"], "CUSTOMER_REQUIRED")
        self._assert_checkout_has_no_effects(
            owner=owner,
            stock_expectations=[(item, location, 5)],
            shift=shift,
        )

        foreign_owner = self._make_user("desktop-credit-foreign-owner")
        foreign_customer = Customer.objects.create(
            owner=foreign_owner,
            name="Foreign Customer",
            credit_balance=Decimal("50.00"),
        )
        foreign_payload = copy.deepcopy(payload)
        foreign_payload["offline_client_ref"] = "DESKTOP-CREDIT-FOREIGN-CUSTOMER"
        foreign_payload["customer"] = {
            "id": foreign_customer.id,
            "name": foreign_customer.name,
        }
        foreign_response = self.client.post(
            reverse("api_sales_pull"),
            foreign_payload,
            content_type="application/json",
            **self._auth_headers(owner),
        )

        self.assertEqual(foreign_response.status_code, 404)
        self.assertEqual(foreign_response.json()["code"], "INVALID_CUSTOMER")
        self._assert_checkout_has_no_effects(
            owner=owner,
            stock_expectations=[(item, location, 5)],
            shift=shift,
        )
        foreign_customer.refresh_from_db()
        self.assertEqual(foreign_customer.credit_balance, Decimal("50.00"))

    def test_tenant_location_and_operator_authorization_fail_before_commit(self):
        owner = self._make_user("desktop-authorization-owner")
        location = Location.objects.create(owner=owner, name="Main Store")
        other_location = Location.objects.create(owner=owner, name="Other Store")
        item = self._make_item(owner, sku="AUTHORIZATION-ITEM", quantity=0)
        StockRecord.objects.create(item=item, location=location, quantity=5)
        StockRecord.objects.create(item=item, location=other_location, quantity=5)
        owner_shift = self._open_shift(owner, location)
        payload = self._desktop_checkout_payload(
            operator=owner,
            owner=owner,
            location=location,
            shift=owner_shift,
            lines=[(item, 1)],
            reference="DESKTOP-WRONG-TENANT",
        )
        other_owner = self._make_user("desktop-authorization-other-owner")
        payload["tenant"]["id"] = other_owner.id

        tenant_response = self.client.post(
            reverse("api_sales_pull"),
            payload,
            content_type="application/json",
            **self._auth_headers(owner),
        )
        self.assertEqual(tenant_response.status_code, 403)
        self.assertEqual(tenant_response.json()["code"], "TENANT_MISMATCH")

        cashier = self._make_staff("desktop-location-cashier", owner, location)
        forbidden_shift = self._open_shift(cashier, other_location)
        forbidden_payload = self._desktop_checkout_payload(
            operator=cashier,
            owner=owner,
            location=other_location,
            shift=forbidden_shift,
            lines=[(item, 1)],
            reference="DESKTOP-FORBIDDEN-LOCATION",
        )
        location_response = self.client.post(
            reverse("api_sales_pull"),
            forbidden_payload,
            content_type="application/json",
            **self._auth_headers(cashier),
        )
        self.assertEqual(location_response.status_code, 403)
        self.assertEqual(location_response.json()["code"], "LOCATION_FORBIDDEN")

        unauthorized = self._make_staff("desktop-unauthorized-operator", owner, location)
        unauthorized_profile = UserProfile.for_user(unauthorized)
        unauthorized_profile.role = "inventory_clerk"
        unauthorized_profile.save(update_fields=["role"])
        unauthorized_shift = self._open_shift(unauthorized, location)
        unauthorized_payload = self._desktop_checkout_payload(
            operator=unauthorized,
            owner=owner,
            location=location,
            shift=unauthorized_shift,
            lines=[(item, 1)],
            reference="DESKTOP-UNAUTHORIZED-OPERATOR",
        )
        operator_response = self.client.post(
            reverse("api_sales_pull"),
            unauthorized_payload,
            content_type="application/json",
            **self._auth_headers(unauthorized),
        )
        self.assertEqual(operator_response.status_code, 403)
        self.assertEqual(operator_response.json()["code"], "OPERATOR_UNAUTHORIZED")

        self._assert_checkout_has_no_effects(
            owner=owner,
            stock_expectations=[
                (item, location, 5),
                (item, other_location, 5),
            ],
            shift=owner_shift,
        )
        forbidden_shift.refresh_from_db()
        unauthorized_shift.refresh_from_db()
        self.assertEqual(forbidden_shift.total_sales, Decimal("0.00"))
        self.assertEqual(unauthorized_shift.total_sales, Decimal("0.00"))

    def test_closed_register_and_archived_item_have_zero_effects(self):
        owner = self._make_user("desktop-state-validation")
        location = Location.objects.create(owner=owner, name="Main Store")
        item = self._make_item(owner, sku="STATE-VALIDATION", quantity=0)
        StockRecord.objects.create(item=item, location=location, quantity=5)
        shift = self._open_shift(owner, location)
        shift.end_time = timezone.now() - timedelta(minutes=1)
        shift.is_closed = True
        shift.save(update_fields=["end_time", "is_closed"])
        closed_payload = self._desktop_checkout_payload(
            operator=owner,
            owner=owner,
            location=location,
            shift=shift,
            lines=[(item, 1)],
            reference="DESKTOP-CLOSED-REGISTER",
        )

        closed_response = self.client.post(
            reverse("api_sales_pull"),
            closed_payload,
            content_type="application/json",
            **self._auth_headers(owner),
        )
        self.assertEqual(closed_response.status_code, 409)
        self.assertEqual(closed_response.json()["code"], "REGISTER_CLOSED")

        open_shift = self._open_shift(owner, location)
        archived_payload = self._desktop_checkout_payload(
            operator=owner,
            owner=owner,
            location=location,
            shift=open_shift,
            lines=[(item, 1)],
            reference="DESKTOP-ARCHIVED-ITEM",
        )
        Item.objects.filter(pk=item.pk).update(status="archived")
        archived_response = self.client.post(
            reverse("api_sales_pull"),
            archived_payload,
            content_type="application/json",
            **self._auth_headers(owner),
        )

        self.assertEqual(archived_response.status_code, 400)
        self.assertEqual(archived_response.json()["code"], "ITEM_UNAVAILABLE")
        self._assert_checkout_has_no_effects(
            owner=owner,
            stock_expectations=[(item, location, 5)],
            shift=open_shift,
        )

    def test_post_write_audit_failure_rolls_back_sale_stock_shift_customer_and_receipt(self):
        owner = self._make_user("desktop-post-write-rollback")
        location = Location.objects.create(owner=owner, name="Main Store")
        item = self._make_item(owner, sku="POST-WRITE-ROLLBACK", quantity=0)
        StockRecord.objects.create(item=item, location=location, quantity=5)
        shift = self._open_shift(owner, location)
        customer = Customer.objects.create(
            owner=owner,
            name="Rollback Customer",
            credit_balance=Decimal("50.00"),
        )
        payload = self._desktop_checkout_payload(
            operator=owner,
            owner=owner,
            location=location,
            shift=shift,
            lines=[(item, 1)],
            reference="DESKTOP-POST-WRITE-ROLLBACK",
            tender="card",
            amount_tendered=Decimal("90.00"),
            customer=customer,
            account_credit=Decimal("10.00"),
        )

        with patch(
            "api.views.AuditLog.objects.create",
            side_effect=ValidationError("Audit persistence failed."),
        ):
            response = self.client.post(
                reverse("api_sales_pull"),
                payload,
                content_type="application/json",
                **self._auth_headers(owner),
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "SALE_VALIDATION_FAILED")
        self._assert_checkout_has_no_effects(
            owner=owner,
            stock_expectations=[(item, location, 5)],
            shift=shift,
            customer=customer,
            customer_credit=Decimal("50.00"),
        )

    def test_customer_credit_and_payment_effects_commit_only_once_on_replay(self):
        owner = self._make_user("desktop-credit-commit")
        location = Location.objects.create(owner=owner, name="Main Store")
        item = self._make_item(owner, sku="CREDIT-COMMIT", quantity=0)
        StockRecord.objects.create(item=item, location=location, quantity=5)
        shift = self._open_shift(owner, location)
        customer = Customer.objects.create(
            owner=owner,
            name="Credit Commit Customer",
            credit_balance=Decimal("50.00"),
        )
        payload = self._desktop_checkout_payload(
            operator=owner,
            owner=owner,
            location=location,
            shift=shift,
            lines=[(item, 1)],
            reference="DESKTOP-CREDIT-COMMIT",
            tender="card",
            amount_tendered=Decimal("90.00"),
            customer=customer,
            account_credit=Decimal("10.00"),
        )

        first_response = self.client.post(
            reverse("api_sales_pull"),
            payload,
            content_type="application/json",
            **self._auth_headers(owner),
        )
        replay_response = self.client.post(
            reverse("api_sales_pull"),
            payload,
            content_type="application/json",
            **self._auth_headers(owner),
        )

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(replay_response.status_code, 200)
        self.assertTrue(replay_response.json()["idempotent_replay"])
        sale = Sale.objects.get(owner=owner, sync_token="DESKTOP-CREDIT-COMMIT")
        self.assertEqual(sale.amount_paid, Decimal("100.00"))
        self.assertEqual(sale.change_due, Decimal("0.00"))
        self.assertEqual(Sale.objects.filter(owner=owner).count(), 1)
        self.assertEqual(SaleItem.objects.filter(sale=sale).count(), 1)
        self.assertEqual(StockRecord.objects.get(item=item, location=location).quantity, 4)
        customer.refresh_from_db()
        shift.refresh_from_db()
        self.assertEqual(customer.credit_balance, Decimal("40.00"))
        self.assertEqual(shift.total_sales, Decimal("100.00"))
        self.assertEqual(
            AuditLog.objects.filter(user=owner, message="Desktop checkout committed").count(),
            1,
        )

    def test_sales_pull_includes_server_sales_not_previously_marked_synced(self):
        owner = self._make_user("desktop-pull-web-sale")
        location = Location.objects.create(owner=owner, name="Main Store")
        sale = Sale.objects.create(
            owner=owner,
            cashier=owner,
            location=location,
            total_price=Decimal("25.00"),
            is_synced=False,
        )

        response = self.client.get(
            reverse("api_sales_pull"),
            {"client_id": "desktop-pull-test"},
            **self._auth_headers(owner),
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(sale.id, [row["id"] for row in response.json()["sales"]])

    @override_settings(API_SYNC_MAX_INVENTORY_ITEMS=1)
    def test_sync_push_inventory_rejects_oversized_batch(self):
        owner = self._make_user("desktop-large-batch")

        response = self.client.post(
            reverse("api_inventory_push"),
            {"items": [{"sku": "ONE"}, {"sku": "TWO"}]},
            content_type="application/json",
            **self._auth_headers(owner),
        )

        self.assertEqual(response.status_code, 413)
        self.assertFalse(Item.objects.filter(owner=owner).exists())

    def test_sync_pull_inventory_does_not_seed_from_unowned_shared_json(self):
        owner = self._make_user("isolated-owner")
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temp_dir:
            shared_file = f"{temp_dir}/inventory_data.json"
            with open(shared_file, "w", encoding="utf-8") as handle:
                handle.write(
                    """
                    {
                      "products": [
                        {"name": "Foreign Seed", "sku": "FOREIGN-1", "quantity_on_hand": 4}
                      ],
                      "settings": {
                        "owner_username": "different-owner"
                      }
                    }
                    """
                )

            original_file = storage.DATA_FILE
            storage.DATA_FILE = shared_file
            try:
                response = self.client.get(
                    reverse("api_inventory_pull"),
                    {"client_id": "desktop-test"},
                    **self._auth_headers(owner),
                )
            finally:
                storage.DATA_FILE = original_file

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["count"], 0)
        self.assertFalse(Item.objects.filter(owner=owner).exists())


class UserRegistryApiTests(TestCase):
    def _make_admin(self, username: str, email: str = "") -> User:
        user = User.objects.create_user(username=username, email=email, password="password123")
        profile = UserProfile.for_user(user)
        profile.role = "admin"
        profile.status = "active"
        profile.plan = "PRO"
        profile.pro_expires = timezone.now().date() + timedelta(days=30)
        profile.plan_end = timezone.now() + timedelta(days=30)
        profile.parent_admin = None
        profile.save()
        return user

    def _make_staff(self, username: str, owner: User, role: str = "cashier", location=None) -> User:
        user = User.objects.create_user(username=username, password="password123")
        profile = UserProfile.for_user(user)
        profile.role = role
        profile.status = "active"
        profile.plan = "PRO"
        profile.parent_admin = UserProfile.for_user(owner)
        profile.default_location = location
        profile.save()
        return user

    def _auth_headers(self, user: User) -> dict:
        token, _ = Token.objects.get_or_create(user=user)
        return {"HTTP_AUTHORIZATION": f"Token {token.key}"}

    def test_login_reports_superuser_role_for_desktop(self):
        User.objects.create_superuser(
            username="platform-owner",
            password="password123",
        )

        response = self.client.post(
            reverse("api_login"),
            {"username": "platform-owner", "password": "password123"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["user"]["role"], "superuser")

    def test_login_returns_plan_metadata_for_offline_enforcement(self):
        owner = self._make_admin("plan-owner")

        response = self.client.post(
            reverse("api_login"),
            {"username": owner.username, "password": "password123"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["user"]["plan"], "PRO")
        self.assertEqual(payload["user"]["status"], "active")
        self.assertIsNotNone(payload["user"]["plan_end"])
        self.assertIsNotNone(payload["user"]["pro_expires"])

    def test_login_rejects_expired_subscription_for_desktop(self):
        owner = self._make_admin("expired-owner")
        profile = UserProfile.for_user(owner)
        profile.pro_expires = timezone.now().date() - timedelta(days=1)
        profile.plan_end = timezone.now() - timedelta(days=1)
        profile.save(update_fields=["pro_expires", "plan_end"])

        response = self.client.post(
            reverse("api_login"),
            {"username": owner.username, "password": "password123"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertIn("expired", payload["message"].lower())

    def test_login_rotates_existing_api_token(self):
        owner = self._make_admin("token-owner")
        old_token = Token.objects.create(user=owner)

        response = self.client.post(
            reverse("api_login"),
            {"username": owner.username, "password": "password123"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertNotEqual(payload["token"], old_token.key)
        self.assertFalse(Token.objects.filter(key=old_token.key).exists())

    @override_settings(API_LOGIN_RATE_LIMIT_ATTEMPTS=2, API_LOGIN_RATE_LIMIT_WINDOW=60)
    def test_login_rate_limits_repeated_failures_per_client_and_username(self):
        owner = self._make_admin("rate-limited-owner")
        cache.clear()

        for _ in range(2):
            response = self.client.post(
                reverse("api_login"),
                {"username": owner.username, "password": "incorrect"},
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 401)

        blocked = self.client.post(
            reverse("api_login"),
            {"username": owner.username, "password": "password123"},
            content_type="application/json",
        )

        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(blocked["Retry-After"], "60")
        self.assertEqual(blocked.json()["error"], "Too Many Attempts")

    @override_settings(API_LOGIN_RATE_LIMIT_ATTEMPTS=2, API_LOGIN_RATE_LIMIT_WINDOW=60)
    def test_successful_login_clears_prior_failure_count(self):
        owner = self._make_admin("rate-reset-owner")
        cache.clear()
        failed = self.client.post(
            reverse("api_login"),
            {"username": owner.username, "password": "incorrect"},
            content_type="application/json",
        )
        self.assertEqual(failed.status_code, 401)

        successful = self.client.post(
            reverse("api_login"),
            {"username": owner.username, "password": "password123"},
            content_type="application/json",
        )
        self.assertEqual(successful.status_code, 200)

        next_failure = self.client.post(
            reverse("api_login"),
            {"username": owner.username, "password": "incorrect"},
            content_type="application/json",
        )
        self.assertEqual(next_failure.status_code, 401)

    def test_expired_subscription_token_is_blocked_after_login(self):
        owner = self._make_admin("blocked-token-owner")
        token, _ = Token.objects.get_or_create(user=owner)
        profile = UserProfile.for_user(owner)
        profile.pro_expires = timezone.now().date() - timedelta(days=1)
        profile.plan_end = timezone.now() - timedelta(days=1)
        profile.save(update_fields=["pro_expires", "plan_end"])

        response = self.client.get(
            reverse("api_users"),
            **{"HTTP_AUTHORIZATION": f"Token {token.key}"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertIn("expired", response.json()["detail"].lower())

    def test_superuser_can_read_platform_user_registry(self):
        superuser = User.objects.create_superuser(
            username="platform-root",
            password="password123",
        )
        owner = self._make_admin("tenant-owner")
        location = Location.objects.create(owner=owner, name="Main Store")
        self._make_staff("tenant-cashier", owner, location=location)

        response = self.client.get(reverse("api_users"), **self._auth_headers(superuser))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["scope"], "platform")
        self.assertEqual(payload["count"], 2)
        usernames = {row["username"] for row in payload["users"]}
        self.assertEqual(usernames, {"tenant-owner", "tenant-cashier"})
        staff_row = next(row for row in payload["users"] if row["username"] == "tenant-cashier")
        self.assertEqual(staff_row["company"], "tenant-owner")
        self.assertEqual(staff_row["default_location"], "Main Store")

    def test_admin_user_registry_is_limited_to_own_company(self):
        owner = self._make_admin("owner-a")
        other_owner = self._make_admin("owner-b")
        self._make_staff("owner-a-cashier", owner)
        self._make_staff("owner-b-cashier", other_owner)

        response = self.client.get(reverse("api_users"), **self._auth_headers(owner))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["scope"], "company")
        usernames = {row["username"] for row in payload["users"]}
        self.assertEqual(usernames, {"owner-a", "owner-a-cashier"})

    def test_non_admin_user_registry_is_forbidden(self):
        owner = self._make_admin("registry-owner")
        manager = self._make_staff("registry-manager", owner, role="manager")

        response = self.client.get(reverse("api_users"), **self._auth_headers(manager))

        self.assertEqual(response.status_code, 403)
        self.assertFalse(response.json()["ok"])

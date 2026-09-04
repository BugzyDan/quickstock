import json
import os
from getpass import getpass

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from inventory.models import (
    CustomerCreditMovement,
    Item,
    Location,
    PurchaseOrder,
    Sale,
    SalesInvoice,
    SalesInvoiceCreditNote,
    SalesInvoicePayment,
    Supplier,
    SupplierInvoice,
    SupplierInvoiceAdjustment,
    SupplierInvoicePayment,
    SupplierInvoiceRefund,
    UserProfile,
)


PURGE_CONFIRMATION_ENV = "QUICKSTOCK_ALLOW_USER_PURGE"
PURGE_CONFIRMATION_VALUE = "I_UNDERSTAND_THIS_DELETES_USERS"


def database_label():
    database = connection.settings_dict
    return {
        "engine": database.get("ENGINE", ""),
        "name": str(database.get("NAME", "")),
        "host": database.get("HOST", ""),
        "port": database.get("PORT", ""),
    }


def financial_history_counts():
    return {
        "sales": Sale.objects.count(),
        "sales_invoices": SalesInvoice.objects.count(),
        "sales_payments": SalesInvoicePayment.objects.count(),
        "sales_credit_notes": SalesInvoiceCreditNote.objects.count(),
        "customer_credit_movements": CustomerCreditMovement.objects.count(),
        "supplier_invoices": SupplierInvoice.objects.count(),
        "supplier_payments": SupplierInvoicePayment.objects.count(),
        "supplier_adjustments": SupplierInvoiceAdjustment.objects.count(),
        "supplier_refunds": SupplierInvoiceRefund.objects.count(),
        "purchase_orders": PurchaseOrder.objects.count(),
    }


class Command(BaseCommand):
    help = "Destructively purge users from a local/development database."

    def add_arguments(self, parser):
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Skip the yes/no prompt. Database name confirmation is still required.",
        )
        parser.add_argument(
            "--database-name",
            default="",
            help="Exact database name/path required for non-interactive confirmation.",
        )
        parser.add_argument(
            "--create-admin",
            action="store_true",
            help="Prompt for a replacement admin account after purging users.",
        )

    def handle(self, *args, **options):
        self._refuse_unsafe_environment()
        User = get_user_model()

        self.stdout.write("=" * 60)
        self.stdout.write("QUICKSTOCK USER PURGE UTILITY")
        self.stdout.write("=" * 60)

        user_count = User.objects.count()
        self.stdout.write(json.dumps({
            "users": user_count,
            "user_profiles": UserProfile.objects.count(),
            "locations": Location.objects.count(),
            "suppliers": Supplier.objects.count(),
            "inventory_items": Item.objects.count(),
        }, indent=2, default=str))

        if user_count == 0:
            self.stdout.write(self.style.SUCCESS("No users found in database. Nothing to purge."))
            return

        self._confirm_database_target(options["database_name"])
        if not options["yes"]:
            response = input("Type yes to permanently delete all users: ").strip().lower()
            if response not in {"yes", "y"}:
                self.stdout.write("Operation cancelled.")
                return

        history = financial_history_counts()
        if any(history.values()):
            self.stdout.write(json.dumps({
                "ok": False,
                "code": "financial_history_protected",
                "message": "User purge blocked; archive accounts so financial history survives.",
                "history": history,
            }, indent=2, default=str))
            return

        with transaction.atomic():
            users_deleted = User.objects.all().delete()
            orphaned_locations = Location.objects.filter(owner__isnull=True).delete()
            orphaned_suppliers = Supplier.objects.filter(owner__isnull=True).delete()
            orphaned_items = Item.objects.filter(owner__isnull=True).delete()

        self.stdout.write(self.style.SUCCESS("User purge completed."))
        self.stdout.write(json.dumps({
            "deleted_users": users_deleted[0],
            "deleted_orphaned_locations": orphaned_locations[0],
            "deleted_orphaned_suppliers": orphaned_suppliers[0],
            "deleted_orphaned_items": orphaned_items[0],
        }, indent=2, default=str))

        if options["create_admin"]:
            self._create_admin()

    def _refuse_unsafe_environment(self):
        allow_purge = os.getenv(PURGE_CONFIRMATION_ENV, "")
        running_on_render = bool(os.getenv("RENDER"))
        if settings.DEBUG and not running_on_render:
            return
        if allow_purge == PURGE_CONFIRMATION_VALUE:
            return

        raise CommandError(json.dumps({
            "ok": False,
            "code": "unsafe_environment",
            "message": (
                "Refusing to purge users outside local DEBUG mode. Set "
                f"{PURGE_CONFIRMATION_ENV}={PURGE_CONFIRMATION_VALUE} only for an intentional maintenance run."
            ),
            "debug": settings.DEBUG,
            "render": running_on_render,
            "database": database_label(),
        }, indent=2, default=str))

    def _confirm_database_target(self, provided_database_name):
        database = database_label()
        database_name = database["name"] or "unknown"
        self.stdout.write("Database target:")
        self.stdout.write(json.dumps(database, indent=2, default=str))

        typed_database = provided_database_name.strip()
        if not typed_database:
            typed_database = input(f"Type the exact database name/path to continue ({database_name}): ").strip()
        if typed_database != database_name:
            raise CommandError("Database confirmation did not match. Operation cancelled.")

    def _create_admin(self):
        User = get_user_model()
        username = input("Admin username: ").strip()
        email = input("Admin email (optional): ").strip()
        password = getpass("Admin password: ").strip()
        confirm_password = getpass("Confirm admin password: ").strip()

        if not username:
            raise CommandError("Username cannot be empty.")
        if password != confirm_password:
            raise CommandError("Passwords do not match.")

        user = User.objects.create_user(
            username=username,
            email=email or None,
            password=password,
        )
        profile = UserProfile.objects.get(user=user)
        profile.role = "admin"
        profile.status = "active"
        profile.save(update_fields=["role", "status"])
        self.stdout.write(self.style.SUCCESS(f"Created admin user '{username}'."))

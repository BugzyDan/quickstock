import json
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from inventory.storage import get_data_file_path
from inventory.models import Item, Category, Brand, Location, StockRecord


class Command(BaseCommand):
    help = "Import the shared inventory_data.json into MySQL via Django models."

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            default=Path(get_data_file_path()),
            help="Path to inventory_data.json (default: project-root/inventory_data.json)",
        )
        parser.add_argument(
            "--username",
            default="admin",
            help="Owner username to assign imported items (default: admin)",
        )
        parser.add_argument(
            "--location",
            default="Main Store",
            help="Location name for stock records (default: Main Store)",
        )

    def handle(self, *args, **options):
        path = Path(options["file"])
        if not path.exists():
            raise CommandError(f"File not found: {path}")

        try:
            data = json.loads(path.read_text())
        except Exception as e:
            raise CommandError(f"Failed to read JSON: {e}")

        inventory = data.get("products") or data.get("inventory") or []
        if not inventory:
            self.stdout.write(self.style.WARNING("No inventory found in JSON."))
            return

        User = get_user_model()
        owner = User.objects.filter(username=options["username"]).first()
        if not owner:
            raise CommandError(f"User '{options['username']}' not found.")

        category_cache = {}
        brand_cache = {}

        def get_category(name: str):
            name = name.strip() or "General"
            if name not in category_cache:
                category_cache[name], _ = Category.objects.get_or_create(owner=owner, name=name)
            return category_cache[name]

        def get_brand(name: str):
            name = name.strip() or "Generic"
            if name not in brand_cache:
                brand_cache[name], _ = Brand.objects.get_or_create(owner=owner, name=name)
            return brand_cache[name]

        # Location for stock records
        location_name = options["location"].strip() or "Main Store"
        location, _ = Location.objects.get_or_create(
            owner=owner, name=location_name, defaults={"country_code": "JM"}
        )

        created = 0
        updated = 0
        for item in inventory:
            sku = str(item.get("SKU") or "").strip()
            if not sku:
                sku = str(item.get("sku") or "").strip()
            name = str(item.get("Name") or "").strip()
            if not name:
                name = str(item.get("name") or "").strip()
            if not name:
                continue
            category = get_category(item.get("Category") or item.get("category") or "General")
            brand = get_brand(item.get("brand") or item.get("Brand") or "Generic")
            cost = float(item.get("Cost") or item.get("cost_price") or item.get("cost") or 0)
            price = float(item.get("Price") or item.get("selling_price") or item.get("price") or 0)
            qty = int(item.get("Amount") or item.get("quantity_on_hand") or item.get("quantity") or 0)

            obj, created_flag = Item.objects.update_or_create(
                sku=sku or None,
                owner=owner,
                defaults={
                    "name": name,
                    "category": category,
                    "brand": brand,
                    "cost_price": cost,
                    "price": price,
                },
            )
            StockRecord.objects.update_or_create(
                item=obj,
                location=location,
                defaults={"quantity": qty},
            )
            if created_flag:
                created += 1
            else:
                updated += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Imported {created} items, updated {updated}, location='{location.name}' owner='{owner.username}'"
            )
        )

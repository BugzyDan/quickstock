from django.db import migrations


def backfill_owner_and_receipt_no(apps, schema_editor):
    Sale = apps.get_model("inventory", "Sale")
    # Use select_related to avoid per-row queries for location
    sales = Sale.objects.select_related("location").order_by("timestamp", "id")

    counters = {}
    updates = []
    for sale in sales:
        if sale.owner_id is None:
            sale.owner_id = getattr(sale.location, "owner_id", None)
        owner_id = sale.owner_id
        if owner_id:
            counters.setdefault(owner_id, 0)
            counters[owner_id] += 1
            sale.receipt_no = counters[owner_id]
        updates.append(sale)

    if updates:
        Sale.objects.bulk_update(updates, ["owner", "receipt_no"])


def reverse_noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0012_rename_inventory_l_owner_i_0bb2f8_idx_inventory_l_owner_i_7df33d_idx_and_more"),
    ]

    operations = [
        migrations.RunPython(backfill_owner_and_receipt_no, reverse_noop),
    ]

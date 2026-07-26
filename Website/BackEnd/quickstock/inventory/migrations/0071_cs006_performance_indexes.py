from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0070_qs004_cash_session_integrity"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="sale",
            index=models.Index(fields=["owner", "-timestamp"], name="sale_owner_ts_desc_idx"),
        ),
        migrations.AddIndex(
            model_name="sale",
            index=models.Index(fields=["owner", "location", "-timestamp"], name="sale_owner_loc_ts_idx"),
        ),
        migrations.AddIndex(
            model_name="sale",
            index=models.Index(fields=["owner", "receipt_no"], name="sale_owner_receipt_idx"),
        ),
        migrations.AddIndex(
            model_name="stockrecord",
            index=models.Index(fields=["location", "quantity"], name="stock_loc_qty_idx"),
        ),
    ]

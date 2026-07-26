from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0009_location_owner"),
    ]

    operations = [
        migrations.AlterField(
            model_name="location",
            name="name",
            field=models.CharField(max_length=100),
        ),
        migrations.AlterUniqueTogether(
            name="location",
            unique_together={("owner", "name")},
        ),
        migrations.AddIndex(
            model_name="location",
            index=models.Index(fields=["owner"], name="inventory_l_owner_i_0bb2f8_idx"),
        ),
        migrations.AddIndex(
            model_name="location",
            index=models.Index(fields=["name"], name="inventory_l_name_i_2b0f4a_idx"),
        ),
    ]

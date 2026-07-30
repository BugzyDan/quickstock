from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0071_cs006_performance_indexes"),
    ]

    operations = [
        migrations.AddField(
            model_name="location",
            name="inventory_capacity",
            field=models.PositiveIntegerField(default=1000),
        ),
    ]

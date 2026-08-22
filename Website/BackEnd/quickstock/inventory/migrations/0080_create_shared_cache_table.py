from django.core.management.commands.createcachetable import Command as CreateCacheTableCommand
from django.db import migrations


def create_shared_cache_table(apps, schema_editor):
    command = CreateCacheTableCommand()
    command.verbosity = 0
    command.create_table(
        schema_editor.connection.alias,
        "quickstock_cache",
        dry_run=False,
    )


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0079_bootstrap_superuser_from_env"),
    ]

    operations = [
        migrations.RunPython(create_shared_cache_table, migrations.RunPython.noop),
    ]

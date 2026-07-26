from django.core.management.base import BaseCommand
from django.db import connections
from django.db.migrations.executor import MigrationExecutor


class Command(BaseCommand):
    help = "Fail if there are unapplied migrations (for deployment checks)."

    def handle(self, *args, **options):
        connection = connections["default"]
        executor = MigrationExecutor(connection)
        targets = executor.loader.graph.leaf_nodes()
        plan = executor.migration_plan(targets)

        if plan:
            self.stderr.write("Unapplied migrations detected:")
            for migration, _backwards in plan:
                self.stderr.write(f"- {migration.app_label}.{migration.name}")
            raise SystemExit(1)

        self.stdout.write("All migrations applied.")

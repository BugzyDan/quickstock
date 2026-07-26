from django.core.management.base import BaseCommand

from inventory.health import get_runtime_health


class Command(BaseCommand):
    help = "Run runtime readiness checks for database, migrations, storage, and logging."

    def handle(self, *args, **options):
        report = get_runtime_health()

        for name, result in report["checks"].items():
            status = "OK" if result.get("ok") else "FAIL"
            self.stdout.write(f"[{status}] {name}: {result.get('detail')}")

        if report["status"] != "ok":
            raise SystemExit(1)

        self.stdout.write(self.style.SUCCESS("Runtime health checks passed."))

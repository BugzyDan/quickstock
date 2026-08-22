import io
import json
import os
import zipfile
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone


class Command(BaseCommand):
    help = "Export database records and local media for a private Render transfer."

    def add_arguments(self, parser):
        parser.add_argument("--output", required=True, help="Destination .quickstock-deploy.zip path.")

    def handle(self, *args, **options):
        output_path = Path(options["output"]).expanduser().resolve()
        if output_path.exists():
            raise CommandError(f"Refusing to overwrite existing bundle: {output_path}")
        output_path.parent.mkdir(parents=True, exist_ok=True)

        fixture = io.StringIO()
        call_command(
            "dumpdata",
            "auth.group",
            "auth.user",
            "inventory",
            exclude=["inventory.userverification"],
            natural_foreign=True,
            natural_primary=True,
            indent=2,
            stdout=fixture,
        )

        manifest = {
            "format": "quickstock-deployment-bundle",
            "version": 1,
            "created_at": timezone.now().isoformat(),
            "contains_sensitive_data": True,
            "excluded_models": [
                "admin.logentry",
                "auth.permission",
                "contenttypes.contenttype",
                "inventory.userverification",
                "sessions.session",
            ],
        }

        with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            bundle.writestr("manifest.json", json.dumps(manifest, indent=2))
            bundle.writestr("fixture.json", fixture.getvalue())

            media_root = Path(settings.MEDIA_ROOT)
            if media_root.exists():
                for media_path in sorted(media_root.rglob("*")):
                    if media_path.is_file():
                        relative_path = media_path.relative_to(media_root).as_posix()
                        bundle.write(media_path, f"media/{relative_path}")

        os.chmod(output_path, 0o600)
        self.stdout.write(
            self.style.SUCCESS(
                f"Private deployment bundle created at {output_path}. Do not commit it to Git."
            )
        )

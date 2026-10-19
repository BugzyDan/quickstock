"""Restore a database backup produced by backup_database.

Usage:
    python manage.py restore_database --archive /backups/quickstock-postgres-....sql.gz --confirm RESTORE

This command is intentionally destructive and requires the literal token
``RESTORE`` so an accidental shell expansion cannot wipe production data.
Stop the web service before running it.
"""

import gzip
import hashlib
import shutil
import subprocess
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import connection


CONFIRM_TOKEN = "RESTORE"


class Command(BaseCommand):
    help = "Restore a verified database backup. Requires --confirm RESTORE."

    def add_arguments(self, parser):
        parser.add_argument("--archive", required=True, help="Path to a .sql.gz or .sqlite3.gz backup.")
        parser.add_argument("--confirm", required=True, help="Must be exactly RESTORE.")
        parser.add_argument(
            "--skip-checksum",
            action="store_true",
            help="Skip the .sha256 manifest check (only for legacy archives).",
        )

    def handle(self, *args, **options):
        if options["confirm"] != CONFIRM_TOKEN:
            raise CommandError(f'Refusing to restore without --confirm {CONFIRM_TOKEN}')

        archive = Path(options["archive"]).expanduser().resolve()
        if not archive.exists():
            raise CommandError(f"Archive not found: {archive}")

        if not options["skip_checksum"]:
            manifest = archive.with_suffix(archive.suffix + ".sha256")
            if not manifest.exists():
                raise CommandError(f"Checksum manifest missing: {manifest}")
            expected = manifest.read_text(encoding="utf-8").split()[0]
            actual = self._sha256(archive)
            if expected != actual:
                raise CommandError("Checksum mismatch; the backup may be corrupt.")

        if connection.vendor == "sqlite":
            self._restore_sqlite(archive)
        elif connection.vendor == "postgresql":
            self._restore_postgres(archive)
        else:
            raise CommandError(f"Unsupported database vendor: {connection.vendor}")

        self.stdout.write(self.style.SUCCESS(f"Restored {archive.name}"))
        self.stdout.write("Run 'python manage.py check_runtime_health' to confirm the service is healthy.")

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _restore_sqlite(self, archive: Path) -> None:
        connection.close()
        target = Path(connection.settings_dict["NAME"])
        with gzip.open(archive, "rb") as gz, target.open("wb") as out:
            shutil.copyfileobj(gz, out)

    def _restore_postgres(self, archive: Path) -> None:
        if shutil.which("psql") is None:
            raise CommandError("psql is not installed; cannot restore a PostgreSQL backup.")
        settings = connection.settings_dict
        command = [
            "psql",
            "--set=ON_ERROR_STOP=1",
            f"--host={settings.get('HOST') or 'localhost'}",
            f"--port={settings.get('PORT') or 5432}",
            f"--username={settings.get('USER')}",
            settings["NAME"],
        ]
        env = {**__import__("os").environ, "PGPASSWORD": settings.get("PASSWORD", "")}
        with gzip.open(archive, "rb") as gz:
            result = subprocess.run(command, stdin=gz, env=env)
        if result.returncode != 0:
            raise CommandError("psql restore failed; check the server log.")

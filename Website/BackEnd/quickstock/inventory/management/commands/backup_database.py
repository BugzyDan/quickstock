"""Create and verify a database backup before migrations or releases.

Usage:
    python manage.py backup_database --output-dir /backups
    python manage.py backup_database --output-dir /backups --retain 30

The command writes a timestamped dump, verifies that the dump is non-empty and
readable, and prunes backups older than --retain days. Point it at a directory
that is stored off the application host; a backup on the same disk as the
database does not protect against host loss.
"""

import gzip
import hashlib
import os
import shutil
import subprocess
from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.utils import timezone


class Command(BaseCommand):
    help = "Create a verified database backup and prune old copies."

    def add_arguments(self, parser):
        parser.add_argument("--output-dir", required=True, help="Directory that stores backup archives.")
        parser.add_argument("--retain", type=int, default=30, help="Days of backups to keep (default: 30).")
        parser.add_argument("--skip-prune", action="store_true", help="Keep every existing backup.")

    def handle(self, *args, **options):
        output_dir = Path(options["output_dir"]).expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        vendor = connection.vendor
        stamp = timezone.now().strftime("%Y%m%dT%H%M%S")
        if vendor == "sqlite":
            archive = self._backup_sqlite(output_dir, stamp)
        elif vendor == "postgresql":
            archive = self._backup_postgres(output_dir, stamp)
        else:
            raise CommandError(f"Unsupported database vendor for backup: {vendor}")

        if archive.stat().st_size == 0:
            raise CommandError(f"Backup {archive} is empty; treating the run as failed.")

        digest = self._sha256(archive)
        manifest = archive.with_suffix(archive.suffix + ".sha256")
        manifest.write_text(f"{digest}  {archive.name}\n", encoding="utf-8")

        self.stdout.write(self.style.SUCCESS(f"Backup written: {archive}"))
        self.stdout.write(f"SHA256: {digest}")

        if not options["skip_prune"]:
            self._prune(output_dir, options["retain"])

    # -- helpers ---------------------------------------------------------

    def _backup_sqlite(self, output_dir: Path, stamp: str) -> Path:
        import sqlite3

        db_path = Path(connection.settings_dict["NAME"])
        if not db_path.exists():
            raise CommandError(f"SQLite database not found: {db_path}")
        archive = output_dir / f"quickstock-sqlite-{stamp}.sqlite3.gz"
        snapshot = output_dir / f".quickstock-snapshot-{stamp}.sqlite3"
        # SQLite's online backup API takes a consistent copy while the app is running.
        source = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            target_conn = sqlite3.connect(snapshot)
            try:
                source.backup(target_conn)
            finally:
                target_conn.close()
        finally:
            source.close()
        with snapshot.open("rb") as raw, gzip.open(archive, "wb") as gz:
            shutil.copyfileobj(raw, gz)
        snapshot.unlink()
        return archive

    def _backup_postgres(self, output_dir: Path, stamp: str) -> Path:
        if shutil.which("pg_dump") is None:
            raise CommandError("pg_dump is not installed; cannot create a PostgreSQL backup.")
        settings = connection.settings_dict
        archive = output_dir / f"quickstock-postgres-{stamp}.sql.gz"
        env = {
            **dict(os.environ),
            "PGPASSWORD": settings.get("PASSWORD", ""),
        }
        command = [
            "pg_dump",
            "--no-owner",
            "--no-acl",
            "--format=plain",
            f"--host={settings.get('HOST') or 'localhost'}",
            f"--port={settings.get('PORT') or 5432}",
            f"--username={settings.get('USER')}",
            settings["NAME"],
        ]
        with gzip.open(archive, "wb") as gz:
            result = subprocess.run(command, stdout=gz, stderr=subprocess.PIPE, env=env)
        if result.returncode != 0:
            archive.unlink(missing_ok=True)
            raise CommandError(f"pg_dump failed: {result.stderr.decode('utf-8', 'replace')}")
        return archive

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _prune(self, output_dir: Path, retain_days: int) -> None:
        cutoff = timezone.now() - timedelta(days=retain_days)
        removed = 0
        for path in output_dir.glob("quickstock-*"):
            modified = datetime.fromtimestamp(path.stat().st_mtime, tz=dt_timezone.utc)
            if modified < cutoff:
                path.unlink()
                removed += 1
        if removed:
            self.stdout.write(f"Pruned {removed} backup file(s) older than {retain_days} days.")

import json
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from django.contrib.auth import get_user_model
from django.apps import apps
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


MAX_ARCHIVE_ENTRIES = 10_000
MAX_FIXTURE_BYTES = 100 * 1024 * 1024
MAX_MEDIA_FILE_BYTES = 50 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 500 * 1024 * 1024


class Command(BaseCommand):
    help = "Import a private QuickStock deployment bundle into Render."

    def add_arguments(self, parser):
        parser.add_argument("bundle", help="Path to a .quickstock-deploy.zip bundle.")
        parser.add_argument("--confirm", help="Required acknowledgement; pass IMPORT.")

    def handle(self, *args, **options):
        if options.get("confirm") != "IMPORT":
            raise CommandError("Pass --confirm IMPORT to acknowledge this production data import.")

        bundle_path = Path(options["bundle"]).expanduser().resolve()
        if not bundle_path.is_file():
            raise CommandError(f"Bundle not found: {bundle_path}")

        User = get_user_model()
        inventory_has_data = any(
            model.objects.exists()
            for model in apps.get_app_config("inventory").get_models()
        )
        if User.objects.exists() or inventory_has_data:
            raise CommandError(
                "The target database already contains application data. Import into a fresh database only."
            )

        with zipfile.ZipFile(bundle_path) as bundle:
            members = bundle.infolist()
            if len(members) > MAX_ARCHIVE_ENTRIES:
                raise CommandError("The deployment bundle contains too many files.")
            if sum(member.file_size for member in members) > MAX_UNCOMPRESSED_BYTES:
                raise CommandError("The deployment bundle is too large when extracted.")
            names = set(bundle.namelist())
            if not {"manifest.json", "fixture.json"}.issubset(names):
                raise CommandError("The bundle is missing manifest.json or fixture.json.")

            manifest = json.loads(bundle.read("manifest.json"))
            if manifest.get("format") != "quickstock-deployment-bundle" or manifest.get("version") != 1:
                raise CommandError("Unsupported QuickStock deployment bundle format.")

            fixture_info = bundle.getinfo("fixture.json")
            if fixture_info.file_size > MAX_FIXTURE_BYTES:
                raise CommandError("The deployment fixture is too large.")

            media_members = []
            for member in members:
                if member.is_dir() or not member.filename.startswith("media/"):
                    continue
                relative = PurePosixPath(member.filename).relative_to("media")
                if relative.is_absolute() or ".." in relative.parts:
                    raise CommandError(f"Unsafe media path in bundle: {member.filename}")
                if member.file_size > MAX_MEDIA_FILE_BYTES:
                    raise CommandError(f"Media file is too large: {member.filename}")
                storage_name = relative.as_posix()
                if default_storage.exists(storage_name):
                    raise CommandError(f"Media already exists in target storage: {storage_name}")
                media_members.append((member, storage_name))

            media_count = 0
            saved_media = []
            try:
                with transaction.atomic():
                    with tempfile.TemporaryDirectory(prefix="quickstock-import-") as temp_dir:
                        fixture_path = Path(temp_dir) / "fixture.json"
                        fixture_path.write_bytes(bundle.read("fixture.json"))
                        call_command("loaddata", str(fixture_path))

                    for member, storage_name in media_members:
                        saved_name = default_storage.save(
                            storage_name,
                            ContentFile(bundle.read(member)),
                        )
                        if saved_name != storage_name:
                            raise CommandError(
                                f"Media storage renamed {storage_name}; import was aborted."
                            )
                        saved_media.append(saved_name)
                        media_count += 1
            except Exception:
                for storage_name in saved_media:
                    default_storage.delete(storage_name)
                raise

        self.stdout.write(
            self.style.SUCCESS(
                f"Deployment bundle imported successfully with {media_count} media file(s)."
            )
        )

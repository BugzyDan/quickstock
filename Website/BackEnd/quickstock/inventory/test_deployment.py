import json
import os
import tempfile
import zipfile
from pathlib import Path

from django.contrib.auth.models import User
from django.core.files.storage import default_storage
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings


def _write_bundle(path, *, fixture="[]", media=None):
    manifest = {
        "format": "quickstock-deployment-bundle",
        "version": 1,
        "contains_sensitive_data": True,
    }
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("manifest.json", json.dumps(manifest))
        bundle.writestr("fixture.json", fixture)
        for name, content in (media or {}).items():
            bundle.writestr(name, content)


class DeploymentBundleTests(TestCase):
    def test_export_bundle_is_private_and_contains_manifest(self):
        User.objects.create_user("bundle-owner", password="password123")
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "clone.quickstock-deploy.zip"
            call_command("export_deployment_bundle", output=str(output), verbosity=0)

            self.assertEqual(os.stat(output).st_mode & 0o777, 0o600)
            with zipfile.ZipFile(output) as bundle:
                self.assertIn("manifest.json", bundle.namelist())
                self.assertIn("fixture.json", bundle.namelist())
                manifest = json.loads(bundle.read("manifest.json"))
                self.assertTrue(manifest["contains_sensitive_data"])

    def test_import_refuses_database_with_existing_application_data(self):
        User.objects.create_user("existing-owner", password="password123")
        with tempfile.TemporaryDirectory() as temp_dir:
            bundle_path = Path(temp_dir) / "clone.quickstock-deploy.zip"
            _write_bundle(bundle_path)

            with self.assertRaisesMessage(CommandError, "fresh database only"):
                call_command(
                    "import_deployment_bundle",
                    str(bundle_path),
                    confirm="IMPORT",
                )

    def test_empty_bundle_imports_media_without_overwriting(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            media_root = Path(temp_dir) / "media"
            bundle_path = Path(temp_dir) / "clone.quickstock-deploy.zip"
            _write_bundle(bundle_path, media={"media/logos/test.txt": b"logo"})

            storage_settings = {
                "default": {
                    "BACKEND": "django.core.files.storage.FileSystemStorage",
                    "OPTIONS": {"location": str(media_root)},
                },
                "staticfiles": {
                    "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
                },
            }
            with override_settings(STORAGES=storage_settings, MEDIA_ROOT=media_root):
                call_command(
                    "import_deployment_bundle",
                    str(bundle_path),
                    confirm="IMPORT",
                    verbosity=0,
                )
                self.assertTrue(default_storage.exists("logos/test.txt"))

                with self.assertRaisesMessage(CommandError, "already exists"):
                    call_command(
                        "import_deployment_bundle",
                        str(bundle_path),
                        confirm="IMPORT",
                        verbosity=0,
                    )

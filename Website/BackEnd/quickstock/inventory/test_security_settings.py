import os
import subprocess
import sys
from pathlib import Path
from unittest import TestCase


BASE_DIR = Path(__file__).resolve().parents[1]


class ProductionSecuritySettingsTests(TestCase):
    def _run_settings_import(self, overrides):
        env = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": str(BASE_DIR),
            "DJANGO_ENV_FILE": str(BASE_DIR / ".env.does-not-exist"),
            "DJANGO_DEBUG": "false",
            "DJANGO_SECRET_KEY": "secure-production-secret-key-for-quickstock-validation-1234567890",
            "DJANGO_ALLOWED_HOSTS": "quickstockja.com,www.quickstockja.com",
            "DJANGO_CSRF_TRUSTED_ORIGINS": "https://quickstockja.com,https://www.quickstockja.com",
            "DJANGO_SESSION_COOKIE_SECURE": "true",
            "DJANGO_CSRF_COOKIE_SECURE": "true",
            "DJANGO_SECURE_SSL_REDIRECT": "true",
            "DJANGO_SECURE_HSTS_SECONDS": "31536000",
            "DJANGO_DB_ENGINE": "django.db.backends.sqlite3",
            "DJANGO_DB_NAME": "settings_validation.sqlite3",
            "WIPAY_ENVIRONMENT": "live",
            "WIPAY_ACCOUNT_NUMBER_LIVE": "123456789",
            "WIPAY_API_KEY_LIVE": "live-key-for-settings-validation",
        }
        env.update(overrides)
        return subprocess.run(
            [sys.executable, "-c", "import quickstock.settings"],
            cwd=BASE_DIR,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def assert_settings_import_fails(self, overrides, expected_message):
        result = self._run_settings_import(overrides)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(expected_message, result.stdout + result.stderr)

    def test_production_settings_accept_hardened_configuration(self):
        result = self._run_settings_import({})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_production_settings_accept_render_blueprint_configuration(self):
        result = self._run_settings_import(
            {
                "RENDER": "true",
                "RENDER_EXTERNAL_HOSTNAME": "quickstock-ja.onrender.com",
                "DJANGO_SECRET_KEY": "render-generated-secret-value-123456",
                "DJANGO_ALLOWED_HOSTS": "quickstock-ja.onrender.com,quickstockja.com,www.quickstockja.com",
                "DJANGO_CSRF_TRUSTED_ORIGINS": "https://quickstock-ja.onrender.com,https://quickstockja.com,https://www.quickstockja.com",
                "DATABASE_URL": "sqlite:///render_settings_validation.sqlite3",
            }
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_production_rejects_placeholder_secret_key(self):
        self.assert_settings_import_fails(
            {"DJANGO_SECRET_KEY": "replace-me-with-a-long-random-secret"},
            "DJANGO_SECRET_KEY must be a strong non-placeholder value",
        )

    def test_production_rejects_render_fallback_secret_key(self):
        self.assert_settings_import_fails(
            {"DJANGO_SECRET_KEY": "render-fallback-" + ("a" * 64)},
            "DJANGO_SECRET_KEY must be a stable production secret",
        )

    def test_production_rejects_wildcard_allowed_hosts(self):
        self.assert_settings_import_fails(
            {"DJANGO_ALLOWED_HOSTS": "quickstockja.com,*"},
            "DJANGO_ALLOWED_HOSTS must contain only real production hostnames",
        )

    def test_production_rejects_local_allowed_hosts(self):
        self.assert_settings_import_fails(
            {"DJANGO_ALLOWED_HOSTS": "quickstockja.com,localhost"},
            "DJANGO_ALLOWED_HOSTS must contain only real production hostnames",
        )

    def test_production_rejects_insecure_csrf_origin(self):
        self.assert_settings_import_fails(
            {"DJANGO_CSRF_TRUSTED_ORIGINS": "http://quickstockja.com"},
            "DJANGO_CSRF_TRUSTED_ORIGINS must contain only HTTPS production origins",
        )

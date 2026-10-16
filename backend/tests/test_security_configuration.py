from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import app_config  # noqa: E402


class FlaskSecretKeyConfigurationTests(unittest.TestCase):
    def test_production_missing_key_is_rejected(self):
        with patch.dict(os.environ, {"APP_ENV": "production"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "FLASK_SECRET_KEY"):
                app_config.get_flask_secret_key()

    def test_production_blank_key_is_rejected(self):
        with patch.dict(
            os.environ,
            {"APP_ENV": "production", "FLASK_SECRET_KEY": "   "},
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "FLASK_SECRET_KEY"):
                app_config.get_flask_secret_key()

    def test_production_short_key_is_rejected(self):
        with patch.dict(
            os.environ,
            {"APP_ENV": "production", "FLASK_SECRET_KEY": "too-short"},
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "32"):
                app_config.get_flask_secret_key()

    def test_known_source_fallback_key_is_rejected(self):
        with patch.dict(
            os.environ,
            {
                "APP_ENV": "production",
                "FLASK_SECRET_KEY": "uts-career-navigator-dev-secret",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "FLASK_SECRET_KEY"):
                app_config.get_flask_secret_key()

    def test_documented_placeholder_key_is_rejected_even_when_long_enough(self):
        with patch.dict(
            os.environ,
            {
                "APP_ENV": "production",
                "FLASK_SECRET_KEY": "change_this_to_a_random_secret_for_sessions",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "FLASK_SECRET_KEY"):
                app_config.get_flask_secret_key()

    def test_valid_production_key_is_accepted_without_modification(self):
        valid_key = "mL7!b8Qv9zD2pR4xK6sT1wY3nF5hJ0cG"
        with patch.dict(
            os.environ,
            {"APP_ENV": "production", "FLASK_SECRET_KEY": valid_key},
            clear=True,
        ):
            self.assertEqual(app_config.get_flask_secret_key(), valid_key)

    def test_default_environment_is_production(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(app_config.get_app_environment(), "production")
            with self.assertRaisesRegex(RuntimeError, "FLASK_SECRET_KEY"):
                app_config.get_flask_secret_key()

    def test_flask_env_is_supported_as_environment_fallback(self):
        with patch.dict(os.environ, {"FLASK_ENV": "testing"}, clear=True):
            self.assertEqual(app_config.get_app_environment(), "testing")
            generated_key = app_config.get_flask_secret_key()

        self.assertGreaterEqual(len(generated_key), 32)
        self.assertNotEqual(generated_key, "uts-career-navigator-dev-secret")

    def test_development_without_configured_key_uses_strong_ephemeral_key(self):
        with patch.dict(os.environ, {"APP_ENV": "development"}, clear=True):
            generated_key = app_config.get_flask_secret_key()

        self.assertGreaterEqual(len(generated_key), 32)
        self.assertNotEqual(generated_key, "uts-career-navigator-dev-secret")

    def test_supplied_short_key_is_rejected_in_testing_too(self):
        with patch.dict(
            os.environ,
            {"APP_ENV": "testing", "FLASK_SECRET_KEY": "short-test-key"},
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "32"):
                app_config.get_flask_secret_key()

    def test_import_refuses_to_start_with_insecure_production_keys(self):
        insecure_keys = (
            "",
            "too-short",
            "uts-career-navigator-dev-secret",
            "change_this_to_a_random_secret_for_sessions",
        )

        for insecure_key in insecure_keys:
            with self.subTest(secret_key=insecure_key or "<missing>"):
                environment = os.environ.copy()
                environment.update(
                    {
                        "APP_ENV": "production",
                        "FLASK_SECRET_KEY": insecure_key,
                        "ADMIN_EMAILS": "security-tests@example.com",
                        "RESEND_API_KEY": "re_security_configuration_test_key_not_real",
                        "RESEND_FROM": "Career Navigator <security-tests@example.com>",
                        "EMAIL_PIN_DEV_MODE": "0",
                    }
                )
                result = subprocess.run(
                    [sys.executable, "-c", "import app"],
                    cwd=BACKEND_DIR,
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )

                self.assertNotEqual(result.returncode, 0)
                self.assertIn("FLASK_SECRET_KEY", result.stderr)

    def test_import_accepts_valid_production_key(self):
        valid_key = "mL7!b8Qv9zD2pR4xK6sT1wY3nF5hJ0cG"
        environment = os.environ.copy()
        environment.update(
            {
                "APP_ENV": "production",
                "FLASK_SECRET_KEY": valid_key,
                "ADMIN_EMAILS": "security-tests@example.com",
                "RESEND_API_KEY": "re_security_configuration_test_key_not_real",
                "RESEND_FROM": "Career Navigator <security-tests@example.com>",
                "EMAIL_PIN_DEV_MODE": "0",
            }
        )
        import_script = "\n".join(
            (
                "import app",
                f"assert app.app.secret_key == {valid_key!r}",
                "assert app.app.config['SESSION_COOKIE_SECURE'] is True",
            )
        )

        result = subprocess.run(
            [sys.executable, "-c", import_script],
            cwd=BACKEND_DIR,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()

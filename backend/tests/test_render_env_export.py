from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from dotenv import dotenv_values


TOOLS_DIR = Path(__file__).resolve().parents[2] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import prepare_render_env as exporter  # noqa: E402


SYNTHETIC_DATABASE_URL = (
    "postgresql://postgres.synthetic:synthetic-database-password@"
    "aws-0-ap-southeast-1.pooler.supabase.com:5432/postgres?sslmode=require"
)


def write_settings(path: Path, settings: dict[str, str]) -> None:
    path.write_text(
        "\n".join(f"{key}={json.dumps(value)}" for key, value in settings.items()) + "\n",
        encoding="utf-8",
    )


class RenderEnvExportTests(unittest.TestCase):
    """Use temporary Git repositories; never read a developer's private settings."""

    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_directory.cleanup)
        self.root = Path(self.temp_directory.name)
        (self.root / "config").mkdir()
        self.source = self.root / "config" / "env"
        self.output = self.root / "config" / ".env.render"
        self.git("init", "--quiet")
        (self.root / ".gitignore").write_text("config/.env.render\n", encoding="utf-8")
        self.source_settings = {
            "DATABASE_URL": SYNTHETIC_DATABASE_URL,
            "ADMIN_EMAILS": "admin@example.test",
            "SMTP_FROM": "Test Sender <sender@example.test>",
            "SMTP_HOST": "smtp.gmail.com",
            "SMTP_USERNAME": "sender@example.test",
            "SMTP_PASSWORD": "synthetic-gmail-app-password",
            "MYSQL_PASSWORD": "synthetic-mysql-password",
            "RESEND_API_KEY": "synthetic-unused-resend-key",
            "UNRELATED_PRIVATE_SETTING": "synthetic-unrelated-secret",
            "FLASK_SECRET_KEY": "local-development-key-must-not-be-exported",
            "APP_ENV": "development",
            "EMAIL_PIN_DEV_MODE": "1",
        }
        write_settings(self.source, self.source_settings)

    def git(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", str(self.root), *args],
            capture_output=True,
            check=True,
        )

    def invoke(self, *, check: bool = False) -> tuple[int, str]:
        console = io.StringIO()
        argv = ["prepare_render_env.py"] + (["--check"] if check else [])
        with (
            patch.object(exporter, "PROJECT_ROOT", self.root),
            patch.object(exporter, "SOURCE_FILE", self.source),
            patch.object(exporter, "OUTPUT_FILE", self.output),
            patch.object(sys, "argv", argv),
            redirect_stdout(console),
        ):
            status = exporter.main()
        return status, console.getvalue()

    def test_export_filters_local_credentials_and_forces_production_settings(self):
        status, console = self.invoke()
        self.assertEqual(status, 0)
        exported = dict(dotenv_values(self.output, interpolate=False))
        contents = self.output.read_text(encoding="utf-8")
        self.assertEqual(set(exported), set(exporter.EXPORT_KEYS))
        self.assertEqual(exported["DATABASE_URL"], SYNTHETIC_DATABASE_URL)
        self.assertEqual(exported["BREVO_FROM"], "sender@example.test")
        self.assertEqual(exported["APP_ENV"], "production")
        self.assertEqual(exported["EMAIL_PIN_DEV_MODE"], "0")
        self.assertEqual(exported["TRUSTED_PROXY_COUNT"], "1")
        self.assertGreaterEqual(len(exported["FLASK_SECRET_KEY"]), 32)
        for key in (
            "SMTP_PASSWORD", "MYSQL_PASSWORD", "RESEND_API_KEY",
            "UNRELATED_PRIVATE_SETTING", "FLASK_SECRET_KEY",
        ):
            self.assertNotIn(self.source_settings[key], contents)
            self.assertNotIn(self.source_settings[key], console)
        self.assertFalse(any(key.startswith("SMTP_") for key in exported))
        self.assertNotIn(SYNTHETIC_DATABASE_URL, console)
        self.assertNotIn(exported["FLASK_SECRET_KEY"], console)

    def test_rerun_preserves_generated_session_secret_and_private_brevo_key(self):
        self.assertEqual(self.invoke()[0], 0)
        first = dict(dotenv_values(self.output, interpolate=False))
        first["BREVO_API_KEY"] = "synthetic-private-brevo-key"
        write_settings(self.output, first)
        self.source_settings["BREVO_API_KEY"] = "synthetic-stale-source-key"
        write_settings(self.source, self.source_settings)

        status, console = self.invoke()

        self.assertEqual(status, 0)
        second = dict(dotenv_values(self.output, interpolate=False))
        self.assertEqual(second["FLASK_SECRET_KEY"], first["FLASK_SECRET_KEY"])
        self.assertEqual(second["BREVO_API_KEY"], first["BREVO_API_KEY"])
        self.assertNotIn(first["FLASK_SECRET_KEY"], console)
        self.assertNotIn(first["BREVO_API_KEY"], console)
        self.assertNotIn("Still required", console)

    def test_check_reports_blank_api_key_and_does_not_modify_file(self):
        self.assertEqual(self.invoke()[0], 0)
        before = self.output.read_bytes()

        status, console = self.invoke(check=True)

        self.assertEqual(status, 2)
        self.assertIn("Still required: BREVO_API_KEY", console)
        self.assertEqual(self.output.read_bytes(), before)
        self.assertNotIn(SYNTHETIC_DATABASE_URL, console)

    def test_check_accepts_complete_settings_with_optional_display_name_empty(self):
        values = exporter.prepare_settings(self.source_settings, {})
        values["BREVO_API_KEY"] = "synthetic-brevo-key"
        values["BREVO_FROM_NAME"] = ""
        write_settings(self.output, values)

        status, console = self.invoke(check=True)

        self.assertEqual(status, 0)
        self.assertIn("Required settings are present", console)

    def test_check_rejects_insecure_session_secrets_without_printing_them(self):
        invalid_keys = (
            "uts-career-navigator-dev-secret",
            "change_this_to_a_random_secret_for_sessions",
            "  CHANGE_THIS_TO_A_RANDOM_SECRET_FOR_SESSIONS  ",
            "change-me",
            "replace_me",
            "replace_with_a_random_secret",
            "  " + "x" * 31 + "  ",
        )
        for secret in invalid_keys:
            with self.subTest(secret=secret):
                values = exporter.prepare_settings(self.source_settings, {})
                values["BREVO_API_KEY"] = "synthetic-brevo-key"
                values["FLASK_SECRET_KEY"] = secret
                write_settings(self.output, values)

                status, console = self.invoke(check=True)

                self.assertEqual(status, 1)
                self.assertNotIn(secret, console)
                self.assertNotIn(values["BREVO_API_KEY"], console)

    def test_rejects_non_tls_or_non_session_supabase_urls_before_writing(self):
        invalid_urls = (
            SYNTHETIC_DATABASE_URL.replace("?sslmode=require", ""),
            SYNTHETIC_DATABASE_URL.replace("sslmode=require", "sslmode=disable"),
            SYNTHETIC_DATABASE_URL.replace("sslmode=require", "sslmode=prefer"),
            SYNTHETIC_DATABASE_URL + "&sslmode=disable",
            SYNTHETIC_DATABASE_URL.replace(":5432/", ":6543/"),
            SYNTHETIC_DATABASE_URL.replace(
                "aws-0-ap-southeast-1.pooler.supabase.com", "db.synthetic.supabase.co"
            ),
            SYNTHETIC_DATABASE_URL.replace(
                "aws-0-ap-southeast-1.pooler.supabase.com", "pooler.supabase.com.attacker.test"
            ),
        )
        for url in invalid_urls:
            with self.subTest(url=url):
                write_settings(self.source, {**self.source_settings, "DATABASE_URL": url})
                status, console = self.invoke()
                self.assertEqual(status, 1)
                self.assertFalse(self.output.exists())
                self.assertNotIn(url, console)

    def test_refuses_output_not_ignored_by_git(self):
        (self.root / ".gitignore").write_text("*.log\n", encoding="utf-8")

        status, console = self.invoke()

        self.assertEqual(status, 1)
        self.assertFalse(self.output.exists())
        self.assertNotIn(SYNTHETIC_DATABASE_URL, console)

    def test_refuses_output_tracked_even_when_ignore_rule_matches(self):
        self.output.write_text("synthetic tracked content\n", encoding="utf-8")
        self.git("add", "--force", "config/.env.render")

        status, console = self.invoke()

        self.assertEqual(status, 1)
        self.assertEqual(self.output.read_text(encoding="utf-8"), "synthetic tracked content\n")
        self.assertNotIn(SYNTHETIC_DATABASE_URL, console)

    def test_output_must_stay_inside_project(self):
        with self.assertRaisesRegex(ValueError, "inside this project"):
            exporter.verify_ignored_output(self.root, self.root.parent / "outside.env")

    def test_check_rejects_unexpected_credentials_without_printing_them(self):
        values = exporter.prepare_settings(self.source_settings, {})
        values["BREVO_API_KEY"] = "synthetic-brevo-key"
        values["SMTP_PASSWORD"] = "synthetic-leaked-gmail-password"
        write_settings(self.output, values)
        before = self.output.read_bytes()

        status, console = self.invoke(check=True)

        self.assertEqual(status, 1)
        self.assertEqual(self.output.read_bytes(), before)
        self.assertNotIn(values["SMTP_PASSWORD"], console)
        self.assertNotIn(values["BREVO_API_KEY"], console)


if __name__ == "__main__":
    unittest.main()

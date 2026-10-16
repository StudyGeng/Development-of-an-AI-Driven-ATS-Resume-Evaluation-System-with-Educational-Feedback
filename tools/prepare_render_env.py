"""Prepare a Git-ignored Render import file without printing its secrets."""

from __future__ import annotations

import argparse
import json
import re
import secrets
import subprocess
from email.utils import parseaddr
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from dotenv import dotenv_values


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_FILE = PROJECT_ROOT / "config" / "env"
OUTPUT_FILE = PROJECT_ROOT / "config" / ".env.render"
EMAIL_PATTERN = re.compile(r"[^\s@,;<>]+@[^\s@,;<>]+\.[^\s@,;<>]+")
# Match backend/app_config.py without importing configuration that reads the environment.
INSECURE_FLASK_SECRET_KEYS = {
    "uts-career-navigator-dev-secret",
    "change_this_to_a_random_secret_for_sessions",
    "change-me",
    "replace_me",
    "replace_with_a_random_secret",
}
EXPORT_KEYS = (
    "DATABASE_URL",
    "APP_ENV",
    "FLASK_SECRET_KEY",
    "ADMIN_EMAILS",
    "TRUSTED_PROXY_COUNT",
    "EMAIL_PIN_DEV_MODE",
    "BREVO_FROM",
    "BREVO_FROM_NAME",
    "BREVO_API_KEY",
)
REQUIRED_KEYS = tuple(key for key in EXPORT_KEYS if key != "BREVO_FROM_NAME")


def verify_ignored_output(root: Path, output: Path) -> None:
    try:
        relative = output.resolve().relative_to(root.resolve())
    except ValueError:
        raise ValueError("The private output must stay inside this project.") from None
    result = subprocess.run(
        ["git", "-C", str(root), "check-ignore", "--quiet", "--", str(relative)],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError("The private output must be ignored by Git before writing it.")
    tracked = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--error-unmatch", "--", str(relative)],
        capture_output=True,
        check=False,
    )
    if tracked.returncode == 0:
        raise ValueError("Refusing to write secrets to a tracked file.")


def validate_settings(values: dict[str, str]) -> list[str]:
    """Check structure only; this does not contact Supabase or send email."""
    for value in values.values():
        if "\n" in value or "\r" in value:
            raise ValueError("Settings must each fit on one line.")
    missing = [key for key in REQUIRED_KEYS if not values.get(key, "").strip()]
    database_url = values.get("DATABASE_URL", "")
    if database_url:
        try:
            parsed = urlsplit(database_url)
            valid_url = (
                parsed.scheme in {"postgresql", "postgres"}
                and bool(parsed.username)
                and bool(parsed.password)
                and (parsed.hostname or "").endswith(".pooler.supabase.com")
                and parsed.port == 5432
                and parsed.path == "/postgres"
                and parse_qs(parsed.query).get("sslmode") == ["require"]
            )
        except (ValueError, TypeError):
            valid_url = False
        if not valid_url:
            raise ValueError("DATABASE_URL must be the Supabase Session pooler URI with TLS required.")
    session_secret = values.get("FLASK_SECRET_KEY", "").strip()
    if session_secret:
        if session_secret.lower() in INSECURE_FLASK_SECRET_KEYS:
            raise ValueError("FLASK_SECRET_KEY must not use a known placeholder.")
        if len(session_secret) < 32:
            raise ValueError("FLASK_SECRET_KEY must be at least 32 characters.")
    if values.get("ADMIN_EMAILS"):
        admins = [part.strip() for part in re.split(r"[,;]", values["ADMIN_EMAILS"])]
        if not admins or any(not EMAIL_PATTERN.fullmatch(email) for email in admins):
            raise ValueError("ADMIN_EMAILS must contain valid administrator email addresses.")
    if values.get("BREVO_FROM") and not EMAIL_PATTERN.fullmatch(values["BREVO_FROM"]):
        raise ValueError("BREVO_FROM must contain a plain sender email address.")
    for key, expected in (
        ("APP_ENV", "production"),
        ("TRUSTED_PROXY_COUNT", "1"),
        ("EMAIL_PIN_DEV_MODE", "0"),
    ):
        if values.get(key) and values[key] != expected:
            raise ValueError(f"{key} has an unexpected value for this deployment.")
    return missing


def prepare_settings(source: dict[str, str | None], existing: dict[str, str | None]) -> dict[str, str]:
    sender = (
        existing.get("BREVO_FROM")
        or source.get("BREVO_FROM")
        or parseaddr(source.get("SMTP_FROM") or "")[1]
        or source.get("SMTP_USERNAME")
        or ""
    ).strip().lower()
    admin_email = (
        existing.get("ADMIN_EMAILS")
        or source.get("ADMIN_EMAILS")
        or source.get("ADMIN_EMAIL")
        or sender
    ).strip()
    # Keep an existing import's key stable when the user adds the email API key.
    session_secret = (existing.get("FLASK_SECRET_KEY") or "").strip() or secrets.token_urlsafe(48)
    return {
        "DATABASE_URL": (source.get("DATABASE_URL") or existing.get("DATABASE_URL") or "").strip(),
        "APP_ENV": "production",
        "FLASK_SECRET_KEY": session_secret,
        "ADMIN_EMAILS": admin_email,
        "TRUSTED_PROXY_COUNT": "1",
        "EMAIL_PIN_DEV_MODE": "0",
        "BREVO_FROM": sender,
        "BREVO_FROM_NAME": (existing.get("BREVO_FROM_NAME") or source.get("BREVO_FROM_NAME") or "UTS ATS System").strip(),
        "BREVO_API_KEY": (existing.get("BREVO_API_KEY") or source.get("BREVO_API_KEY") or "").strip(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Check the private import file without modifying it.")
    args = parser.parse_args()
    try:
        verify_ignored_output(PROJECT_ROOT, OUTPUT_FILE)
        if args.check:
            if not OUTPUT_FILE.exists():
                raise ValueError("Prepare config/.env.render first.")
            values = {key: value or "" for key, value in dotenv_values(OUTPUT_FILE, interpolate=False).items()}
            unexpected = set(values) - set(EXPORT_KEYS)
            if unexpected:
                raise ValueError("The import contains unexpected settings; regenerate it before use.")
        else:
            if not SOURCE_FILE.exists():
                raise ValueError("The local config/env file is missing.")
            source = dict(dotenv_values(SOURCE_FILE, interpolate=False))
            existing = dict(dotenv_values(OUTPUT_FILE, interpolate=False)) if OUTPUT_FILE.exists() else {}
            values = prepare_settings(source, existing)
        missing = validate_settings(values)
        if not args.check:
            content = (
                "# PRIVATE: import into Render using Add from .env; keep out of GitHub and chat.\n"
                "# Fill BREVO_API_KEY privately after creating and verifying your Brevo sender.\n"
                "# Field checks do not verify provider activation or live email delivery.\n"
            )
            content += "\n".join(f"{key}={json.dumps(values[key])}" for key in EXPORT_KEYS) + "\n"
            OUTPUT_FILE.write_text(content, encoding="utf-8")
            print("Prepared config/.env.render (ignored by Git). No secret values were printed.")
        if missing:
            print("Still required: " + ", ".join(missing))
            return 2 if args.check else 0
        print("Required settings are present. Live database and email delivery still need verification.")
        return 0
    except (ValueError, OSError):
        # Even an OS/parser error can include a URL or path supplied as a value.
        print("Could not prepare or validate the import file. Check its location and required settings privately.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Authentication and session helpers for the Flask application."""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any

from flask import session

from app_config import SPECIAL_CHARACTER_PATTERN
from core_utils import normalize_match_text, normalize_text


def normalize_email(email: str) -> str:
    return normalize_text(email).lower()


def get_admin_emails() -> set[str]:
    configured = os.getenv("ADMIN_EMAILS") or os.getenv("ADMIN_EMAIL", "")
    normalized_emails = (normalize_email(email) for email in re.split(r"[,;]", configured))
    return {email for email in normalized_emails if email}


def validate_admin_email_configuration(require_configured_admin: bool = False) -> set[str]:
    configured_emails = get_admin_emails()
    invalid_emails = sorted(email for email in configured_emails if not is_valid_email(email))
    if invalid_emails:
        raise RuntimeError("ADMIN_EMAILS contains an invalid email address.")
    if require_configured_admin and not configured_emails:
        raise RuntimeError(
            "ADMIN_EMAILS is required in production. Set the real administrator email privately."
        )
    if require_configured_admin and "admin@uts.local" in configured_emails:
        raise RuntimeError(
            "ADMIN_EMAILS cannot use the public development address admin@uts.local in production."
        )
    return configured_emails


def is_configured_admin_email(email: str) -> bool:
    return normalize_email(email) in get_admin_emails()


def is_user_admin(user: dict[str, Any] | None) -> bool:
    if not user:
        return False
    # Authorization is based on the current database flag. ADMIN_EMAILS is used
    # only to provision that flag during registration/login, never as a request-time bypass.
    return bool(user.get("is_admin"))


def serialize_user(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": user["id"],
        "full_name": user["full_name"],
        "email": user["email"],
        "email_verified_at": user.get("email_verified_at"),
        "created_at": user.get("created_at"),
        "is_admin": is_user_admin(user),
    }


def save_login_session(user: dict[str, Any]) -> None:
    session.clear()
    # A permanent signed session survives closing and reopening the browser.
    # Flask still expires it according to PERMANENT_SESSION_LIFETIME.
    session.permanent = True
    session["user_id"] = int(user["id"])
    session["email"] = normalize_email(str(user.get("email") or ""))
    session["is_admin"] = is_user_admin(user)


def clear_login_session() -> None:
    session.clear()


def is_valid_email(email: str) -> bool:
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email))


def utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def normalize_pin(pin: str) -> str:
    return re.sub(r"\D+", "", str(pin or ""))


def normalize_pin_purpose(purpose: str) -> str:
    normalized = normalize_match_text(purpose).replace(" ", "_")
    aliases = {
        "signup": "register",
        "sign_up": "register",
        "create_account": "register",
        "forgot_password": "password_reset",
        "reset_password": "password_reset",
    }
    return aliases.get(normalized, normalized)


def get_password_validation_error(password: str) -> str:
    if len(password) < 8:
        return "Password must be at least 8 characters."
    if not re.search(r"[A-Za-z]", password):
        return "Password must include at least 1 letter."
    if not re.search(r"\d", password):
        return "Password must include at least 1 number."
    if not SPECIAL_CHARACTER_PATTERN.search(password):
        return "Password must include at least 1 special character, for example !, @, #, or $."
    return ""

"""Central configuration values shared by the Flask application."""

from __future__ import annotations

import os
import re
import secrets
from urllib.parse import urlsplit


def get_bounded_integer_environment(
    name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    raw_value = os.getenv(name, str(default)).strip()
    try:
        value = int(raw_value)
    except ValueError as error:
        raise RuntimeError(f"{name} must be an integer.") from error
    if value < minimum or value > maximum:
        raise RuntimeError(f"{name} must be between {minimum} and {maximum}.")
    return value


def get_allowed_cors_origins() -> tuple[str, ...]:
    """Parse an explicit allow-list; cross-origin browser access is off by default."""
    raw_origins = os.getenv("CORS_ALLOWED_ORIGINS", "").strip()
    if not raw_origins:
        return ()

    origins: list[str] = []
    for raw_origin in raw_origins.split(","):
        origin = raw_origin.strip().rstrip("/")
        parsed = urlsplit(origin)
        if (
            not origin
            or origin == "*"
            or parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise RuntimeError(
                "CORS_ALLOWED_ORIGINS must contain comma-separated http(s) origins without paths."
            )
        if origin not in origins:
            origins.append(origin)
    return tuple(origins)


APP_TITLE = "UTS CS Career Navigator Resume Analyzer"
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")

ACCURACY_TESTING_WORKBOOK = os.getenv(
    "ACCURACY_TESTING_WORKBOOK",
    os.path.join(
        BASE_DIR,
        "outputs",
        "accuracy_evaluation",
        "ats_resume_fyp_testing.xlsx",
    ),
)
ACCURACY_TESTING_PENDING_FILE = os.getenv(
    "ACCURACY_TESTING_PENDING_FILE",
    os.path.join(BASE_DIR, "outputs", "accuracy_evaluation", "accuracy_testing_pending.jsonl"),
)

RESUME_GENERATION_MAX_TEXT_LENGTH = 12000
CS_SAMPLE_ONLY_TOP_MATCH_THRESHOLD = 20.0
PDF_EXTENSIONS = {".pdf"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg"}
SUPPORTED_RESUME_EXTENSIONS = PDF_EXTENSIONS | IMAGE_EXTENSIONS

EMAIL_PIN_LENGTH = 6
EMAIL_PIN_TTL_MINUTES = get_bounded_integer_environment("EMAIL_PIN_TTL_MINUTES", 10, 1, 60)
EMAIL_PIN_MAX_ATTEMPTS = get_bounded_integer_environment("EMAIL_PIN_MAX_ATTEMPTS", 5, 1, 10)
EMAIL_PIN_REQUEST_LIMIT_PER_EMAIL = get_bounded_integer_environment(
    "EMAIL_PIN_REQUEST_LIMIT_PER_EMAIL", 3, 1, 20
)
EMAIL_PIN_REQUEST_LIMIT_PER_IP = get_bounded_integer_environment(
    "EMAIL_PIN_REQUEST_LIMIT_PER_IP", 10, 1, 100
)
EMAIL_PIN_REQUEST_WINDOW_MINUTES = get_bounded_integer_environment(
    "EMAIL_PIN_REQUEST_WINDOW_MINUTES", 15, 1, 1440
)
EMAIL_PIN_ATTEMPT_LIMIT_PER_IP = get_bounded_integer_environment(
    "EMAIL_PIN_ATTEMPT_LIMIT_PER_IP", 20, 1, 200
)
EMAIL_PIN_RATE_WINDOW_MINUTES = get_bounded_integer_environment(
    "EMAIL_PIN_RATE_WINDOW_MINUTES", 15, 1, 1440
)
LOGIN_REQUEST_LIMIT_PER_IP = get_bounded_integer_environment(
    "LOGIN_REQUEST_LIMIT_PER_IP", 10, 1, 1000
)
LOGIN_REQUEST_LIMIT_PER_EMAIL = get_bounded_integer_environment(
    "LOGIN_REQUEST_LIMIT_PER_EMAIL", 10, 1, 1000
)
LOGIN_RATE_WINDOW_MINUTES = get_bounded_integer_environment(
    "LOGIN_RATE_WINDOW_MINUTES", 15, 1, 1440
)
ANALYSIS_REQUEST_LIMIT_PER_IP = get_bounded_integer_environment(
    "ANALYSIS_REQUEST_LIMIT_PER_IP", 20, 1, 1000
)
ANALYSIS_RATE_WINDOW_MINUTES = get_bounded_integer_environment(
    "ANALYSIS_RATE_WINDOW_MINUTES", 15, 1, 1440
)
SESSION_LIFETIME_DAYS = get_bounded_integer_environment(
    "SESSION_LIFETIME_DAYS", 7, 1, 30
)
MAX_RESUME_UPLOAD_BYTES = get_bounded_integer_environment(
    "MAX_RESUME_UPLOAD_BYTES", 10 * 1024 * 1024, 1024 * 1024, 50 * 1024 * 1024
)
MAX_FORM_MEMORY_BYTES = get_bounded_integer_environment(
    "MAX_FORM_MEMORY_BYTES", 500_000, 64 * 1024, 2 * 1024 * 1024
)
MAX_FORM_PARTS = get_bounded_integer_environment("MAX_FORM_PARTS", 32, 1, 1000)
EMAIL_PIN_PURPOSES = {"register", "password_reset"}
TRUSTED_PROXY_COUNT = get_bounded_integer_environment("TRUSTED_PROXY_COUNT", 0, 0, 10)
SPECIAL_CHARACTER_PATTERN = re.compile(r"[^A-Za-z0-9]")

MIN_FLASK_SECRET_KEY_LENGTH = 32
NON_PRODUCTION_ENVIRONMENTS = {"development", "dev", "local", "test", "testing"}
INSECURE_FLASK_SECRET_KEYS = {
    "uts-career-navigator-dev-secret",
    "change_this_to_a_random_secret_for_sessions",
    "change-me",
    "replace_me",
    "replace_with_a_random_secret",
}

TRUE_ENVIRONMENT_VALUES = {"1", "true", "yes", "on"}
FALSE_ENVIRONMENT_VALUES = {"0", "false", "no", "off"}


class EmailPinConfigurationError(RuntimeError):
    """Raised when PIN delivery is unavailable or unsafe for this environment."""


def get_app_environment() -> str:
    """Return the deployment environment, defaulting to production for safety."""
    return (
        os.getenv("APP_ENV")
        or os.getenv("FLASK_ENV")
        or "production"
    ).strip().lower()


def is_production_environment() -> bool:
    return get_app_environment() not in NON_PRODUCTION_ENVIRONMENTS


CORS_ALLOWED_ORIGINS = get_allowed_cors_origins()


def get_accuracy_testing_auto_export_enabled() -> bool:
    """Allow workbook export only through an explicit non-production opt-in."""
    raw_value = os.getenv("ACCURACY_TESTING_AUTO_EXPORT", "0").strip().lower()
    if raw_value not in TRUE_ENVIRONMENT_VALUES | FALSE_ENVIRONMENT_VALUES:
        raise RuntimeError(
            "ACCURACY_TESTING_AUTO_EXPORT must be a true or false value."
        )
    return raw_value in TRUE_ENVIRONMENT_VALUES and not is_production_environment()


def email_pin_dev_mode_requested() -> bool:
    return os.getenv("EMAIL_PIN_DEV_MODE", "").strip().lower() in TRUE_ENVIRONMENT_VALUES


def get_email_pin_delivery_mode() -> str:
    """Resolve a complete PIN delivery configuration without exposing credentials."""
    development_pin_enabled = email_pin_dev_mode_requested()
    if is_production_environment() and development_pin_enabled:
        raise EmailPinConfigurationError(
            "EMAIL_PIN_DEV_MODE cannot be enabled in production. Configure a real email provider."
        )

    resend_api_key = os.getenv("RESEND_API_KEY", "").strip()
    resend_from = os.getenv("RESEND_FROM", "").strip()
    if resend_api_key or resend_from:
        if not resend_api_key or not resend_from:
            raise EmailPinConfigurationError(
                "Resend PIN delivery requires both RESEND_API_KEY and RESEND_FROM."
            )
        return "resend_api"

    smtp_host = os.getenv("SMTP_HOST", "").strip()
    smtp_from = os.getenv("SMTP_FROM", "").strip()
    smtp_username = os.getenv("SMTP_USERNAME", "").strip()
    smtp_password = os.getenv("SMTP_PASSWORD", "")
    if smtp_host or smtp_from or smtp_username or smtp_password:
        if not smtp_host or not smtp_from:
            raise EmailPinConfigurationError(
                "SMTP PIN delivery requires both SMTP_HOST and SMTP_FROM."
            )
        if bool(smtp_username) != bool(smtp_password):
            raise EmailPinConfigurationError(
                "SMTP_USERNAME and SMTP_PASSWORD must either both be set or both be omitted."
            )
        get_bounded_integer_environment("SMTP_PORT", 587, 1, 65535)
        smtp_tls_value = os.getenv("SMTP_USE_TLS", "1").strip().lower()
        if smtp_tls_value not in TRUE_ENVIRONMENT_VALUES | FALSE_ENVIRONMENT_VALUES:
            raise EmailPinConfigurationError(
                "SMTP_USE_TLS must be a true or false value."
            )
        if is_production_environment() and smtp_tls_value not in TRUE_ENVIRONMENT_VALUES:
            raise EmailPinConfigurationError(
                "SMTP_USE_TLS must be enabled for production PIN delivery."
            )
        return "smtp"

    if development_pin_enabled and not is_production_environment():
        return "dev"

    if is_production_environment():
        raise EmailPinConfigurationError(
            "Production PIN delivery requires a configured Resend or SMTP email provider."
        )

    return "unavailable"


def get_flask_secret_key() -> str:
    """Load a strong session key, failing closed outside explicit local/test modes."""
    secret_key = os.getenv("FLASK_SECRET_KEY", "").strip()

    if secret_key:
        if secret_key.lower() in INSECURE_FLASK_SECRET_KEYS:
            raise RuntimeError(
                "FLASK_SECRET_KEY uses a known insecure placeholder. Generate a new random value."
            )
        if len(secret_key) < MIN_FLASK_SECRET_KEY_LENGTH:
            raise RuntimeError(
                f"FLASK_SECRET_KEY must contain at least {MIN_FLASK_SECRET_KEY_LENGTH} characters."
            )
        return secret_key

    if not is_production_environment():
        # Local/test sessions may reset on restart. Production must provide a stable key.
        return secrets.token_urlsafe(48)

    raise RuntimeError(
        "FLASK_SECRET_KEY is required in production. "
        "Set it to a long random value in the hosting environment before starting the application."
    )

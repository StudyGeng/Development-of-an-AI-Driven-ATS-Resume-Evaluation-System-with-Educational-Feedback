from __future__ import annotations

import importlib
import io
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import app_config  # noqa: E402


with patch.dict(
    os.environ,
    {
        "APP_ENV": "testing",
        "FLASK_SECRET_KEY": "email-pin-tests-only-secret-key-value-1234567890",
    },
    clear=False,
):
    app_module = importlib.import_module("app")

import security  # noqa: E402


PIN = "481205"
PIN_EXPIRY = datetime(2026, 9, 10, 12, 0, 0)


def production_email_environment(**overrides: str) -> dict[str, str]:
    environment = {
        "APP_ENV": "production",
        "FLASK_SECRET_KEY": "email-pin-tests-only-production-secret-1234567890",
        "RESEND_API_KEY": "re_email_pin_security_test_key_not_real",
        "RESEND_FROM": "Career Navigator <security-tests@example.com>",
        "EMAIL_PIN_DEV_MODE": "0",
    }
    environment.update(overrides)
    return environment


class RateLimitEventCursor:
    def __init__(self, event_store: "RateLimitEventStore"):
        self.event_store = event_store
        self.result: list[dict] = []
        self.closed = False

    def execute(self, query: str, params=()):
        normalized_query = " ".join(query.lower().split())
        safe_params = tuple(params)
        self.event_store.queries.append((normalized_query, safe_params))

        if normalized_query.startswith("select id, created_at from auth_rate_limit_events"):
            event_type, subject_hash, window_start = safe_params
            self.result = [
                dict(event)
                for event in self.event_store.events
                if event["event_type"] == event_type
                and event["subject_hash"] == subject_hash
                and event["created_at"] >= window_start
            ]
            self.result.sort(key=lambda event: (event["created_at"], event["id"]))
            return

        if normalized_query.startswith("insert into auth_rate_limit_events"):
            event_type, subject_hash, created_at = safe_params
            self.event_store.events.append(
                {
                    "id": len(self.event_store.events) + 1,
                    "event_type": event_type,
                    "subject_hash": subject_hash,
                    "created_at": created_at,
                }
            )
            self.result = []
            return

        if normalized_query.startswith("delete from auth_rate_limit_events"):
            (cutoff,) = safe_params
            self.event_store.events[:] = [
                event for event in self.event_store.events if event["created_at"] >= cutoff
            ]
            self.result = []
            return

        raise AssertionError(f"Unexpected rate-limit query: {normalized_query}")

    def fetchall(self):
        return list(self.result)

    def close(self):
        self.closed = True


class RateLimitEventConnection:
    def __init__(self, event_store: "RateLimitEventStore"):
        self.event_store = event_store
        self.cursor_instance = RateLimitEventCursor(event_store)
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self, dictionary=False):
        if not dictionary:
            raise AssertionError("Rate-limit queries must use dictionary rows")
        return self.cursor_instance

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class RateLimitEventStore:
    def __init__(self):
        self.events: list[dict] = []
        self.connections: list[RateLimitEventConnection] = []
        self.queries: list[tuple[str, tuple]] = []

    def __call__(self):
        connection = RateLimitEventConnection(self)
        self.connections.append(connection)
        return connection


class ActivePinCursor:
    def __init__(self, pin_store: "ActivePinStore"):
        self.pin_store = pin_store
        self.result = None
        self.queries: list[tuple[str, tuple]] = []
        self.closed = False

    def execute(self, query: str, params=()):
        normalized_query = " ".join(query.lower().split())
        safe_params = tuple(params)
        self.queries.append((normalized_query, safe_params))
        self.pin_store.queries.append((normalized_query, safe_params))

        if normalized_query.startswith(
            "select id, pin_hash, attempts, expires_at from email_verification_pins"
        ):
            self.result = None if self.pin_store.consumed else {
                "id": 501,
                "pin_hash": "irrelevant-when-password-check-is-mocked",
                "attempts": self.pin_store.attempts,
                "expires_at": datetime(2099, 1, 1, 0, 0, 0),
            }
            return

        if normalized_query.startswith("update email_verification_pins set attempts = %s"):
            self.pin_store.attempts = int(safe_params[0])
            if "consumed_at" in normalized_query:
                self.pin_store.consumed = True
            self.result = None
            return

        if normalized_query.startswith(
            "update email_verification_pins set consumed_at = utc_timestamp()"
        ):
            self.pin_store.consumed = True
            self.result = None
            return

        raise AssertionError(f"Unexpected active-PIN query: {normalized_query}")

    def fetchone(self):
        return self.result

    def close(self):
        self.closed = True


class ActivePinConnection:
    def __init__(self, pin_store: "ActivePinStore"):
        self.cursor_instance = ActivePinCursor(pin_store)
        self.commits = 0
        self.rolled_back = False
        self.closed = False

    def cursor(self, dictionary=False):
        if not dictionary:
            raise AssertionError("PIN verification must use dictionary rows")
        return self.cursor_instance

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class ActivePinStore:
    def __init__(self):
        self.attempts = 0
        self.consumed = False
        self.connections: list[ActivePinConnection] = []
        self.queries: list[tuple[str, tuple]] = []

    def __call__(self):
        connection = ActivePinConnection(self)
        self.connections.append(connection)
        return connection


class EmailPinProductionSecurityTests(unittest.TestCase):
    def setUp(self):
        self.previous_environment = app_module.app.config.get("APP_ENV")
        app_module.app.config.update(
            TESTING=True,
            SECRET_KEY="email-pin-route-test-secret",
            APP_ENV="production",
        )
        self.client = app_module.app.test_client()

    def tearDown(self):
        app_module.app.config["APP_ENV"] = self.previous_environment

    def post_pin(self, email: str, *, remote_addr: str = "198.51.100.40"):
        return self.client.post(
            "/auth/pin/send",
            json={"email": email, "purpose": "register"},
            environ_base={"REMOTE_ADDR": remote_addr},
        )

    def test_production_success_omits_pin_and_delivery_metadata(self):
        environment = production_email_environment(EMAIL_PIN_DEV_MODE="0")
        with patch.dict(os.environ, environment, clear=True), patch.object(
            app_module, "enforce_email_pin_rate_limits", return_value=None
        ), patch.object(
            app_module, "email_exists", return_value=False
        ), patch.object(
            app_module, "create_email_pin", return_value=(PIN, PIN_EXPIRY, 101)
        ), patch.object(
            app_module, "send_email_pin", return_value="resend_api"
        ):
            response = self.post_pin("production-success@example.com")

        self.assertEqual(response.status_code, 200, response.get_json())
        body = response.get_json()
        self.assertNotIn("dev_pin", body)
        self.assertNotIn("delivery", body)
        self.assertNotIn("provider", body)
        self.assertNotIn(PIN, response.get_data(as_text=True))

    def test_production_dev_delivery_marker_still_never_returns_dev_pin(self):
        environment = production_email_environment(EMAIL_PIN_DEV_MODE="0")
        with patch.dict(os.environ, environment, clear=True), patch.object(
            app_module, "enforce_email_pin_rate_limits", return_value=None
        ), patch.object(
            app_module, "email_exists", return_value=False
        ), patch.object(
            app_module, "create_email_pin", return_value=(PIN, PIN_EXPIRY, 102)
        ), patch.object(
            app_module, "send_email_pin", return_value="dev"
        ):
            response = self.post_pin("production-dev-marker@example.com")

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertNotIn("dev_pin", response.get_json())
        self.assertNotIn(PIN, response.get_data(as_text=True))

    def test_delivery_failure_does_not_echo_or_log_pin(self):
        environment = production_email_environment()
        captured_stdout = io.StringIO()
        captured_stderr = io.StringIO()

        with patch.dict(os.environ, environment, clear=True), patch.object(
            app_module, "enforce_email_pin_rate_limits", return_value=None
        ), patch.object(
            app_module, "email_exists", return_value=False
        ), patch.object(
            app_module, "create_email_pin", return_value=(PIN, PIN_EXPIRY, 103)
        ), patch.object(
            app_module,
            "send_email_pin",
            side_effect=OSError(f"provider rejected secret PIN {PIN}"),
        ), patch.object(
            app_module, "invalidate_email_pin", return_value=True
        ) as invalidate_pin, redirect_stdout(captured_stdout), redirect_stderr(captured_stderr):
            response = self.post_pin("delivery-failure@example.com")

        self.assertEqual(response.status_code, 502, response.get_json())
        body = response.get_json()
        self.assertNotIn("dev_pin", body)
        self.assertNotIn(PIN, response.get_data(as_text=True))
        self.assertNotIn(PIN, captured_stdout.getvalue())
        self.assertNotIn(PIN, captured_stderr.getvalue())
        invalidate_pin.assert_called_once_with(103)

    def test_explicit_development_mode_returns_dev_pin_without_printing_it(self):
        environment = {
            "APP_ENV": "development",
            "FLASK_SECRET_KEY": "email-pin-development-test-secret-key-1234567890",
            "EMAIL_PIN_DEV_MODE": "1",
        }
        captured_stdout = io.StringIO()
        captured_stderr = io.StringIO()

        with patch.dict(os.environ, environment, clear=True), patch.object(
            app_module, "enforce_email_pin_rate_limits", return_value=None
        ), patch.object(
            app_module, "email_exists", return_value=False
        ), patch.object(
            app_module, "create_email_pin", return_value=(PIN, PIN_EXPIRY, 104)
        ), redirect_stdout(captured_stdout), redirect_stderr(captured_stderr):
            response = self.post_pin("explicit-local-development@example.com")

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json().get("dev_pin"), PIN)
        self.assertEqual(captured_stdout.getvalue(), "")
        self.assertEqual(captured_stderr.getvalue(), "")


class EmailPinProductionStartupTests(unittest.TestCase):
    def run_app_import(self, **overrides: str) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment.update(
            {
                "APP_ENV": "production",
                "FLASK_SECRET_KEY": "email-pin-startup-test-secret-key-value-1234567890",
                "ADMIN_EMAILS": "security-tests@example.com",
                "ADMIN_EMAIL": "",
                "RESEND_API_KEY": "re_email_pin_startup_test_key_not_real",
                "RESEND_FROM": "Career Navigator <security-tests@example.com>",
                "SMTP_HOST": "",
                "EMAIL_PIN_DEV_MODE": "0",
            }
        )
        environment.update(overrides)
        import_script = "\n".join(
            (
                "import app",
            )
        )
        return subprocess.run(
            [sys.executable, "-c", import_script],
            cwd=BACKEND_DIR,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    def test_production_startup_rejects_missing_email_provider(self):
        result = self.run_app_import(RESEND_API_KEY="", RESEND_FROM="", SMTP_HOST="")

        self.assertNotEqual(result.returncode, 0)
        error_output = (result.stdout + result.stderr).lower()
        self.assertIn("email", error_output)
        self.assertTrue(
            "provider" in error_output or "resend" in error_output or "smtp" in error_output,
            error_output,
        )
        self.assertNotIn(PIN, error_output)

    def test_production_startup_rejects_missing_administrator_configuration(self):
        result = self.run_app_import(ADMIN_EMAILS="", ADMIN_EMAIL="")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("admin", (result.stdout + result.stderr).lower())

    def test_production_startup_rejects_development_pin_mode(self):
        result = self.run_app_import(EMAIL_PIN_DEV_MODE="1")

        self.assertNotEqual(result.returncode, 0)
        error_output = (result.stdout + result.stderr).lower()
        self.assertIn("email_pin_dev_mode", error_output)
        self.assertNotIn(PIN, error_output)


class DurableEmailPinRateLimitTests(unittest.TestCase):
    def setUp(self):
        app_module.app.config.update(TESTING=True, SECRET_KEY="rate-limit-test-secret")
        self.event_store = RateLimitEventStore()
        self.database_patch = patch.object(
            app_module,
            "get_db_connection",
            self.event_store,
        )
        self.database_patch.start()

    def tearDown(self):
        self.database_patch.stop()

    def test_fourth_request_for_same_normalized_email_and_purpose_is_blocked(self):
        with patch.multiple(
            app_module,
            EMAIL_PIN_REQUEST_LIMIT_PER_EMAIL=3,
            EMAIL_PIN_REQUEST_LIMIT_PER_IP=10,
            EMAIL_PIN_REQUEST_WINDOW_MINUTES=15,
            EMAIL_PIN_RATE_WINDOW_MINUTES=15,
        ):
            app_module.enforce_email_pin_rate_limits(
                "request",
                " RateLimit@Example.com ",
                "198.51.100.50",
                purpose="signup",
            )
            for _ in range(2):
                app_module.enforce_email_pin_rate_limits(
                    "request",
                    "ratelimit@example.com",
                    "198.51.100.50",
                    purpose="register",
                )

            with self.assertRaises(app_module.EmailPinRateLimitError) as blocked:
                app_module.enforce_email_pin_rate_limits(
                    "request",
                    "RATELIMIT@example.com",
                    "198.51.100.50",
                    purpose="register",
                )

        self.assertGreaterEqual(blocked.exception.retry_after_seconds, 1)
        self.assertLessEqual(blocked.exception.retry_after_seconds, 15 * 60 + 1)
        self.assertIn("requests", blocked.exception.detail.lower())
        self.assertEqual(len(self.event_store.events), 6)
        self.assertTrue(self.event_store.connections[-1].rolled_back)
        self.assertTrue(self.event_store.connections[-1].cursor_instance.closed)
        self.assertTrue(self.event_store.connections[-1].closed)

        select_queries = [
            query
            for query, _ in self.event_store.queries
            if query.startswith("select id, created_at from auth_rate_limit_events")
        ]
        self.assertTrue(select_queries)
        self.assertTrue(all("for update" in query for query in select_queries))
        for event in self.event_store.events:
            self.assertEqual(len(event["subject_hash"]), 64)
            self.assertNotIn("ratelimit@example.com", event["subject_hash"])
            self.assertNotIn("198.51.100.50", event["subject_hash"])

    def test_eleventh_request_from_same_ip_is_blocked_across_email_addresses(self):
        with patch.multiple(
            app_module,
            EMAIL_PIN_REQUEST_LIMIT_PER_EMAIL=3,
            EMAIL_PIN_REQUEST_LIMIT_PER_IP=10,
            EMAIL_PIN_REQUEST_WINDOW_MINUTES=15,
            EMAIL_PIN_RATE_WINDOW_MINUTES=15,
        ):
            for index in range(10):
                app_module.enforce_email_pin_rate_limits(
                    "request",
                    f"different-{index}@example.com",
                    "203.0.113.70",
                    purpose="register",
                )

            with self.assertRaises(app_module.EmailPinRateLimitError) as blocked:
                app_module.enforce_email_pin_rate_limits(
                    "request",
                    "different-10@example.com",
                    "203.0.113.70",
                    purpose="register",
                )

        self.assertIn("requests", blocked.exception.detail.lower())
        self.assertEqual(len(self.event_store.events), 20)

    def test_twenty_first_verification_attempt_from_same_ip_is_blocked(self):
        with patch.multiple(
            app_module,
            EMAIL_PIN_ATTEMPT_LIMIT_PER_IP=20,
            EMAIL_PIN_RATE_WINDOW_MINUTES=15,
            EMAIL_PIN_REQUEST_WINDOW_MINUTES=15,
        ):
            for index in range(20):
                app_module.enforce_email_pin_rate_limits(
                    "attempt",
                    f"attempt-{index}@example.com",
                    "192.0.2.80",
                    purpose="register",
                )

            with self.assertRaises(app_module.EmailPinRateLimitError) as blocked:
                app_module.enforce_email_pin_rate_limits(
                    "attempt",
                    "attempt-20@example.com",
                    "192.0.2.80",
                    purpose="register",
                )

        self.assertIn("attempts", blocked.exception.detail.lower())
        self.assertEqual(len(self.event_store.events), 20)


class PerPinAttemptLimitTests(unittest.TestCase):
    def setUp(self):
        app_module.app.config.update(TESTING=True, SECRET_KEY="pin-attempt-test-secret")
        self.client = app_module.app.test_client()
        self.pin_store = ActivePinStore()

    def test_fifth_wrong_pin_is_rate_limited_and_atomically_consumed(self):
        payload = {
            "full_name": "PIN Attempt Test",
            "email": "pin-attempt-limit@example.com",
            "password": "Strong!123",
            "verification_pin": "000000",
        }
        with patch.object(
            app_module, "get_db_connection", self.pin_store
        ), patch.object(
            app_module, "email_exists", return_value=False
        ), patch.object(
            app_module, "enforce_email_pin_rate_limits", return_value=None
        ), patch.object(
            app_module, "check_password_hash", return_value=False
        ), patch.multiple(
            app_module,
            EMAIL_PIN_MAX_ATTEMPTS=5,
            EMAIL_PIN_RATE_WINDOW_MINUTES=15,
        ):
            first_four = [
                self.client.post("/users/register", json=payload)
                for _ in range(4)
            ]
            fifth = self.client.post("/users/register", json=payload)

        self.assertTrue(all(response.status_code == 400 for response in first_four))
        self.assertTrue(
            all(response.get_json() == {"detail": "Invalid PIN."} for response in first_four)
        )
        self.assertEqual(fifth.status_code, 429, fifth.get_json())
        self.assertEqual(fifth.headers.get("Retry-After"), "900")
        self.assertIn("too many", fifth.get_json().get("detail", "").lower())
        self.assertEqual(self.pin_store.attempts, 5)
        self.assertTrue(self.pin_store.consumed)

        select_queries = [
            query
            for query, _ in self.pin_store.queries
            if query.startswith(
                "select id, pin_hash, attempts, expires_at from email_verification_pins"
            )
        ]
        self.assertEqual(len(select_queries), 5)
        self.assertTrue(all("for update" in query for query in select_queries))
        self.assertTrue(all(connection.commits == 1 for connection in self.pin_store.connections))
        self.assertTrue(
            all(connection.cursor_instance.closed for connection in self.pin_store.connections)
        )
        self.assertTrue(all(connection.closed for connection in self.pin_store.connections))


class EmailPinRateLimitRouteTests(unittest.TestCase):
    def setUp(self):
        app_module.app.config.update(TESTING=True, SECRET_KEY="rate-route-test-secret")
        self.client = app_module.app.test_client()

    def rate_limit_error(self):
        return app_module.EmailPinRateLimitError(
            "Too many PIN attempts. Please wait before trying again.",
            321,
        )

    def assert_rate_limited(self, response):
        self.assertEqual(response.status_code, 429, response.get_json())
        self.assertEqual(response.headers.get("Retry-After"), "321")
        body = response.get_json()
        self.assertEqual(body.get("retry_after_seconds"), 321)
        self.assertNotIn("dev_pin", body)
        self.assertNotIn(PIN, response.get_data(as_text=True))

    def test_pin_send_route_returns_429_before_creating_or_sending_a_pin(self):
        with patch.object(
            app_module,
            "get_email_pin_delivery_mode",
            return_value="resend_api",
        ), patch.object(
            app_module,
            "enforce_email_pin_rate_limits",
            side_effect=self.rate_limit_error(),
        ), patch.object(app_module, "email_exists") as email_exists, patch.object(
            app_module, "create_email_pin"
        ) as create_pin, patch.object(app_module, "send_email_pin") as send_pin:
            response = self.client.post(
                "/auth/pin/send",
                json={"email": "Rate-Route@Example.com", "purpose": "register"},
                environ_base={"REMOTE_ADDR": "198.51.100.81"},
            )

        self.assert_rate_limited(response)
        email_exists.assert_not_called()
        create_pin.assert_not_called()
        send_pin.assert_not_called()

    def test_pin_send_route_rejects_missing_provider_before_pin_storage(self):
        captured_stdout = io.StringIO()
        captured_stderr = io.StringIO()
        with patch.object(
            app_module,
            "get_email_pin_delivery_mode",
            side_effect=app_config.EmailPinConfigurationError("provider missing"),
        ), patch.object(
            app_module, "enforce_email_pin_rate_limits"
        ) as enforce_limits, patch.object(
            app_module, "email_exists"
        ) as email_exists, patch.object(
            app_module, "create_email_pin"
        ) as create_pin, patch.object(
            app_module, "send_email_pin"
        ) as send_pin, redirect_stdout(captured_stdout), redirect_stderr(captured_stderr):
            response = self.client.post(
                "/auth/pin/send",
                json={"email": "missing-provider-route@example.com", "purpose": "register"},
            )

        self.assertEqual(response.status_code, 503, response.get_json())
        self.assertNotIn("dev_pin", response.get_json())
        self.assertNotIn(PIN, response.get_data(as_text=True))
        self.assertEqual(captured_stdout.getvalue(), "")
        self.assertEqual(captured_stderr.getvalue(), "")
        enforce_limits.assert_not_called()
        email_exists.assert_not_called()
        create_pin.assert_not_called()
        send_pin.assert_not_called()

    def test_registration_route_returns_429_before_checking_or_consuming_pin(self):
        with patch.object(
            app_module,
            "enforce_email_pin_rate_limits",
            side_effect=self.rate_limit_error(),
        ), patch.object(app_module, "email_exists", return_value=False) as email_exists, patch.object(
            app_module, "consume_email_pin"
        ) as consume_pin:
            response = self.client.post(
                "/users/register",
                json={
                    "full_name": "Rate Limit Test",
                    "email": "register-rate@example.com",
                    "password": "Strong!123",
                    "verification_pin": "000000",
                },
                environ_base={"REMOTE_ADDR": "198.51.100.82"},
            )

        self.assert_rate_limited(response)
        email_exists.assert_called_once_with("register-rate@example.com")
        consume_pin.assert_not_called()

    def test_password_reset_route_returns_429_before_checking_or_consuming_pin(self):
        with patch.object(
            app_module,
            "enforce_email_pin_rate_limits",
            side_effect=self.rate_limit_error(),
        ), patch.object(app_module, "email_exists", return_value=True) as email_exists, patch.object(
            app_module, "consume_email_pin"
        ) as consume_pin:
            response = self.client.post(
                "/users/password-reset",
                json={
                    "email": "reset-rate@example.com",
                    "new_password": "Strong!123",
                    "verification_pin": "000000",
                },
                environ_base={"REMOTE_ADDR": "198.51.100.83"},
            )

        self.assert_rate_limited(response)
        email_exists.assert_called_once_with("reset-rate@example.com")
        consume_pin.assert_not_called()


class ExplicitAdministratorConfigurationTests(unittest.TestCase):
    def test_no_default_administrator_email_exists(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(security.get_admin_emails(), set())
            self.assertFalse(security.is_configured_admin_email("admin@uts.local"))

    def test_blank_administrator_variables_do_not_create_an_admin(self):
        with patch.dict(
            os.environ,
            {"ADMIN_EMAILS": "  ", "ADMIN_EMAIL": "  "},
            clear=True,
        ):
            self.assertEqual(security.get_admin_emails(), set())

    def test_explicit_private_administrator_emails_are_normalized(self):
        with patch.dict(
            os.environ,
            {
                "ADMIN_EMAILS": " Owner@Example.com ; security@example.com ",
            },
            clear=True,
        ):
            self.assertEqual(
                security.get_admin_emails(),
                {"owner@example.com", "security@example.com"},
            )
            self.assertTrue(security.is_configured_admin_email("OWNER@example.com"))

    def test_public_development_admin_address_is_rejected_for_production(self):
        with patch.dict(
            os.environ,
            {"ADMIN_EMAILS": "admin@uts.local"},
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "ADMIN_EMAILS"):
                security.validate_admin_email_configuration(require_configured_admin=True)


class EmailPinDeliveryConfigurationTests(unittest.TestCase):
    def test_production_requires_a_real_email_provider(self):
        with patch.dict(
            os.environ,
            {
                "APP_ENV": "production",
                "RESEND_API_KEY": "",
                "RESEND_FROM": "",
                "SMTP_HOST": "",
                "SMTP_FROM": "",
                "SMTP_USERNAME": "",
                "SMTP_PASSWORD": "",
                "EMAIL_PIN_DEV_MODE": "0",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(app_config.EmailPinConfigurationError, "provider"):
                app_config.get_email_pin_delivery_mode()

    def test_production_rejects_development_pin_mode_even_with_provider(self):
        with patch.dict(
            os.environ,
            production_email_environment(EMAIL_PIN_DEV_MODE="true"),
            clear=True,
        ):
            with self.assertRaisesRegex(
                app_config.EmailPinConfigurationError,
                "EMAIL_PIN_DEV_MODE",
            ):
                app_config.get_email_pin_delivery_mode()

    def test_development_pin_mode_requires_an_explicit_opt_in(self):
        with patch.dict(
            os.environ,
            {"APP_ENV": "development", "EMAIL_PIN_DEV_MODE": "0"},
            clear=True,
        ):
            self.assertEqual(app_config.get_email_pin_delivery_mode(), "unavailable")

        with patch.dict(
            os.environ,
            {"APP_ENV": "development", "EMAIL_PIN_DEV_MODE": "1"},
            clear=True,
        ):
            self.assertEqual(app_config.get_email_pin_delivery_mode(), "dev")


if __name__ == "__main__":
    unittest.main()

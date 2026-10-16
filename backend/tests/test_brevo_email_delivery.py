from __future__ import annotations

import importlib
import io
import json
import os
import ssl
import sys
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from email.message import Message
from pathlib import Path
from unittest.mock import patch
from urllib.response import addinfourl


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import app_config  # noqa: E402


# These tests neither load local secrets nor contact a real email service/database.
with patch("dotenv.load_dotenv"), patch.dict(
    os.environ,
    {"APP_ENV": "testing", "FLASK_SECRET_KEY": "brevo-tests-only-session-secret-1234567890"},
    clear=True,
):
    app_module = importlib.import_module("app")


TEST_API_KEY = "brevo-tests-only-api-key-not-real"
TEST_PIN = "481205"


def brevo_environment(**overrides: str) -> dict[str, str]:
    environment = {
        "APP_ENV": "production",
        "EMAIL_PIN_DEV_MODE": "0",
        "BREVO_API_KEY": TEST_API_KEY,
        "BREVO_FROM": "sender@example.com",
    }
    environment.update(overrides)
    return environment


class BrevoConfigurationTests(unittest.TestCase):
    def test_complete_brevo_configuration_selects_https_delivery(self):
        with patch.dict(os.environ, brevo_environment(), clear=True):
            self.assertEqual(app_config.get_email_pin_delivery_mode(), "brevo_api")

    def test_missing_key_or_sender_is_rejected_without_smtp_fallback(self):
        for missing in ("BREVO_API_KEY", "BREVO_FROM"):
            with self.subTest(missing=missing), patch.dict(
                os.environ,
                brevo_environment(**{missing: ""}, SMTP_HOST="smtp.example.com", SMTP_FROM="sender@example.com"),
                clear=True,
            ):
                with self.assertRaisesRegex(app_config.EmailPinConfigurationError, "requires both"):
                    app_config.get_email_pin_delivery_mode()

    def test_sender_requires_a_plain_email_address(self):
        for sender in ("not-an-email", "Sender <sender@example.com>", "sender@example.com\nother@example.com"):
            with self.subTest(sender=sender), patch.dict(
                os.environ, brevo_environment(BREVO_FROM=sender), clear=True
            ):
                with self.assertRaisesRegex(app_config.EmailPinConfigurationError, "BREVO_FROM"):
                    app_config.get_email_pin_delivery_mode()

    def test_production_dev_pin_remains_forbidden_with_brevo(self):
        with patch.dict(os.environ, brevo_environment(EMAIL_PIN_DEV_MODE="1"), clear=True):
            with self.assertRaisesRegex(app_config.EmailPinConfigurationError, "EMAIL_PIN_DEV_MODE"):
                app_config.get_email_pin_delivery_mode()

    def test_existing_resend_configuration_keeps_precedence(self):
        with patch.dict(
            os.environ,
            brevo_environment(RESEND_API_KEY="resend-test-key", RESEND_FROM="sender@example.com"),
            clear=True,
        ):
            self.assertEqual(app_config.get_email_pin_delivery_mode(), "resend_api")


class BrevoRequestTests(unittest.TestCase):
    def test_registration_and_reset_use_brevo_contract_with_timeout(self):
        for purpose in ("register", "password_reset"):
            with self.subTest(purpose=purpose), patch.dict(
                os.environ, brevo_environment(BREVO_FROM_NAME="ATS Student Testing"), clear=True
            ), patch.object(urllib.request, "build_opener") as build_opener:
                opener = build_opener.return_value
                opener.open.return_value.__enter__.return_value.status = 201
                delivery = app_module.send_email_pin("student@example.com", TEST_PIN, purpose)

            self.assertEqual(delivery, "brevo_api")
            request = opener.open.call_args.args[0]
            self.assertEqual(request.full_url, "https://api.brevo.com/v3/smtp/email")
            self.assertEqual(request.get_method(), "POST")
            headers = {name.lower(): value for name, value in request.header_items()}
            self.assertEqual(headers["api-key"], TEST_API_KEY)
            self.assertEqual(headers["content-type"], "application/json")
            self.assertEqual(headers["accept"], "application/json")
            self.assertEqual(opener.open.call_args.kwargs, {"timeout": 10})
            self.assertEqual(
                json.loads(request.data),
                {
                    "sender": {"email": "sender@example.com", "name": "ATS Student Testing"},
                    "to": [{"email": "student@example.com"}],
                    "subject": app_module.email_pin_subject(purpose),
                    "textContent": app_module.email_pin_body(TEST_PIN, purpose),
                },
            )

    def test_sender_name_defaults_to_app_title(self):
        with patch.dict(os.environ, brevo_environment(), clear=True), patch.object(
            urllib.request, "build_opener"
        ) as build_opener:
            opener = build_opener.return_value
            opener.open.return_value.__enter__.return_value.status = 201
            app_module.send_email_pin("student@example.com", TEST_PIN, "register")

        payload = json.loads(opener.open.call_args.args[0].data)
        self.assertEqual(payload["sender"]["name"], app_config.APP_TITLE)

    def test_non_created_status_never_counts_as_success(self):
        for status in (200, 202, 302, 400, 401, 429, 500):
            with self.subTest(status=status), patch.dict(
                os.environ, brevo_environment(), clear=True
            ), patch.object(urllib.request, "build_opener") as build_opener:
                build_opener.return_value.open.return_value.__enter__.return_value.status = status
                with self.assertRaisesRegex(OSError, f"HTTP {status}"):
                    app_module.send_email_pin("student@example.com", TEST_PIN, "register")

    def test_http_error_does_not_expose_provider_body_or_reason(self):
        raw_secret_error = f"rejected {TEST_API_KEY} and PIN {TEST_PIN}"
        body = io.BytesIO(raw_secret_error.encode())
        provider_error = urllib.error.HTTPError(
            "https://api.brevo.com/v3/smtp/email", 401, raw_secret_error, {}, body
        )
        with patch.dict(os.environ, brevo_environment(), clear=True), patch.object(
            urllib.request, "build_opener"
        ) as build_opener:
            build_opener.return_value.open.side_effect = provider_error
            with self.assertRaises(OSError) as caught:
                app_module.send_email_pin("student@example.com", TEST_PIN, "register")

        self.assertNotIn(TEST_API_KEY, str(caught.exception))
        self.assertNotIn(TEST_PIN, str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)
        self.assertTrue(body.closed)

    def test_connection_failure_and_timeout_are_sanitized(self):
        for error_type in (urllib.error.URLError, TimeoutError):
            with self.subTest(error_type=error_type), patch.dict(
                os.environ, brevo_environment(), clear=True
            ), patch.object(urllib.request, "build_opener") as build_opener:
                build_opener.return_value.open.side_effect = error_type(f"{TEST_API_KEY} {TEST_PIN}")
                with self.assertRaises(OSError) as caught:
                    app_module.send_email_pin("student@example.com", TEST_PIN, "register")
                self.assertNotIn(TEST_API_KEY, str(caught.exception))
                self.assertNotIn(TEST_PIN, str(caught.exception))
                self.assertTrue(caught.exception.__suppress_context__)

    def test_redirect_never_sends_key_or_pin_to_another_endpoint(self):
        requests = []

        class FakeHttps(urllib.request.HTTPSHandler):
            def __init__(self):
                # The fake transport does not need the machine's certificate store.
                super().__init__(context=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT))

            def https_open(self, request):
                requests.append(request)
                headers = Message()
                headers["Location"] = "https://untrusted.example/collect"
                response = addinfourl(io.BytesIO(b""), headers, request.full_url, 302)
                response.msg = "Found"
                return response

        real_build_opener = urllib.request.build_opener
        fake_https = FakeHttps()
        with patch.dict(os.environ, brevo_environment(), clear=True), patch.object(
            urllib.request,
            "build_opener",
            side_effect=lambda *handlers: real_build_opener(fake_https, *handlers),
        ):
            with self.assertRaisesRegex(OSError, "HTTP 302"):
                app_module.send_email_pin("student@example.com", TEST_PIN, "register")

        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].full_url, "https://api.brevo.com/v3/smtp/email")


class BrevoPinRouteTests(unittest.TestCase):
    def setUp(self):
        self.config_patch = patch.dict(app_module.app.config, TESTING=True)
        self.config_patch.start()
        self.addCleanup(self.config_patch.stop)
        self.client = app_module.app.test_client()

    def test_missing_brevo_configuration_does_not_create_pin(self):
        with patch.dict(os.environ, brevo_environment(BREVO_FROM=""), clear=True), patch.object(
            app_module, "create_email_pin"
        ) as create_pin, patch.object(app_module, "send_email_pin") as send_pin:
            response = self.client.post(
                "/auth/pin/send", json={"email": "student@example.com", "purpose": "register"}
            )

        self.assertEqual(response.status_code, 503)
        create_pin.assert_not_called()
        send_pin.assert_not_called()

    def test_provider_failure_invalidates_pin_and_never_reports_success(self):
        for purpose in ("register", "password_reset"):
            stdout, stderr = io.StringIO(), io.StringIO()
            with self.subTest(purpose=purpose), patch.dict(
                os.environ, brevo_environment(), clear=True
            ), patch.object(app_module, "enforce_email_pin_rate_limits"), patch.object(
                app_module, "email_exists", return_value=purpose == "password_reset"
            ), patch.object(
                app_module, "create_email_pin", return_value=(TEST_PIN, datetime(2026, 10, 16), 123)
            ), patch.object(app_module, "invalidate_email_pin") as invalidate_pin, patch.object(
                urllib.request, "build_opener"
            ) as build_opener, redirect_stdout(stdout), redirect_stderr(stderr):
                build_opener.return_value.open.side_effect = urllib.error.URLError(
                    f"provider failure {TEST_API_KEY} PIN {TEST_PIN}"
                )
                response = self.client.post(
                    "/auth/pin/send", json={"email": "student@example.com", "purpose": purpose}
                )

            self.assertEqual(response.status_code, 502)
            invalidate_pin.assert_called_once_with(123)
            self.assertNotIn("message", response.get_json())
            output = response.get_data(as_text=True) + stdout.getvalue() + stderr.getvalue()
            self.assertNotIn(TEST_API_KEY, output)
            self.assertNotIn(TEST_PIN, output)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import importlib
import io
import os
import sys
import unittest
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
        "FLASK_SECRET_KEY": "web-protections-tests-only-secret-key-value-1234567890",
        "ACCURACY_TESTING_AUTO_EXPORT": "0",
    },
    clear=False,
):
    app_module = importlib.import_module("app")


class RateLimitCursor:
    def __init__(self, store: "RateLimitStore"):
        self.store = store
        self.result: list[dict] = []
        self.closed = False

    def execute(self, query: str, params=()):
        normalized_query = " ".join(query.lower().split())
        safe_params = tuple(params)

        if normalized_query.startswith("select id, created_at from auth_rate_limit_events"):
            event_type, subject_hash, window_start = safe_params
            self.result = sorted(
                (
                    dict(event)
                    for event in self.store.events
                    if event["event_type"] == event_type
                    and event["subject_hash"] == subject_hash
                    and event["created_at"] >= window_start
                ),
                key=lambda event: (event["created_at"], event["id"]),
            )
            return

        if normalized_query.startswith("insert into auth_rate_limit_events"):
            event_type, subject_hash, created_at = safe_params
            self.store.events.append(
                {
                    "id": len(self.store.events) + 1,
                    "event_type": event_type,
                    "subject_hash": subject_hash,
                    "created_at": created_at,
                }
            )
            self.result = []
            return

        if normalized_query.startswith("delete from auth_rate_limit_events"):
            (cutoff,) = safe_params
            self.store.events[:] = [
                event for event in self.store.events if event["created_at"] >= cutoff
            ]
            self.result = []
            return

        raise AssertionError(f"Unexpected rate-limit query: {normalized_query}")

    def fetchall(self):
        return list(self.result)

    def close(self):
        self.closed = True


class RateLimitConnection:
    def __init__(self, store: "RateLimitStore"):
        self.cursor_instance = RateLimitCursor(store)
        self.closed = False
        self.rolled_back = False

    def cursor(self, dictionary=False):
        if not dictionary:
            raise AssertionError("Rate-limit queries must request dictionary rows")
        return self.cursor_instance

    def commit(self):
        return None

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class RateLimitStore:
    def __init__(self):
        self.events: list[dict] = []

    def __call__(self):
        return RateLimitConnection(self)


class WebProtectionTests(unittest.TestCase):
    CONFIG_KEYS = (
        "TESTING",
        "ENFORCE_CSRF_IN_TESTS",
        "ENFORCE_RATE_LIMITS_IN_TESTS",
        "MAX_CONTENT_LENGTH",
    )

    def setUp(self):
        self.saved_config = {
            key: app_module.app.config.get(key) for key in self.CONFIG_KEYS
        }
        app_module.app.config.update(
            TESTING=True,
            ENFORCE_CSRF_IN_TESTS=False,
            ENFORCE_RATE_LIMITS_IN_TESTS=False,
        )
        self.client = app_module.app.test_client()

    def tearDown(self):
        app_module.app.config.update(self.saved_config)

    def csrf_headers(self, origin: str = "http://localhost") -> dict[str, str]:
        token_response = self.client.get("/auth/csrf-token")
        self.assertEqual(token_response.status_code, 200)
        return {
            "Origin": origin,
            "X-CSRF-Token": token_response.get_json()["csrf_token"],
        }

    def test_cross_origin_cors_is_disabled_by_default(self):
        response = self.client.get(
            "/health",
            headers={"Origin": "https://evil.example"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.headers.get("Access-Control-Allow-Origin"))
        self.assertIsNone(response.headers.get("Access-Control-Allow-Credentials"))

    def test_explicit_allow_list_emits_cors_only_for_that_origin(self):
        with patch.object(
            app_module,
            "CORS_ALLOWED_ORIGINS",
            ("https://frontend.example",),
        ):
            response = self.client.options(
                "/users/login",
                headers={"Origin": "https://frontend.example"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers.get("Access-Control-Allow-Origin"),
            "https://frontend.example",
        )
        self.assertEqual(response.headers.get("Access-Control-Allow-Credentials"), "true")
        self.assertIn("X-CSRF-Token", response.headers.get("Access-Control-Allow-Headers", ""))

    def test_csrf_blocks_missing_token_and_foreign_origin(self):
        app_module.app.config["ENFORCE_CSRF_IN_TESTS"] = True

        missing_token = self.client.post(
            "/users/logout",
            headers={"Origin": "http://localhost"},
        )
        self.assertEqual(missing_token.status_code, 403)
        self.assertEqual(missing_token.get_json(), {"detail": "CSRF validation failed."})

        foreign_origin = self.client.post(
            "/users/logout",
            headers=self.csrf_headers("https://evil.example"),
        )
        self.assertEqual(foreign_origin.status_code, 403)
        self.assertEqual(foreign_origin.get_json(), {"detail": "CSRF validation failed."})

    def test_csrf_accepts_current_origin_with_session_token(self):
        app_module.app.config["ENFORCE_CSRF_IN_TESTS"] = True

        response = self.client.post("/users/logout", headers=self.csrf_headers())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"message": "Logged out successfully"})

    def test_security_headers_are_added_to_all_responses(self):
        response = self.client.get("/health")

        self.assertIn("default-src 'self'", response.headers["Content-Security-Policy"])
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertEqual(
            response.headers["Referrer-Policy"],
            "strict-origin-when-cross-origin",
        )
        self.assertIn("camera=()", response.headers["Permissions-Policy"])

    def test_production_https_response_gets_hsts(self):
        with patch.dict(os.environ, {"APP_ENV": "production"}, clear=False):
            response = self.client.get("/health", base_url="https://resume.example")

        self.assertIn("max-age=31536000", response.headers["Strict-Transport-Security"])

    def test_upload_limit_returns_json_413_before_resume_processing(self):
        app_module.app.config["MAX_CONTENT_LENGTH"] = 128
        with patch.object(
            app_module,
            "get_authenticated_session_user",
            return_value={"id": 1},
        ):
            response = self.client.post(
                "/analyze-file",
                data={"resume_file": (io.BytesIO(b"x" * 2048), "resume.pdf")},
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 413)
        self.assertIn("must not exceed", response.get_json()["detail"])

    def test_login_and_analysis_routes_return_429_before_expensive_work(self):
        app_module.app.config["ENFORCE_RATE_LIMITS_IN_TESTS"] = True
        limit_error = app_module.RequestRateLimitError("Slow down", 45)
        with patch.object(
            app_module,
            "enforce_login_rate_limits",
            side_effect=limit_error,
        ), patch.object(app_module, "get_db_connection") as database_connection:
            login_response = self.client.post(
                "/users/login",
                json={"email": "person@example.com", "password": "Strong!123"},
            )

        self.assertEqual(login_response.status_code, 429)
        self.assertEqual(login_response.headers.get("Retry-After"), "45")
        database_connection.assert_not_called()

        with patch.object(
            app_module,
            "get_authenticated_session_user",
            return_value={"id": 1},
        ), patch.object(
            app_module,
            "enforce_analysis_rate_limit",
            side_effect=limit_error,
        ):
            analysis_response = self.client.post("/analyze-file", data={})

        self.assertEqual(analysis_response.status_code, 429)
        self.assertEqual(analysis_response.headers.get("Retry-After"), "45")

    def test_login_rate_limit_is_durable_and_hashes_subjects(self):
        store = RateLimitStore()
        with patch.object(app_module, "get_db_connection", store), patch.multiple(
            app_module,
            LOGIN_REQUEST_LIMIT_PER_IP=2,
            LOGIN_REQUEST_LIMIT_PER_EMAIL=2,
            LOGIN_RATE_WINDOW_MINUTES=15,
        ):
            app_module.enforce_login_rate_limits("person@example.com", "198.51.100.7")
            app_module.enforce_login_rate_limits("person@example.com", "198.51.100.7")
            with self.assertRaises(app_module.RequestRateLimitError) as blocked:
                app_module.enforce_login_rate_limits("person@example.com", "198.51.100.7")

        self.assertIn("login", blocked.exception.detail.lower())
        self.assertEqual(len(store.events), 4)
        self.assertTrue(all(len(event["subject_hash"]) == 64 for event in store.events))
        self.assertTrue(
            all("person@example.com" not in event["subject_hash"] for event in store.events)
        )


class WebProtectionConfigurationTests(unittest.TestCase):
    def test_cors_wildcard_is_rejected(self):
        with patch.dict(os.environ, {"CORS_ALLOWED_ORIGINS": "*"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "CORS_ALLOWED_ORIGINS"):
                app_config.get_allowed_cors_origins()

    def test_cors_origins_are_normalized_to_unique_explicit_values(self):
        with patch.dict(
            os.environ,
            {
                "CORS_ALLOWED_ORIGINS": (
                    "https://frontend.example, https://frontend.example/, http://localhost:3000"
                )
            },
            clear=True,
        ):
            self.assertEqual(
                app_config.get_allowed_cors_origins(),
                ("https://frontend.example", "http://localhost:3000"),
            )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import importlib
import os
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import database  # noqa: E402


with patch.dict(
    os.environ,
    {
        "APP_ENV": "testing",
        "FLASK_SECRET_KEY": "session-tests-only-secret-key-value-1234567890",
    },
    clear=False,
), patch.object(database, "init_mysql_database"):
    app_module = importlib.import_module("app")

from security import save_login_session  # noqa: E402


TEST_USER = {
    "id": 7,
    "full_name": "Persistent User",
    "email": "persistent@example.com",
    "email_verified_at": datetime(2026, 9, 1, 8, 30),
    "is_admin": 0,
    "created_at": datetime(2026, 8, 1, 9, 15),
}


class SessionCursor:
    def __init__(self, user):
        self.user = user
        self.closed = False

    def execute(self, query, params=()):
        normalized_query = " ".join(query.lower().split())
        if "from users" not in normalized_query or "where id = %s" not in normalized_query:
            raise AssertionError(f"Unexpected session query: {normalized_query}")
        requested_id = int(params[0])
        self.result = dict(self.user) if self.user and int(self.user["id"]) == requested_id else None

    def fetchone(self):
        return self.result

    def close(self):
        self.closed = True


class SessionConnection:
    def __init__(self, user):
        self.cursor_instance = SessionCursor(user)
        self.closed = False

    def cursor(self, dictionary=False):
        if not dictionary:
            raise AssertionError("Session lookup must use a dictionary cursor")
        return self.cursor_instance

    def close(self):
        self.closed = True


class SessionPersistenceTests(unittest.TestCase):
    def setUp(self):
        app_module.app.config.update(
            TESTING=True,
            SECRET_KEY="session-route-test-secret",
            PERMANENT_SESSION_LIFETIME=timedelta(days=7),
        )
        self.client = app_module.app.test_client()

    def test_login_session_is_marked_permanent(self):
        with app_module.app.test_request_context():
            save_login_session(TEST_USER)

            self.assertTrue(app_module.session.permanent)
            self.assertEqual(app_module.session["user_id"], TEST_USER["id"])

    def test_anonymous_session_check_is_not_authenticated_or_cached(self):
        response = self.client.get("/auth/session")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"authenticated": False, "user": None})
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_valid_old_session_is_restored_and_upgraded_to_persistent_cookie(self):
        connection = SessionConnection(TEST_USER)
        with self.client.session_transaction() as current_session:
            current_session["user_id"] = TEST_USER["id"]
            current_session["email"] = TEST_USER["email"]
            current_session["is_admin"] = False
            self.assertFalse(current_session.permanent)

        with patch.object(app_module, "get_db_connection", return_value=connection):
            response = self.client.get("/auth/session")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["authenticated"])
        self.assertEqual(payload["user"]["id"], TEST_USER["id"])
        self.assertEqual(payload["user"]["email"], TEST_USER["email"])
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertIn("Expires=", response.headers.get("Set-Cookie", ""))
        with self.client.session_transaction() as current_session:
            self.assertTrue(current_session.permanent)
        self.assertTrue(connection.cursor_instance.closed)
        self.assertTrue(connection.closed)

    def test_deleted_account_clears_stale_signed_session(self):
        connection = SessionConnection(None)
        with self.client.session_transaction() as current_session:
            current_session["user_id"] = 999
            current_session["email"] = "deleted@example.com"

        with patch.object(app_module, "get_db_connection", return_value=connection):
            response = self.client.get("/auth/session")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.get_json()["authenticated"])
        with self.client.session_transaction() as current_session:
            self.assertNotIn("user_id", current_session)


if __name__ == "__main__":
    unittest.main()

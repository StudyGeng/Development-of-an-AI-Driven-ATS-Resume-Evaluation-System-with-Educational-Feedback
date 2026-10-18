from __future__ import annotations

import base64
import importlib
import io
import os
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from PIL import Image


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

with patch.dict(
    os.environ,
    {"APP_ENV": "testing", "FLASK_SECRET_KEY": "self-service-profile-tests-secret-1234567890"},
    clear=False,
):
    app_module = importlib.import_module("app")


TEST_USER = {
    "id": 23,
    "full_name": "Ming Student",
    "email": "ming@example.com",
    "email_verified_at": datetime(2026, 10, 1),
    "is_admin": 0,
    "created_at": datetime(2026, 9, 1),
}


class FakeCursor:
    def __init__(self, row=None):
        self.row = row
        self.queries = []
        self.closed = False

    def execute(self, query, params=()):
        self.queries.append((" ".join(query.lower().split()), tuple(params)))

    def fetchone(self):
        return self.row

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, row=None):
        self.cursor_instance = FakeCursor(row)
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self, dictionary=False):
        return self.cursor_instance

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class SelfServiceProfileTests(unittest.TestCase):
    def setUp(self):
        app_module.app.config.update(TESTING=True, ENFORCE_CSRF_IN_TESTS=False)
        self.client = app_module.app.test_client()

    def auth_user(self, user=None):
        return patch.object(app_module, "authenticate_json_request", return_value=(user or dict(TEST_USER), None))

    def test_mutating_routes_require_a_signed_in_account(self):
        requests = (
            self.client.patch("/users/me", json={"full_name": "New Name"}),
            self.client.post("/users/me/email-change/pin", json={"new_email": "new@example.com", "current_password": "Secret!123"}),
            self.client.put("/users/me/email", json={"new_email": "new@example.com", "current_password": "Secret!123", "verification_pin": "123456"}),
            self.client.get("/users/me/avatar"),
            self.client.put("/users/me/avatar", data={"avatar": (io.BytesIO(b"image"), "photo.png")}),
            self.client.delete("/users/me/avatar"),
        )
        self.assertTrue(all(response.status_code == 401 for response in requests))

    def test_name_update_is_bounded_and_only_updates_current_user(self):
        updated = {**TEST_USER, "full_name": "Ming Updated"}
        connection = FakeConnection(updated)
        with self.auth_user(), patch.object(app_module, "get_db_connection", return_value=connection):
            response = self.client.patch("/users/me", json={"full_name": "  Ming   Updated  "})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["user"]["full_name"], "Ming Updated")
        self.assertEqual(connection.cursor_instance.queries[0][1], ("Ming Updated", TEST_USER["id"]))
        self.assertTrue(connection.committed)
        self.assertTrue(connection.closed)

        with self.auth_user(), patch.object(app_module, "get_db_connection") as database:
            invalid = self.client.patch("/users/me", json={"full_name": "X"})
        self.assertEqual(invalid.status_code, 400)
        database.assert_not_called()

    def test_email_change_pin_requires_password_and_is_bound_to_user(self):
        with self.auth_user(), patch.object(app_module, "get_email_pin_delivery_mode", return_value="dev"), patch.object(
            app_module, "enforce_login_rate_limits"
        ), patch.object(app_module, "verify_current_user_password", return_value=True), patch.object(
            app_module, "email_exists", return_value=False
        ), patch.object(app_module, "enforce_email_pin_rate_limits") as rate_limit, patch.object(
            app_module, "create_email_pin", return_value=("123456", datetime(2026, 10, 1), 7)
        ) as create_pin, patch.object(app_module, "send_email_pin", return_value="dev") as send_pin:
            response = self.client.post(
                "/users/me/email-change/pin",
                json={"new_email": " NEW@example.com ", "current_password": "Secret!123"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(create_pin.call_args.args[:2], ("new@example.com", "email_change_23"))
        self.assertEqual(send_pin.call_args.args, ("new@example.com", "123456", "email_change_23"))
        self.assertEqual(rate_limit.call_args.args[0], "request")

        with self.auth_user(), patch.object(app_module, "get_email_pin_delivery_mode", return_value="dev"), patch.object(
            app_module, "enforce_login_rate_limits"
        ), patch.object(app_module, "verify_current_user_password", return_value=False), patch.object(
            app_module, "create_email_pin"
        ) as create_pin:
            wrong = self.client.post(
                "/users/me/email-change/pin",
                json={"new_email": "new@example.com", "current_password": "wrong"},
            )
        self.assertEqual(wrong.status_code, 401)
        create_pin.assert_not_called()

    def test_admin_email_change_is_rejected_before_pin_or_update(self):
        admin = {**TEST_USER, "is_admin": 1}
        with self.auth_user(admin), patch.object(app_module, "create_email_pin") as create_pin, patch.object(
            app_module, "consume_email_pin"
        ) as consume_pin:
            send = self.client.post(
                "/users/me/email-change/pin",
                json={"new_email": "new@example.com", "current_password": "Secret!123"},
            )
            update = self.client.put(
                "/users/me/email",
                json={"new_email": "new@example.com", "current_password": "Secret!123", "verification_pin": "123456"},
            )
        self.assertEqual(send.status_code, 403)
        self.assertEqual(update.status_code, 403)
        create_pin.assert_not_called()
        consume_pin.assert_not_called()

    def test_email_update_requires_pin_and_preserves_session_csrf(self):
        updated = {**TEST_USER, "email": "new@example.com"}
        connection = FakeConnection(updated)
        with self.client.session_transaction() as current_session:
            current_session["user_id"] = TEST_USER["id"]
            current_session["csrf_token"] = "long-existing-token-for-this-session-123456"
        with self.auth_user(), patch.object(app_module, "enforce_login_rate_limits"), patch.object(
            app_module, "verify_current_user_password", return_value=True
        ), patch.object(app_module, "email_exists", return_value=False), patch.object(
            app_module, "enforce_email_pin_rate_limits"
        ), patch.object(app_module, "consume_email_pin", return_value=(True, "PIN verified.")) as consume_pin, patch.object(
            app_module, "get_db_connection", return_value=connection
        ):
            response = self.client.put(
                "/users/me/email",
                json={"new_email": "new@example.com", "current_password": "Secret!123", "verification_pin": "123456"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["user"]["email"], "new@example.com")
        self.assertEqual(consume_pin.call_args.args, ("new@example.com", "email_change_23", "123456"))
        self.assertEqual(connection.cursor_instance.queries[0][1], ("new@example.com", TEST_USER["id"]))
        self.assertTrue(connection.committed)
        with self.client.session_transaction() as current_session:
            self.assertEqual(current_session["email"], "new@example.com")
            self.assertEqual(current_session["csrf_token"], "long-existing-token-for-this-session-123456")

        with self.auth_user(), patch.object(app_module, "consume_email_pin") as consume_pin:
            invalid = self.client.put(
                "/users/me/email",
                json={"new_email": "other@example.com", "current_password": "Secret!123", "verification_pin": "12a456"},
            )
        self.assertEqual(invalid.status_code, 400)
        consume_pin.assert_not_called()

    def test_avatar_upload_reencodes_image_and_stores_only_for_owner(self):
        source = io.BytesIO()
        Image.new("RGBA", (64, 32), (80, 120, 200, 128)).save(source, format="PNG")
        connection = FakeConnection()
        with self.auth_user(), patch.object(app_module, "get_db_connection", return_value=connection), patch.object(
            app_module, "is_postgres_backend", return_value=True
        ):
            response = self.client.put(
                "/users/me/avatar",
                data={"avatar": (io.BytesIO(source.getvalue()), "photo.png")},
                content_type="multipart/form-data",
            )
        self.assertEqual(response.status_code, 200)
        query, params = connection.cursor_instance.queries[0]
        self.assertIn("on conflict (user_id)", query)
        self.assertEqual(params[:2], (TEST_USER["id"], "image/jpeg"))
        with Image.open(io.BytesIO(base64.b64decode(params[2]))) as normalized:
            self.assertEqual(normalized.format, "JPEG")
            self.assertEqual(normalized.size, (64, 32))
            self.assertFalse(normalized.getexif())

        with self.auth_user(), patch.object(app_module, "get_db_connection") as database:
            unsafe = self.client.put(
                "/users/me/avatar",
                data={"avatar": (io.BytesIO(b'<svg xmlns="http://www.w3.org/2000/svg"/>'), "photo.svg")},
                content_type="multipart/form-data",
            )
        self.assertEqual(unsafe.status_code, 400)
        database.assert_not_called()

    def test_avatar_read_is_private_and_delete_uses_signed_in_user_id(self):
        encoded = base64.b64encode(b"normalized-jpeg").decode("ascii")
        read_connection = FakeConnection({"mime_type": "image/jpeg", "image_base64": encoded})
        with self.auth_user(), patch.object(app_module, "get_db_connection", return_value=read_connection):
            response = self.client.get("/users/me/avatar")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b"normalized-jpeg")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(read_connection.cursor_instance.queries[0][1], (TEST_USER["id"],))

        delete_connection = FakeConnection()
        with self.auth_user(), patch.object(app_module, "get_db_connection", return_value=delete_connection):
            deleted = self.client.delete("/users/me/avatar")
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(delete_connection.cursor_instance.queries[0][1], (TEST_USER["id"],))
        self.assertTrue(delete_connection.committed)


if __name__ == "__main__":
    unittest.main()

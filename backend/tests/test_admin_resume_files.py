from __future__ import annotations

import importlib
import json
import os
import sys
import unittest
from datetime import datetime
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from werkzeug.datastructures import FileStorage


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

with patch.dict(
    os.environ,
    {
        "APP_ENV": "testing",
        "FLASK_SECRET_KEY": "resume-file-tests-only-secret-key-value-1234567890",
    },
    clear=False,
):
    app_module = importlib.import_module("app")


USERS = {
    1: {"id": 1, "full_name": "Alice", "email": "alice@example.com", "is_admin": 0},
    9: {"id": 9, "full_name": "Admin", "email": "admin@example.com", "is_admin": 1},
}

PDF_BYTES = b"%PDF-1.7 original resume bytes"


class FakeCursor:
    def __init__(self, database):
        self.database = database
        self.result = None
        self.lastrowid = None

    def execute(self, query, params=()):
        normalized_query = " ".join(query.lower().split())
        safe_params = tuple(params)
        self.database.executed.append(normalized_query)

        for fragment in self.database.fail_on:
            if fragment in normalized_query:
                raise app_module.DatabaseError("simulated failure")

        if "from users where id = %s" in normalized_query:
            user = USERS.get(int(safe_params[0]))
            self.result = dict(user) if user else None
        elif normalized_query.startswith("create table if not exists resume_files"):
            self.result = None
        elif normalized_query.startswith(("savepoint", "release savepoint", "rollback to savepoint")):
            self.result = None
        elif normalized_query.startswith("insert into resume_analyses"):
            self.result = (501,)
            self.lastrowid = 501
        elif normalized_query.startswith("insert into resume_files"):
            self.database.file_inserts.append(safe_params)
            self.result = None
        elif "from resume_analyses ra" in normalized_query and "where ra.id = %s" in normalized_query:
            analysis = self.database.analyses.get(int(safe_params[0]))
            self.result = dict(analysis) if analysis else None
        elif "from resume_files where analysis_id = %s" in normalized_query:
            stored = self.database.files.get(int(safe_params[0]))
            self.result = dict(stored) if stored else None
        else:
            raise AssertionError(f"Unexpected query: {normalized_query}")

    def fetchone(self):
        return self.result

    def fetchall(self):
        return list(self.result or [])

    def close(self):
        pass


class FakeConnection:
    def __init__(self, database):
        self.database = database

    def cursor(self, dictionary=False):
        return FakeCursor(self.database)

    def commit(self):
        self.database.commits += 1

    def rollback(self):
        self.database.rollbacks += 1

    def close(self):
        pass


class FakeDatabase:
    def __init__(self):
        self.executed: list[str] = []
        self.fail_on: list[str] = []
        self.file_inserts: list[tuple] = []
        self.commits = 0
        self.rollbacks = 0
        self.analyses = {
            501: {
                "id": 501,
                "uploaded_resume_name": "alice.pdf",
                "target_role": "Software Developer",
                "matching_percentage": 72.5,
                "resume_excerpt": "Built a Flask API.",
                "analysis_json": json.dumps({"matching_percentage": 72.5, "resume_summary": {"skills": ["python"]}}),
                "created_at": datetime(2026, 10, 1, 9, 30),
                "full_name": "Alice",
                "email": "alice@example.com",
            }
        }
        self.files = {
            501: {
                "file_name": "alice.pdf",
                "content_type": "application/pdf",
                "file_size": len(PDF_BYTES),
                "file_data": PDF_BYTES,
            }
        }

    def __call__(self):
        return FakeConnection(self)


class ResumeFileStorageTests(unittest.TestCase):
    def setUp(self):
        app_module.app.config.update(TESTING=True, SECRET_KEY="resume-file-test-secret")
        self.client = app_module.app.test_client()
        self.database = FakeDatabase()
        self.connection_patch = patch.object(app_module, "get_db_connection", self.database)
        self.connection_patch.start()
        app_module._resume_file_table_ready = False

    def tearDown(self):
        self.connection_patch.stop()
        app_module._resume_file_table_ready = False

    def sign_in(self, user_id):
        with self.client.session_transaction() as current_session:
            current_session["user_id"] = user_id

    def persist(self):
        payload = {
            "uploaded_resume_name": "alice.pdf",
            "compatibility": {"matching_percentage": 72.5},
            "feedback": "Feedback",
            "resume_excerpt": "Excerpt",
        }
        upload = app_module.ResumeUpload("alice.pdf", "application/pdf", PDF_BYTES)
        return app_module.persist_analysis(payload, "Job description", "Software Developer", 1, resume_upload=upload)

    def test_original_file_is_saved_with_its_analysis(self):
        analysis_id = self.persist()

        self.assertEqual(analysis_id, 501)
        self.assertEqual(
            self.database.file_inserts,
            [(501, "alice.pdf", "application/pdf", len(PDF_BYTES), PDF_BYTES)],
        )
        self.assertIn("release savepoint resume_file_upload", self.database.executed)
        self.assertEqual(self.database.rollbacks, 0)

    def test_failed_file_insert_keeps_the_analysis(self):
        self.database.fail_on.append("insert into resume_files")

        analysis_id = self.persist()

        self.assertEqual(analysis_id, 501)
        self.assertIn("rollback to savepoint resume_file_upload", self.database.executed)
        self.assertEqual(self.database.executed[-1], "rollback to savepoint resume_file_upload")
        self.assertGreaterEqual(self.database.commits, 1)

    def test_unavailable_file_table_still_saves_the_analysis(self):
        self.database.fail_on.append("create table if not exists resume_files")

        analysis_id = self.persist()

        self.assertEqual(analysis_id, 501)
        self.assertEqual(self.database.file_inserts, [])
        self.assertTrue(any(query.startswith("insert into resume_analyses") for query in self.database.executed))
        self.assertFalse(app_module._resume_file_table_ready)

    def test_upload_type_comes_from_the_validated_extension(self):
        stream = BytesIO(b"\x89PNG image bytes")
        stream.read(4)
        upload = app_module.read_resume_upload(
            FileStorage(stream=stream, filename="CV.PNG", content_type="text/html")
        )

        self.assertEqual(upload, app_module.ResumeUpload("CV.PNG", "image/png", b"\x89PNG image bytes"))
        self.assertIsNone(
            app_module.read_resume_upload(FileStorage(stream=BytesIO(b"x"), filename="notes.txt"))
        )

    def test_regular_user_cannot_open_another_users_report_or_file(self):
        self.sign_in(1)

        report_response = self.client.get("/admin/analyses/501")
        file_response = self.client.get("/admin/analyses/501/resume-file")

        self.assertEqual(report_response.status_code, 403)
        self.assertEqual(file_response.status_code, 403)
        self.assertNotIn(PDF_BYTES, file_response.data)

    def test_admin_report_includes_file_details(self):
        self.sign_in(9)

        response = self.client.get("/admin/analyses/501")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["user_email"], "alice@example.com")
        self.assertEqual(payload["score"], 72.5)
        self.assertEqual(payload["analysis"]["resume_excerpt"], "Built a Flask API.")
        self.assertEqual(
            payload["resume_file"],
            {"file_name": "alice.pdf", "content_type": "application/pdf", "file_size": len(PDF_BYTES)},
        )
        self.assertIn("no-store", response.headers["Cache-Control"])

    def test_admin_report_for_unknown_analysis_returns_not_found(self):
        self.sign_in(9)

        response = self.client.get("/admin/analyses/999")

        self.assertEqual(response.status_code, 404)

    def test_admin_downloads_the_original_file(self):
        self.sign_in(9)

        response = self.client.get("/admin/analyses/501/resume-file")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, PDF_BYTES)
        self.assertEqual(response.mimetype, "application/pdf")
        self.assertIn("attachment", response.headers["Content-Disposition"])
        self.assertIn("alice.pdf", response.headers["Content-Disposition"])
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")

    def test_missing_original_file_returns_not_found(self):
        self.sign_in(9)
        self.database.files.clear()

        response = self.client.get("/admin/analyses/501/resume-file")

        self.assertEqual(response.status_code, 404)


class JobDataInsightCacheTests(unittest.TestCase):
    def tearDown(self):
        app_module._job_data_insights_cache = None

    def test_insights_are_rebuilt_only_when_the_catalogue_reloads(self):
        first_catalogue = object()
        second_catalogue = object()
        app_module._job_data_insights_cache = None
        with (
            patch.object(app_module, "get_job_catalog_version", side_effect=[first_catalogue, first_catalogue, second_catalogue]),
            patch.object(app_module, "fetch_job_data_for_insights", return_value=([], {})),
            patch.object(app_module, "build_job_data_insights", side_effect=[{"build": 1}, {"build": 2}]) as build,
        ):
            self.assertEqual(app_module.get_cached_job_data_insights(), {"build": 1})
            self.assertEqual(app_module.get_cached_job_data_insights(), {"build": 1})
            self.assertEqual(app_module.get_cached_job_data_insights(), {"build": 2})

        self.assertEqual(build.call_count, 2)


if __name__ == "__main__":
    unittest.main()

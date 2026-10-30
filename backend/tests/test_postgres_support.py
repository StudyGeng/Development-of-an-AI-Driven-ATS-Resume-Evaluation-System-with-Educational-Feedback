from __future__ import annotations

import contextlib
import io
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import database  # noqa: E402
import manage  # noqa: E402


POSTGRES_URL = "postgresql://postgres.example:placeholder@example.invalid:5432/postgres"


@unittest.skipIf(database.psycopg is None, "PostgreSQL dependencies are not installed")
class PostgresSupportTests(unittest.TestCase):
    def test_database_url_selects_postgres_and_migration_command(self):
        with patch.dict(os.environ, {"DATABASE_URL": POSTGRES_URL}):
            self.assertTrue(database.is_postgres_backend())
            self.assertEqual(database.utc_now_sql(), "(CURRENT_TIMESTAMP AT TIME ZONE 'UTC')")
            with patch.object(manage, "init_postgres_database") as postgres_migrate, patch.object(
                manage, "init_mysql_database"
            ) as mysql_migrate, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(manage.main(["migrate"]), 0)
            postgres_migrate.assert_called_once_with()
            mysql_migrate.assert_not_called()

        with patch.dict(os.environ, {"DATABASE_URL": ""}):
            self.assertFalse(database.is_postgres_backend())
            self.assertEqual(database.utc_now_sql(), "UTC_TIMESTAMP()")

    def test_postgres_migration_uses_private_schema_for_nine_tables(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value
        with patch.dict(os.environ, {"DATABASE_URL": POSTGRES_URL}), patch.object(
            database.psycopg, "connect", return_value=connection
        ):
            database.init_postgres_database()

        statements = [call.args[0] for call in cursor.execute.call_args_list]
        self.assertEqual(statements[:2], [
            "CREATE SCHEMA IF NOT EXISTS ats_private",
            "SET search_path TO ats_private",
        ])
        tables = (
            "users", "user_avatars", "email_verification_pins", "auth_rate_limit_events",
            "resume_analyses", "resume_files", "download_history", "job_roles", "job_requirements",
        )
        table_creates = [statement for statement in statements if "CREATE TABLE IF NOT EXISTS" in statement]
        self.assertEqual(len(table_creates), len(tables))
        for table in tables:
            self.assertTrue(any(f"CREATE TABLE IF NOT EXISTS {table}" in sql for sql in table_creates))
        self.assertNotIn("public.", "\n".join(statements))
        connection.commit.assert_called_once_with()
        cursor.close.assert_called_once_with()
        connection.close.assert_called_once_with()

    def test_postgres_dictionary_cursor_and_close_return_connection_to_pool(self):
        pool = MagicMock()
        raw_connection = pool.getconn.return_value
        with patch.dict(os.environ, {"DATABASE_URL": POSTGRES_URL}), patch.object(
            database, "_postgres_connection_pool", None
        ), patch.object(database, "ConnectionPool", return_value=pool) as make_pool:
            connection = database.get_db_connection()
            make_pool.call_args.kwargs["configure"](raw_connection)
            raw_connection.execute.assert_called_once_with("SET search_path TO ats_private")
            raw_connection.commit.assert_called_once_with()
            connection.cursor(dictionary=True)
            raw_connection.cursor.assert_called_once_with(row_factory=database.dict_row)
            connection.close()
            connection.close()

        self.assertEqual(make_pool.call_count, 1)
        self.assertEqual(pool.getconn.call_count, 1)
        raw_connection.rollback.assert_called_once_with()
        pool.putconn.assert_called_once_with(raw_connection)

    def test_postgres_unique_violation_is_duplicate_entry(self):
        error = Exception("duplicate")
        error.sqlstate = "23505"
        self.assertTrue(database.is_duplicate_entry_error(error))
        error.sqlstate = "23503"
        self.assertFalse(database.is_duplicate_entry_error(error))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import contextlib
import io
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import database  # noqa: E402
import manage  # noqa: E402


class DatabaseMigrationCommandTests(unittest.TestCase):
    def test_migrate_command_runs_schema_initialization(self):
        output = io.StringIO()
        with patch.object(manage, "init_mysql_database") as initialize:
            with contextlib.redirect_stdout(output):
                exit_code = manage.main(["migrate"])

        self.assertEqual(exit_code, 0)
        initialize.assert_called_once_with(create_database=False)
        self.assertIn("completed successfully", output.getvalue())

    def test_create_database_flag_is_forwarded_for_local_setup(self):
        with patch.object(manage, "init_mysql_database") as initialize:
            exit_code = manage.main(["migrate", "--create-database"])

        self.assertEqual(exit_code, 0)
        initialize.assert_called_once_with(create_database=True)

    def test_command_requires_an_explicit_subcommand(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                manage.main([])

        self.assertEqual(raised.exception.code, 2)

    def test_importing_web_app_never_opens_a_database_connection(self):
        environment = os.environ.copy()
        environment.update(
            {
                "APP_ENV": "testing",
                "FLASK_SECRET_KEY": "migration-import-test-secret-key-value-1234567890",
            }
        )
        script = "\n".join(
            (
                "from unittest.mock import patch",
                "import database",
                "with patch.object(database.mysql.connector, 'connect', side_effect=AssertionError('unexpected database connection')):",
                "    import app",
            )
        )

        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=BACKEND_DIR,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)


class DatabaseMigrationResourceTests(unittest.TestCase):
    def test_database_resources_close_after_success(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value

        with patch.object(database, "get_mysql_pool_size", return_value=5), patch.object(
            database, "get_mysql_config", return_value={"database": "career_navigator"}
        ) as config, patch.object(
            database.mysql.connector, "connect", return_value=connection
        ), patch.object(database, "ensure_column"):
            database.init_mysql_database()

        config.assert_called_once_with(include_database=True)
        executed_sql = "\n".join(str(call.args[0]) for call in cursor.execute.call_args_list)
        self.assertNotIn("CREATE DATABASE", executed_sql)
        connection.commit.assert_called_once_with()
        cursor.close.assert_called_once_with()
        connection.close.assert_called_once_with()

    def test_local_setup_can_explicitly_create_database(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value

        with patch.object(database, "get_mysql_pool_size", return_value=5), patch.object(
            database, "get_mysql_config", return_value={"host": "localhost"}
        ) as config, patch.object(
            database.mysql.connector, "connect", return_value=connection
        ), patch.object(database, "ensure_column"):
            database.init_mysql_database(create_database=True)

        config.assert_called_once_with(include_database=False)
        executed_sql = "\n".join(str(call.args[0]) for call in cursor.execute.call_args_list)
        self.assertIn("CREATE DATABASE IF NOT EXISTS", executed_sql)
        self.assertIn("USE `career_navigator`", executed_sql)

    def test_database_resources_close_after_migration_error(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value
        cursor.execute.side_effect = RuntimeError("migration failed")

        with patch.object(database, "get_mysql_pool_size", return_value=5), patch.object(
            database, "get_mysql_config", return_value={"database": "career_navigator"}
        ), patch.object(
            database.mysql.connector, "connect", return_value=connection
        ):
            with self.assertRaisesRegex(RuntimeError, "migration failed"):
                database.init_mysql_database()

        connection.commit.assert_not_called()
        connection.rollback.assert_called_once_with()
        cursor.close.assert_called_once_with()
        connection.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()

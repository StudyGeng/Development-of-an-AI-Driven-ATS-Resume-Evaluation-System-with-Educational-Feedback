from __future__ import annotations

import os
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import database  # noqa: E402


class DatabaseConnectionPoolTests(unittest.TestCase):
    def setUp(self):
        self.original_pool = database._connection_pool
        database._connection_pool = None

    def tearDown(self):
        database._connection_pool = self.original_pool

    def test_pool_is_created_lazily_once_and_reused(self):
        pool = Mock()
        first_connection = Mock()
        second_connection = Mock()
        pool.get_connection.side_effect = [first_connection, second_connection]
        mysql_config = {
            "host": "db.example.test",
            "port": 3306,
            "user": "app_user",
            "password": "test-only-password",
            "database": "career_navigator",
            "charset": "utf8mb4",
            "collation": "utf8mb4_unicode_ci",
        }

        with patch.dict(os.environ, {"MYSQL_POOL_SIZE": "7"}, clear=False), patch.object(
            database,
            "get_mysql_config",
            return_value=mysql_config,
        ), patch.object(database, "MySQLConnectionPool", return_value=pool) as pool_class:
            returned_first = database.get_db_connection()
            returned_second = database.get_db_connection()

        self.assertIs(returned_first, first_connection)
        self.assertIs(returned_second, second_connection)
        pool_class.assert_called_once_with(
            pool_name="career_navigator_pool",
            pool_size=7,
            pool_reset_session=True,
            **mysql_config,
        )
        self.assertEqual(pool.get_connection.call_count, 2)

    def test_default_pool_size_is_five(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(database.get_mysql_pool_size(), 5)

    def test_invalid_pool_sizes_fail_with_clear_configuration_error(self):
        for value in ("not-a-number", "0", "33", "-1"):
            with self.subTest(value=value), patch.dict(
                os.environ,
                {"MYSQL_POOL_SIZE": value},
                clear=False,
            ):
                with self.assertRaisesRegex(RuntimeError, "MYSQL_POOL_SIZE"):
                    database.get_mysql_pool_size()

    def test_pool_size_boundaries_are_valid(self):
        for expected in (1, 32):
            with self.subTest(pool_size=expected), patch.dict(
                os.environ,
                {"MYSQL_POOL_SIZE": str(expected)},
                clear=False,
            ):
                self.assertEqual(database.get_mysql_pool_size(), expected)

    def test_concurrent_first_requests_construct_only_one_pool(self):
        pool = Mock()
        pool.get_connection.return_value = Mock()
        with patch.dict(os.environ, {"MYSQL_POOL_SIZE": "5"}, clear=False), patch.object(
            database,
            "MySQLConnectionPool",
            return_value=pool,
        ) as pool_class:
            with ThreadPoolExecutor(max_workers=8) as executor:
                connections = list(executor.map(lambda _: database.get_db_connection(), range(16)))

        pool_class.assert_called_once()
        self.assertEqual(pool.get_connection.call_count, 16)
        self.assertTrue(all(connection is pool.get_connection.return_value for connection in connections))

    def test_failed_pool_construction_is_retryable(self):
        with patch.dict(os.environ, {"MYSQL_POOL_SIZE": "5"}, clear=False), patch.object(
            database,
            "MySQLConnectionPool",
            side_effect=database.DatabaseError("database unavailable"),
        ) as pool_class:
            with self.assertRaises(database.DatabaseError):
                database.get_db_connection()
            with self.assertRaises(database.DatabaseError):
                database.get_db_connection()

        self.assertIsNone(database._connection_pool)
        self.assertEqual(pool_class.call_count, 2)


if __name__ == "__main__":
    unittest.main()

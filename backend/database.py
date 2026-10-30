from __future__ import annotations

import os
from pathlib import Path
from threading import Lock
from typing import Any

import mysql.connector  # type: ignore
from dotenv import load_dotenv  # type: ignore
from mysql.connector import errorcode  # type: ignore
from mysql.connector.pooling import MySQLConnectionPool  # type: ignore

try:
    import psycopg  # type: ignore
    from psycopg.rows import dict_row  # type: ignore
    from psycopg_pool import ConnectionPool, PoolClosed, PoolTimeout  # type: ignore
except ImportError:  # Local MySQL installations can install PostgreSQL extras later.
    psycopg = None
    dict_row = None
    ConnectionPool = None
    PoolClosed = None
    PoolTimeout = None


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_ENV_PATH = PROJECT_ROOT / "config" / "env"
ROOT_ENV_PATH = PROJECT_ROOT / "env"

load_dotenv(CONFIG_ENV_PATH if CONFIG_ENV_PATH.exists() else ROOT_ENV_PATH)

DatabaseError = mysql.connector.Error
DatabaseErrors = (
    (mysql.connector.Error, psycopg.Error, PoolClosed, PoolTimeout)
    if psycopg is not None
    else (mysql.connector.Error,)
)
MYSQL_POOL_MAX_SIZE = 32
POSTGRES_POOL_MAX_SIZE = 32
POSTGRES_SCHEMA = "ats_private"

_connection_pool: MySQLConnectionPool | None = None
_connection_pool_lock = Lock()
_postgres_connection_pool: Any = None
_postgres_connection_pool_lock = Lock()


def is_postgres_backend() -> bool:
    return bool(os.getenv("DATABASE_URL", "").strip())


def utc_now_sql() -> str:
    """Return a UTC expression compatible with the timestamp columns."""
    return "(CURRENT_TIMESTAMP AT TIME ZONE 'UTC')" if is_postgres_backend() else "UTC_TIMESTAMP()"


def get_postgres_database_url() -> str:
    url = os.getenv("DATABASE_URL", "").strip()
    if not url.startswith(("postgresql://", "postgres://")):
        raise RuntimeError("DATABASE_URL must be a PostgreSQL connection URI")
    return url


def get_postgres_pool_size() -> int:
    raw_pool_size = os.getenv("POSTGRES_POOL_SIZE", "3").strip()
    try:
        pool_size = int(raw_pool_size)
    except ValueError as error:
        raise RuntimeError("POSTGRES_POOL_SIZE must be an integer between 1 and 32") from error
    if not 1 <= pool_size <= POSTGRES_POOL_MAX_SIZE:
        raise RuntimeError("POSTGRES_POOL_SIZE must be between 1 and 32")
    return pool_size


def _require_postgres_driver() -> None:
    if psycopg is None or ConnectionPool is None:
        raise RuntimeError("PostgreSQL support requires psycopg and psycopg_pool; install backend/requirements.txt")


def _configure_postgres_connection(connection: Any) -> None:
    # The session pooler preserves this setting across transactions. A single
    # private schema keeps these personal-data tables out of Supabase Data API.
    connection.execute(f"SET search_path TO {POSTGRES_SCHEMA}")
    connection.commit()


class _PostgresConnection:
    """Adapt Psycopg's cursor and pool lifecycle to the existing MySQL calls."""

    def __init__(self, connection: Any, pool: Any) -> None:
        self._connection = connection
        self._pool = pool

    def cursor(self, dictionary: bool = False):
        if dictionary:
            return self._connection.cursor(row_factory=dict_row)
        return self._connection.cursor()

    def close(self) -> None:
        connection = self._connection
        if connection is not None:
            self._connection = None
            try:
                # Read-only queries also start a transaction in Psycopg. End it
                # before returning the connection to avoid a pool reset warning.
                connection.rollback()
            finally:
                self._pool.putconn(connection)

    def __getattr__(self, name: str):
        return getattr(self._connection, name)


def get_database_name() -> str:
    return os.getenv("MYSQL_DATABASE", "career_navigator").strip() or "career_navigator"


def quote_identifier(identifier: str) -> str:
    return "`" + identifier.replace("`", "``") + "`"


def get_mysql_config(include_database: bool = True) -> dict[str, Any]:
    config: dict[str, Any] = {
        "host": os.getenv("MYSQL_HOST", "127.0.0.1"),
        "port": int(os.getenv("MYSQL_PORT", "3307")),
        "user": os.getenv("MYSQL_USER", "root"),
        "password": os.getenv("MYSQL_PASSWORD", ""),
        "charset": "utf8mb4",
        "collation": "utf8mb4_unicode_ci",
    }
    ssl_ca = os.getenv("MYSQL_SSL_CA", "").strip()
    if ssl_ca:
        config["ssl_ca"] = ssl_ca
        config["ssl_verify_cert"] = True
    if include_database:
        config["database"] = get_database_name()
    return config


def get_mysql_pool_size() -> int:
    raw_pool_size = os.getenv("MYSQL_POOL_SIZE", "5").strip()
    try:
        pool_size = int(raw_pool_size)
    except ValueError as error:
        raise RuntimeError("MYSQL_POOL_SIZE must be an integer between 1 and 32") from error

    if not 1 <= pool_size <= MYSQL_POOL_MAX_SIZE:
        raise RuntimeError("MYSQL_POOL_SIZE must be between 1 and 32")
    return pool_size


def get_db_connection():
    """Borrow a connection from this worker's selected database pool."""
    global _connection_pool, _postgres_connection_pool

    if is_postgres_backend():
        _require_postgres_driver()
        if _postgres_connection_pool is None:
            with _postgres_connection_pool_lock:
                if _postgres_connection_pool is None:
                    _postgres_connection_pool = ConnectionPool(
                        conninfo=get_postgres_database_url(),
                        kwargs={"sslmode": "require", "prepare_threshold": None},
                        min_size=0,
                        max_size=get_postgres_pool_size(),
                        configure=_configure_postgres_connection,
                        open=True,
                    )
        return _PostgresConnection(_postgres_connection_pool.getconn(), _postgres_connection_pool)

    if _connection_pool is None:
        with _connection_pool_lock:
            if _connection_pool is None:
                _connection_pool = MySQLConnectionPool(
                    pool_name="career_navigator_pool",
                    pool_size=get_mysql_pool_size(),
                    pool_reset_session=True,
                    **get_mysql_config(),
                )
    return _connection_pool.get_connection()


def is_duplicate_entry_error(error: Exception) -> bool:
    return (
        getattr(error, "errno", None) == errorcode.ER_DUP_ENTRY
        or getattr(error, "sqlstate", None) == "23505"
    )


def column_exists(cursor, database_name: str, table_name: str, column_name: str) -> bool:
    cursor.execute(
        """
        SELECT COUNT(*) AS count
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = %s
          AND TABLE_NAME = %s
          AND COLUMN_NAME = %s
        """,
        (database_name, table_name, column_name),
    )
    row = cursor.fetchone()
    return bool(row and row[0])


def ensure_column(cursor, database_name: str, table_name: str, column_name: str, column_sql: str) -> None:
    if not column_exists(cursor, database_name, table_name, column_name):
        cursor.execute(f"ALTER TABLE {quote_identifier(table_name)} ADD COLUMN {column_sql}")


# Original uploads are kept per analysis so administrators can review them.
# Deleting an analysis (directly, by clearing history, or with its account)
# removes its file through the cascade.
MYSQL_RESUME_FILES_TABLE = """
    CREATE TABLE IF NOT EXISTS resume_files (
        analysis_id INT PRIMARY KEY,
        file_name VARCHAR(255) NOT NULL,
        content_type VARCHAR(100) NOT NULL,
        file_size INT UNSIGNED NOT NULL,
        file_data LONGBLOB NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        CONSTRAINT fk_resume_files_analysis
            FOREIGN KEY (analysis_id)
            REFERENCES resume_analyses(id)
            ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

POSTGRES_RESUME_FILES_TABLE = """
    CREATE TABLE IF NOT EXISTS resume_files (
        analysis_id INTEGER PRIMARY KEY REFERENCES resume_analyses(id) ON DELETE CASCADE,
        file_name VARCHAR(255) NOT NULL,
        content_type VARCHAR(100) NOT NULL,
        file_size INTEGER NOT NULL CHECK (file_size >= 0),
        file_data BYTEA NOT NULL,
        created_at TIMESTAMP DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')
    )
"""


def resume_files_table_sql() -> str:
    return POSTGRES_RESUME_FILES_TABLE if is_postgres_backend() else MYSQL_RESUME_FILES_TABLE


def init_mysql_database(*, create_database: bool = False) -> None:
    """Create or upgrade tables without seeding user data.

    Managed database users commonly cannot create databases, so the default
    connects directly to the configured ``MYSQL_DATABASE``. Local first-time
    setup can opt in to creating that database.
    """
    # Validate pool sizing before opening a database connection.
    get_mysql_pool_size()
    database_name = get_database_name()
    connection = None
    cursor = None
    try:
        connection = mysql.connector.connect(**get_mysql_config(include_database=not create_database))
        cursor = connection.cursor()
        if create_database:
            cursor.execute(
                f"""
                CREATE DATABASE IF NOT EXISTS {quote_identifier(database_name)}
                CHARACTER SET utf8mb4
                COLLATE utf8mb4_unicode_ci
                """
            )
            cursor.execute(f"USE {quote_identifier(database_name)}")
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INT AUTO_INCREMENT PRIMARY KEY,
                full_name VARCHAR(255) NOT NULL,
                email VARCHAR(255) NOT NULL UNIQUE,
                password_hash VARCHAR(255) NOT NULL,
                email_verified_at TIMESTAMP NULL,
                is_admin TINYINT UNSIGNED NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        )
        ensure_column(cursor, database_name, "users", "email_verified_at", "email_verified_at TIMESTAMP NULL")
        ensure_column(cursor, database_name, "users", "is_admin", "is_admin TINYINT UNSIGNED NOT NULL DEFAULT 0")
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS user_avatars (
                user_id INT PRIMARY KEY,
                mime_type VARCHAR(32) NOT NULL,
                image_base64 MEDIUMTEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT fk_user_avatars_user
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS email_verification_pins (
                id INT AUTO_INCREMENT PRIMARY KEY,
                email VARCHAR(255) NOT NULL,
                purpose VARCHAR(50) NOT NULL,
                pin_hash VARCHAR(255) NOT NULL,
                attempts TINYINT UNSIGNED NOT NULL DEFAULT 0,
                expires_at DATETIME NOT NULL,
                consumed_at DATETIME NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_email_pins_lookup (email, purpose, consumed_at),
                INDEX idx_email_pins_expires_at (expires_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS auth_rate_limit_events (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                event_type VARCHAR(32) NOT NULL,
                subject_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_auth_rate_subject (event_type, subject_hash, created_at),
                INDEX idx_auth_rate_created_at (created_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS resume_analyses (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT,
                uploaded_resume_name VARCHAR(255) NOT NULL,
                target_role VARCHAR(255) NOT NULL,
                matching_percentage DECIMAL(5,2) NOT NULL,
                feedback TEXT NOT NULL,
                job_description LONGTEXT NOT NULL,
                resume_excerpt LONGTEXT,
                analysis_json LONGTEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT fk_resume_analyses_user
                    FOREIGN KEY (user_id)
                    REFERENCES users(id)
                    ON DELETE SET NULL,
                INDEX idx_resume_analyses_user_id (user_id),
                INDEX idx_resume_analyses_created_at (created_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        )
        ensure_column(cursor, database_name, "resume_analyses", "user_id", "user_id INT")
        ensure_column(cursor, database_name, "resume_analyses", "analysis_json", "analysis_json LONGTEXT")
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS download_history (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT,
                analysis_id INT,
                downloaded_file_name VARCHAR(255) NOT NULL,
                report_type VARCHAR(50) NOT NULL DEFAULT 'resume_comments',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT fk_download_history_user
                    FOREIGN KEY (user_id)
                    REFERENCES users(id)
                    ON DELETE SET NULL,
                CONSTRAINT fk_download_history_analysis
                    FOREIGN KEY (analysis_id)
                    REFERENCES resume_analyses(id)
                    ON DELETE SET NULL,
                INDEX idx_download_history_user_id (user_id),
                INDEX idx_download_history_analysis_id (analysis_id),
                INDEX idx_download_history_created_at (created_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        )
        cursor.execute(MYSQL_RESUME_FILES_TABLE)
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS job_roles (
                id INT AUTO_INCREMENT PRIMARY KEY,
                role_name VARCHAR(255) NOT NULL UNIQUE,
                masco_description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS job_requirements (
                id INT AUTO_INCREMENT PRIMARY KEY,
                role_id INT NOT NULL,
                requirement_type VARCHAR(50) NOT NULL,
                requirement_text TEXT NOT NULL,
                importance_weight TINYINT UNSIGNED NOT NULL DEFAULT 3,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT fk_job_requirements_role
                    FOREIGN KEY (role_id)
                    REFERENCES job_roles(id)
                    ON DELETE CASCADE,
                INDEX idx_job_requirements_role_id (role_id),
                INDEX idx_job_requirements_type (requirement_type)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        )
        connection.commit()
    except Exception:
        if connection is not None:
            connection.rollback()
        raise
    finally:
        try:
            if cursor is not None:
                cursor.close()
        finally:
            if connection is not None:
                connection.close()


POSTGRES_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        full_name VARCHAR(255) NOT NULL,
        email VARCHAR(255) NOT NULL UNIQUE,
        password_hash VARCHAR(255) NOT NULL,
        email_verified_at TIMESTAMP NULL,
        is_admin SMALLINT NOT NULL DEFAULT 0 CHECK (is_admin IN (0, 1)),
        created_at TIMESTAMP DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')
    )
    """,
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified_at TIMESTAMP NULL",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_admin SMALLINT NOT NULL DEFAULT 0",
    """
    CREATE TABLE IF NOT EXISTS user_avatars (
        user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        mime_type VARCHAR(32) NOT NULL,
        image_base64 TEXT NOT NULL,
        updated_at TIMESTAMP DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS email_verification_pins (
        id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        email VARCHAR(255) NOT NULL,
        purpose VARCHAR(50) NOT NULL,
        pin_hash VARCHAR(255) NOT NULL,
        attempts SMALLINT NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 255),
        expires_at TIMESTAMP NOT NULL,
        consumed_at TIMESTAMP NULL,
        created_at TIMESTAMP DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_email_pins_lookup ON email_verification_pins (email, purpose, consumed_at)",
    "CREATE INDEX IF NOT EXISTS idx_email_pins_expires_at ON email_verification_pins (expires_at)",
    """
    CREATE TABLE IF NOT EXISTS auth_rate_limit_events (
        id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        event_type VARCHAR(32) NOT NULL,
        subject_hash CHAR(64) NOT NULL,
        created_at TIMESTAMP DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_auth_rate_subject ON auth_rate_limit_events (event_type, subject_hash, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_auth_rate_created_at ON auth_rate_limit_events (created_at)",
    """
    CREATE TABLE IF NOT EXISTS resume_analyses (
        id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
        uploaded_resume_name VARCHAR(255) NOT NULL,
        target_role VARCHAR(255) NOT NULL,
        matching_percentage NUMERIC(5,2) NOT NULL,
        feedback TEXT NOT NULL,
        job_description TEXT NOT NULL,
        resume_excerpt TEXT,
        analysis_json TEXT,
        created_at TIMESTAMP DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')
    )
    """,
    "ALTER TABLE resume_analyses ADD COLUMN IF NOT EXISTS user_id INTEGER",
    "ALTER TABLE resume_analyses ADD COLUMN IF NOT EXISTS analysis_json TEXT",
    "CREATE INDEX IF NOT EXISTS idx_resume_analyses_user_id ON resume_analyses (user_id)",
    "CREATE INDEX IF NOT EXISTS idx_resume_analyses_created_at ON resume_analyses (created_at)",
    POSTGRES_RESUME_FILES_TABLE,
    """
    CREATE TABLE IF NOT EXISTS download_history (
        id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
        analysis_id INTEGER REFERENCES resume_analyses(id) ON DELETE SET NULL,
        downloaded_file_name VARCHAR(255) NOT NULL,
        report_type VARCHAR(50) NOT NULL DEFAULT 'resume_comments',
        created_at TIMESTAMP DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_download_history_user_id ON download_history (user_id)",
    "CREATE INDEX IF NOT EXISTS idx_download_history_analysis_id ON download_history (analysis_id)",
    "CREATE INDEX IF NOT EXISTS idx_download_history_created_at ON download_history (created_at)",
    """
    CREATE TABLE IF NOT EXISTS job_roles (
        id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        role_name VARCHAR(255) NOT NULL UNIQUE,
        masco_description TEXT,
        created_at TIMESTAMP DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC'),
        updated_at TIMESTAMP DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')
    )
    """,
    """
    CREATE OR REPLACE FUNCTION ats_private.touch_job_roles_updated_at()
    RETURNS TRIGGER LANGUAGE plpgsql AS $$
    BEGIN
        NEW.updated_at = CURRENT_TIMESTAMP AT TIME ZONE 'UTC';
        RETURN NEW;
    END;
    $$
    """,
    """
    CREATE OR REPLACE TRIGGER trg_job_roles_updated_at
    BEFORE UPDATE ON job_roles
    FOR EACH ROW EXECUTE FUNCTION ats_private.touch_job_roles_updated_at()
    """,
    """
    CREATE TABLE IF NOT EXISTS job_requirements (
        id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        role_id INTEGER NOT NULL REFERENCES job_roles(id) ON DELETE CASCADE,
        requirement_type VARCHAR(50) NOT NULL,
        requirement_text TEXT NOT NULL,
        importance_weight SMALLINT NOT NULL DEFAULT 3 CHECK (importance_weight BETWEEN 0 AND 255),
        created_at TIMESTAMP DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_job_requirements_role_id ON job_requirements (role_id)",
    "CREATE INDEX IF NOT EXISTS idx_job_requirements_type ON job_requirements (requirement_type)",
)


def init_postgres_database() -> None:
    """Create or upgrade the app's tables in a private Supabase schema."""
    _require_postgres_driver()
    get_postgres_pool_size()
    connection = None
    cursor = None
    try:
        connection = psycopg.connect(
            get_postgres_database_url(), sslmode="require", prepare_threshold=None
        )
        cursor = connection.cursor()
        cursor.execute(f"CREATE SCHEMA IF NOT EXISTS {POSTGRES_SCHEMA}")
        cursor.execute(f"SET search_path TO {POSTGRES_SCHEMA}")
        for statement in POSTGRES_SCHEMA_STATEMENTS:
            cursor.execute(statement)
        connection.commit()
    except Exception:
        if connection is not None:
            connection.rollback()
        raise
    finally:
        try:
            if cursor is not None:
                cursor.close()
        finally:
            if connection is not None:
                connection.close()

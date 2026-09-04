from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import mysql.connector  # type: ignore
from dotenv import load_dotenv  # type: ignore
from mysql.connector import errorcode  # type: ignore


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_ENV_PATH = PROJECT_ROOT / "config" / "env"
ROOT_ENV_PATH = PROJECT_ROOT / "env"

load_dotenv(CONFIG_ENV_PATH if CONFIG_ENV_PATH.exists() else ROOT_ENV_PATH)

DatabaseError = mysql.connector.Error


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


def get_db_connection():
    return mysql.connector.connect(**get_mysql_config())


def is_duplicate_entry_error(error: Exception) -> bool:
    return getattr(error, "errno", None) == errorcode.ER_DUP_ENTRY


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


def init_mysql_database() -> None:
    database_name = get_database_name()
    connection = mysql.connector.connect(**get_mysql_config(include_database=False))
    cursor = connection.cursor()
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
    cursor.close()
    connection.close()

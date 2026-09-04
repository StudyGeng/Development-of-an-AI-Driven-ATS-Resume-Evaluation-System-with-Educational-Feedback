CREATE DATABASE IF NOT EXISTS career_navigator
CHARACTER SET utf8mb4
COLLATE utf8mb4_unicode_ci;

USE career_navigator;

CREATE TABLE IF NOT EXISTS users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    full_name VARCHAR(255) NOT NULL,
    email VARCHAR(255) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    email_verified_at TIMESTAMP NULL,
    is_admin TINYINT UNSIGNED NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS job_roles (
    id INT AUTO_INCREMENT PRIMARY KEY,
    role_name VARCHAR(255) NOT NULL UNIQUE,
    masco_description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

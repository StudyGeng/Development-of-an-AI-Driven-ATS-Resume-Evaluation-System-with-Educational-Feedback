from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import secrets
import shutil
import base64
import ssl
from collections import Counter
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from html import escape as html_escape
from io import BytesIO
import smtplib
import urllib.error
import urllib.request
from time import perf_counter
from urllib.parse import urlsplit
from typing import Any, NamedTuple

from database import (
    DatabaseError,
    DatabaseErrors,
    get_db_connection,
    is_postgres_backend,
    is_duplicate_entry_error,
    resume_files_table_sql,
    utc_now_sql,
)
from data_cleaning import (
    build_job_data_cleaning_plan,
    build_job_data_quality_report,
)
from job_data_insights import build_job_data_insights
from flask import Flask, jsonify, redirect, request, send_file, send_from_directory, session
from pypdf import PdfReader, PdfWriter
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.security import check_password_hash, generate_password_hash

from admin_helpers import (
    clamp_admin_score,
    estimate_after_recommendation_score,
    isoformat_datetime,
    parse_stored_analysis_json,
)
from analysis_helpers import compute_tfidf_score, get_scoring_cache, scoring_cache_scope
from app_config import (
    ACCURACY_TESTING_PENDING_FILE,
    ACCURACY_TESTING_WORKBOOK,
    ANALYSIS_RATE_WINDOW_MINUTES,
    ANALYSIS_REQUEST_LIMIT_PER_IP,
    APP_TITLE,
    CORS_ALLOWED_ORIGINS,
    CROSS_SITE_SESSION_COOKIES,
    CS_SAMPLE_ONLY_TOP_MATCH_THRESHOLD,
    EMAIL_PIN_ATTEMPT_LIMIT_PER_IP,
    EmailPinConfigurationError,
    EMAIL_PIN_LENGTH,
    EMAIL_PIN_MAX_ATTEMPTS,
    EMAIL_PIN_PURPOSES,
    EMAIL_PIN_RATE_WINDOW_MINUTES,
    EMAIL_PIN_REQUEST_LIMIT_PER_EMAIL,
    EMAIL_PIN_REQUEST_LIMIT_PER_IP,
    EMAIL_PIN_REQUEST_WINDOW_MINUTES,
    EMAIL_PIN_TTL_MINUTES,
    email_pin_dev_mode_requested,
    FRONTEND_DIR,
    get_accuracy_testing_auto_export_enabled,
    get_email_pin_delivery_mode,
    get_app_environment,
    get_flask_secret_key,
    IMAGE_EXTENSIONS,
    is_production_environment,
    LOGIN_RATE_WINDOW_MINUTES,
    LOGIN_REQUEST_LIMIT_PER_EMAIL,
    LOGIN_REQUEST_LIMIT_PER_IP,
    MAX_FORM_MEMORY_BYTES,
    MAX_FORM_PARTS,
    MAX_RESUME_UPLOAD_BYTES,
    PDF_EXTENSIONS,
    RESUME_GENERATION_MAX_TEXT_LENGTH,
    SESSION_LIFETIME_DAYS,
    PARTITIONED_SESSION_COOKIES,
    SUPPORTED_RESUME_EXTENSIONS,
    TRUSTED_PROXY_COUNT,
)
from core_utils import (
    count_resume_words,
    get_file_extension,
    normalize_match_text,
    normalize_text,
    role_similarity_score,
)
from export_helpers import (
    get_link_label,
    normalize_provider_resume_link,
    normalize_resume_link,
    percent_status_label,
)
from job_repository import (
    apply_job_data_cleaning_plan,
    fetch_job_catalog,
    fetch_job_data_for_insights,
    get_job_catalog_version,
    fetch_job_requirements,
    fetch_job_roles,
    fetch_raw_job_data_rows,
)
from models import ParsedResume
from security import (
    clear_login_session,
    get_password_validation_error,
    is_configured_admin_email,
    is_user_admin,
    is_valid_email,
    normalize_email,
    normalize_pin,
    normalize_pin_purpose,
    save_login_session,
    serialize_user,
    utc_now_naive,
    validate_admin_email_configuration,
)

try:
    from PIL import Image, ImageOps, UnidentifiedImageError  # type: ignore
except ImportError:  # pragma: no cover - optional OCR dependency
    Image = None  # type: ignore
    ImageOps = None  # type: ignore
    UnidentifiedImageError = OSError  # type: ignore

try:
    import pytesseract  # type: ignore
except ImportError:  # pragma: no cover - optional OCR dependency
    pytesseract = None  # type: ignore


ROLE_RECOMMENDATION_MARGIN = 8.0
ROLE_RANKING_VERSION = "2.2"
ROLE_REQUIREMENT_FIT_WEIGHT = 0.55
ROLE_EVIDENCE_WEIGHT = 0.10
ROLE_TITLE_WEIGHT = 0.35
ROLE_TITLE_HISTORY_DECAY = 0.85
NON_IT_HEADLINE_REVIEW_THRESHOLD = 35.0
AUTO_NO_MATCH_SAMPLE_ROLES = (
    "Software Developer",
    "Full Stack Developer",
    "Business Data Analyst",
    "Cybersecurity Analyst",
    "Cloud Computing Solution Architect",
)
ROLE_FAMILY_ALIASES = {
    "computer programmer": "software_development",
    "programmer": "software_development",
    "application developer": "software_development",
    "software developer": "software_development",
    "software engineer": "software_development",
    "software designer": "software_development",
    "java consultant": "software_development",
    "net developer": "software_development",
    "backend developer": "software_development",
    "frontend developer": "software_development",
    "full stack developer": "software_development",
    "full stack web developer": "software_development",
    "cyber security analyst": "cybersecurity",
    "cybersecurity analyst": "cybersecurity",
    "cybersecurity executive": "cybersecurity",
    "computer security manager": "cybersecurity",
    "data security specialist": "cybersecurity",
    "digital forensic specialist": "cybersecurity",
    "information security consultant": "cybersecurity",
    "security administrator information technology": "cybersecurity",
    "security technologist specialist": "cybersecurity",
    "database architect": "database",
    "oracle database engineer": "database",
    "sql database developer": "database",
    "business data analyst": "data_ai",
    "data analyst": "data_ai",
    "data scientist": "data_ai",
    "machine learning engineer": "data_ai",
    "research scientist": "data_ai",
    "application consultant cloud": "cloud_devops",
    "cloud computing solution architect": "cloud_devops",
    "cloud engineer": "cloud_devops",
    "devops engineer": "cloud_devops",
    "ux designer user experience": "ui_ux",
    "ui designer": "ui_ux",
    "ui ux designer": "ui_ux",
    "network analyst": "networking",
    "network system engineer information technology": "networking",
    "computer network technician": "networking",
    "web developer": "web_development",
    "website developer": "web_development",
    "web designer": "web_development",
    "hypertext preprocessor php web programmer": "web_development",
}
POPULAR_ROLE_ALIASES = {
    "software engineer": "Software Developer",
    "software developer": "Software Developer",
    "application developer": "Software Developer",
    "backend developer": "Full Stack Developer",
    "frontend developer": "Full Stack Developer",
    "front end developer": "Full Stack Developer",
    "full stack developer": "Full Stack Developer",
    "web developer": "Full Stack Developer",
    "website developer": "Full Stack Developer",
    "web application developer": "Full Stack Developer",
    "web development intern": "Full Stack Developer",
    "front end web developer": "Full Stack Developer",
    "software designer": "Software Designer",
    "java consultant": "Java Consultant",
    "data analyst": "Business Data Analyst",
    "data scientist": "Data Scientist",
    "machine learning engineer": "Machine Learning Engineer",
    "devops engineer": "Cloud Computing Solution Architect",
    "cloud engineer": "Cloud Computing Solution Architect",
    "cybersecurity analyst": "Cybersecurity Analyst",
    "cyber security analyst": "Cybersecurity Analyst",
    "information security analyst": "Cybersecurity Analyst",
    "security analyst": "Cybersecurity Analyst",
    "soc analyst": "Cybersecurity Analyst",
    "security engineer": "Security Technologist Specialist",
    "security administrator": "Security Administrator (Information Technology)",
    "research scientist": "Data Scientist",
    "database engineer": "Oracle Database Engineer",
    "database developer": "SQL Database Developer",
    "sql developer": "SQL Database Developer",
}
ROLE_TITLE_MATCH_ALIASES = {
    "cybersecurity analyst": {
        "cybersecurity analyst",
        "cyber security analyst",
        "cyber-security analyst",
        "information security analyst",
        "security analyst",
        "soc analyst",
    },
    "cybersecurity executive": {
        "cybersecurity executive",
        "cyber security executive",
        "cybersecurity specialist",
        "security executive",
    },
    "computer security manager": {
        "computer security manager",
        "cybersecurity manager",
        "cyber security manager",
        "information security manager",
        "security manager",
    },
    "computer services manager": {
        "computer services manager",
        "information technology manager",
        "it manager",
        "ict manager",
        "technology manager",
        "systems manager",
    },
    "data security specialist": {
        "data security specialist",
        "information security specialist",
        "data protection specialist",
        "cybersecurity specialist",
    },
    "digital forensic specialist": {
        "digital forensic specialist",
        "digital forensics specialist",
        "cyber forensic specialist",
        "incident investigator",
    },
    "information security consultant": {
        "information security consultant",
        "cybersecurity consultant",
        "cyber security consultant",
        "security consultant",
    },
    "security administrator information technology": {
        "security administrator",
        "it security administrator",
        "information security administrator",
        "cybersecurity administrator",
    },
    "security technologist specialist": {
        "security technologist specialist",
        "security technologist",
        "security engineer",
        "cybersecurity engineer",
        "cyber security engineer",
    },
    "sql database developer": {
        "sql database developer",
        "database developer",
        "sql developer",
        "database programmer",
    },
    "oracle database engineer": {
        "oracle database engineer",
        "oracle database administrator",
        "oracle dba",
        "database engineer",
    },
    "database architect": {
        "database architect",
        "data architect",
        "database designer",
    },
    "web designer": {
        "web designer",
        "web design specialist",
        "website designer",
        "ui designer",
        "ux designer",
    },
    "hypertext preprocessor php web programmer": {
        "php web programmer",
        "php developer",
        "web programmer",
        "web application developer",
    },
    "full stack developer": {
        "full stack developer",
        "fullstack developer",
        "full stack web developer",
        "fullstack web developer",
        "full-stack developer",
        "full-stack web developer",
        "web developer",
        "website developer",
        "web application developer",
        "web development intern",
        "web developer intern",
        "front end web developer",
        "frontend web developer",
        "front end developer",
        "frontend developer",
        "back end developer",
        "backend developer",
    },
    "software developer": {
        "software developer",
        "software engineer",
        "application developer",
    },
    "software tester": {
        "software tester",
        "software test engineer",
        "qa engineer",
        "qa tester",
        "quality assurance engineer",
        "quality assurance tester",
        "test engineer",
    },
    "software designer": {
        "software designer",
        "software design engineer",
    },
    "java consultant": {
        "java consultant",
        "java developer",
    },
}
REQUIREMENT_TYPE_LABELS = {
    "task": "Key responsibilities",
    "skill": "Required skills",
    "knowledge": "Knowledge areas",
    "tool": "Tools and technologies",
    "education": "Education background",
    "experience": "Experience",
}
REQUIREMENT_TYPE_ORDER = ["task", "skill", "knowledge", "tool", "education", "experience"]
REQUIREMENT_TYPE_SCORE_WEIGHTS = {
    "task": 1.2,
    "experience": 1.2,
    "skill": 1.1,
    "knowledge": 0.85,
    "education": 0.75,
    "tool": 0.7,
}
KEYWORD_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "candidate",
    "candidates",
    "code",
    "conduct",
    "computer",
    "complete",
    "ensure",
    "for",
    "from",
    "ability",
    "area",
    "areas",
    "background",
    "include",
    "including",
    "if",
    "in",
    "into",
    "is",
    "it",
    "key",
    "knowledge",
    "masco",
    "must",
    "of",
    "on",
    "or",
    "required",
    "requires",
    "requiring",
    "requirement",
    "requirements",
    "responsibilities",
    "role",
    "roles",
    "responsible",
    "skill",
    "skills",
    "such",
    "target",
    "that",
    "the",
    "their",
    "this",
    "to",
    "tools",
    "use",
    "using",
    "with",
}
CANONICAL_KEYWORD_ALIASES = {
    "analysed": "analysis",
    "analyze": "analysis",
    "analyzed": "analysis",
    "analyzing": "analysis",
    "applications": "application",
    "apps": "application",
    "apis": "api",
    "assessed": "assess",
    "assesses": "assess",
    "assessing": "assess",
    "assessments": "assessment",
    "back-end": "backend",
    "back-end.": "backend",
    "building": "build",
    "built": "build",
    "controls": "control",
    "coding": "code",
    "created": "create",
    "creating": "create",
    "cyber-security": "cybersecurity",
    "databases": "database",
    "designed": "design",
    "designing": "design",
    "designs": "design",
    "debugged": "debug",
    "debugging": "debug",
    "debugs": "debug",
    "decreased": "decrease",
    "decreasing": "decrease",
    "developed": "develop",
    "developer": "develop",
    "developers": "develop",
    "developing": "develop",
    "development": "develop",
    "develops": "develop",
    "delivered": "deliver",
    "delivering": "deliver",
    "enabled": "enable",
    "enabling": "enable",
    "engineered": "engineer",
    "engineering": "engineer",
    "engineers": "engineer",
    "evaluated": "evaluate",
    "evaluating": "evaluate",
    "fixed": "fix",
    "fixing": "fix",
    "front-end": "frontend",
    "front-end.": "frontend",
    "full-stack": "fullstack",
    "incidents": "incident",
    "inspections": "inspection",
    "investigated": "investigate",
    "investigating": "investigate",
    "investigation": "investigate",
    "implementing": "implement",
    "implemented": "implement",
    "implements": "implement",
    "maintained": "maintain",
    "maintaining": "maintain",
    "maintains": "maintain",
    "node.js": "node",
    "nodejs": "node",
    "optimized": "optimize",
    "optimizing": "optimize",
    "procedures": "procedure",
    "programmer": "program",
    "programmers": "program",
    "programming": "program",
    "programs": "program",
    "resolved": "resolve",
    "resolving": "resolve",
    "restful": "rest",
    "react.js": "react",
    "risks": "risk",
    "specifications": "specification",
    "troubleshooting": "troubleshoot",
    "troubleshoots": "troubleshoot",
    "threats": "threat",
    "systems": "system",
    "technologies": "technology",
    "tested": "test",
    "testing": "test",
    "tests": "test",
    "viruses": "virus",
    "vulnerabilities": "vulnerability",
}
SEMANTIC_KEYWORD_EQUIVALENTS = {
    "analysis": {"analyze", "analytics", "evaluate", "assess", "review"},
    "application": {"app", "software", "platform", "system"},
    "backend": {"back", "back-end", "server", "api", "database", "node", "django", "flask", "php"},
    "build": {"built", "building", "create", "develop", "implement", "deliver", "engineer"},
    "cloud": {"aws", "azure", "gcp", "docker", "kubernetes", "devops", "deployment"},
    "complex": {"advanced", "challenging", "problem", "system"},
    "computing": {"cloud", "technology", "infrastructure"},
    "critical": {"analysis", "problem", "solve", "decision"},
    "css": {"html", "tailwind", "bootstrap", "frontend"},
    "debug": {"troubleshoot", "resolve", "fix", "diagnose", "support"},
    "decrease": {"reduce", "reduced", "improve", "optimized", "save"},
    "design": {"architect", "architecture", "model", "plan", "prototype", "ux", "ui"},
    "develop": {"build", "built", "create", "implement", "code", "program", "engineer"},
    "dotnet": {"c#", "csharp", "aspnet", "backend"},
    "frontend": {"front", "front-end", "ui", "ux", "html", "css", "javascript", "typescript", "react", "angular", "bootstrap"},
    "fullstack": {"full", "stack", "frontend", "backend", "front-end", "back-end", "web"},
    "hardware": {"computer", "device", "infrastructure", "network"},
    "javascript": {"js", "typescript", "react", "node", "frontend"},
    "jquery": {"javascript", "js", "react", "frontend"},
    "maintain": {"support", "improve", "optimize", "refactor", "debug", "update", "enhance"},
    "program": {"code", "coding", "develop", "software"},
    "responsive": {"mobile", "cross-platform", "frontend", "web", "ui"},
    "security": {"cybersecurity", "cyber", "threat", "risk", "malware", "vulnerability", "incident", "attack", "protection"},
    "sql": {"mysql", "postgresql", "sqlalchemy", "oracle", "plsql", "tsql"},
    "specification": {"requirement", "documentation", "technical", "design"},
    "system": {"application", "platform", "backend", "service", "api", "database", "software"},
    "test": {"qa", "validation", "unit", "integration", "pytest", "jest", "sdlc"},
    "thinking": {"analysis", "problem", "solve", "decision"},
    "tomcat": {"apache", "server", "deployment", "java", "backend"},
    "troubleshoot": {"debug", "resolve", "fix", "diagnose", "support"},
    "vulnerability": {"risk", "weakness", "exposure"},
}
STRICT_TECHNICAL_KEYWORD_EVIDENCE = {
    "dotnet": {"dotnet"},
    "jquery": {"jquery"},
    "sql": {"sql", "mysql", "postgresql", "sqlalchemy", "oracle", "plsql", "tsql"},
    "mysql": {"mysql"},
    "postgresql": {"postgresql"},
    "oracle": {"oracle"},
    "tomcat": {"tomcat"},
}
SOFTWARE_DEVELOPER_EVIDENCE_GROUPS = [
    {"software", "develop", "program", "code", "python", "java", "javascript", "typescript", "c#", "c++"},
    {"api", "rest", "flask", "django", "fastapi", "react", "node", "html", "css", "frontend", "backend"},
    {"sql", "mysql", "postgresql", "sqlalchemy", "database", "orm"},
    {"test", "debug", "validation", "qa", "sdlc", "troubleshoot"},
    {"design", "architecture", "system", "application", "platform"},
    {"git", "docker", "kubernetes", "aws", "azure", "gcp", "cloud", "deployment", "ci", "cd"},
]
ROLE_SPECIFIC_SOFTWARE_EVIDENCE_GROUPS = {
    "computer programmer": [
        {"python", "java", "javascript", "typescript", "c#", "c++", "php", "ruby", "go", "kotlin", "swift", "code", "program"},
        {"algorithm", "data structure", "complexity", "object oriented", "oop"},
        {"develop", "implement", "build", "application", "software"},
        {"test", "debug", "validation", "troubleshoot"},
        {"git", "version control", "document", "maintain", "optimize", "refactor"},
    ],
    "software developer": [
        {"python", "java", "javascript", "typescript", "c#", "c++", "php", "code", "program"},
        {"software", "develop", "application", "system", "sdlc", "implement"},
        {"api", "rest", "backend", "frontend", "database", "sql"},
        {"test", "debug", "validation", "qa", "troubleshoot"},
        {"git", "code review", "requirement", "document", "team"},
        {"docker", "deployment", "ci", "cd", "cloud", "aws", "azure", "gcp"},
    ],
    "software designer": [
        {"design", "architecture", "architect", "pattern"},
        {"requirement", "specification", "model", "uml"},
        {"ui", "ux", "interface", "prototype", "wireframe"},
        {"software", "application", "system", "platform"},
        {"document", "communication", "team", "stakeholder"},
        {"validation", "evaluate", "test", "research"},
    ],
    "full stack developer": [
        {"frontend", "html", "css", "javascript", "typescript", "react", "angular", "vue"},
        {"backend", "server", "node", "flask", "django", "fastapi", "php", "java"},
        {"api", "rest", "service", "integration"},
        {"database", "sql", "mysql", "postgresql", "mongodb", "orm"},
        {"authentication", "authorization", "security", "session"},
        {"git", "test", "docker", "deployment", "ci", "cd", "cloud"},
    ],
}
CYBERSECURITY_EVIDENCE_GROUPS = [
    {"cybersecurity", "cyber", "security", "information security", "network security", "soc"},
    {"threat", "risk", "mitigate", "malware", "virus", "attack", "vulnerability", "incident", "intrusion"},
    {"assessment", "audit", "penetration", "nist", "inspection", "security plan", "forensic", "digital evidence"},
    {"data protection", "dlp", "policy", "access", "control", "compliance", "secure"},
    {"network", "firewall", "endpoint", "computer", "system", "infrastructure"},
    {"monitor", "detect", "respond", "investigate", "analysis"},
]
DATABASE_EVIDENCE_GROUPS = [
    {"database", "dbms", "sql", "mysql", "postgresql", "oracle", "mongodb"},
    {"query", "schema", "table", "data model", "database design", "data architecture"},
    {"administration", "management", "backup", "performance", "optimize", "maintain"},
    {"stored procedure", "etl", "data warehouse", "data migration", "replication"},
    {"data", "integrity", "validation", "report", "analytics"},
]
NETWORK_EVIDENCE_GROUPS = [
    {"network", "networking", "telecommunication", "infrastructure"},
    {"routing", "cisco", "switch", "router", "lan", "wan"},
    {"configure", "troubleshoot", "monitor", "performance", "optimize"},
    {"network security", "firewall", "intrusion", "vpn", "access control"},
    {"hardware", "cable", "server", "operating system"},
]
QA_TESTING_EVIDENCE_GROUPS = [
    {"qa", "quality assurance", "tester", "test", "testing", "validation"},
    {"bug", "defect", "debug", "troubleshoot", "issue"},
    {"test plan", "test case", "automation", "selenium", "unit", "integration"},
    {"software", "application", "system", "web", "mobile"},
]
WEB_DEVELOPMENT_EVIDENCE_GROUPS = [
    {"web", "website", "web application", "web page", "frontend", "front-end"},
    {"html", "css", "javascript", "jquery", "xml", "php", "wordpress", "dreamweaver"},
    {"design", "layout", "ui", "ux", "responsive", "navigation", "color", "visual"},
    {"develop", "build", "create", "implement", "code", "bug", "enhancement", "functionality"},
    {"user", "customer", "client", "conversion", "click", "performance", "security"},
    {"backend", "server", "database", "oracle", "sql", ".net", "application"},
]
DEFAULT_SKILL_DICTIONARY = [
    ".net",
    "abap",
    "ajax",
    "angular",
    "angularjs",
    "api",
    "apache ant",
    "asp.net",
    "backend",
    "back-end",
    "blackboard",
    "bootstrap",
    "c#",
    "c++",
    "ci/cd",
    "cloud computing",
    "computer programming",
    "cissp",
    "cybersecurity",
    "data structures",
    "data protection",
    "data management",
    "debugging",
    "digital forensics",
    "deployment",
    "dlp",
    "dreamweaver",
    "frontend",
    "front-end",
    "full-stack",
    "java",
    "jquery",
    "operations analysis",
    "node",
    "node.js",
    "oop",
    "php",
    "powershell",
    "quality assurance",
    "rest api",
    "restful api",
    "sdlc",
    "security+",
    "security audit",
    "security assessment",
    "security plans",
    "nist",
    "penetration testing",
    "intrusion detection",
    "malware analysis",
    "network security",
    "threat analysis",
    "software development",
    "software testing",
    "spring mvc",
    "spring soa",
    "system design",
    "tailwind",
    "typescript",
    "qa",
    "database",
    "mobile app",
    "visual basic",
    "wordpress",
    "xml",
    "web app",
    "web application",
    "web technologies",
    "website",
    "python",
    "sql",
    "data analysis",
    "machine learning",
    "deep learning",
    "nlp",
    "nosql",
    "mongodb",
    "apache spark",
    "objective c",
    "flask",
    "django",
    "fastapi",
    "docker",
    "kubernetes",
    "aws",
    "azure",
    "gcp",
    "git",
    "linux",
    "tableau",
    "power bi",
    "excel",
    "statistics",
    "communication",
    "leadership",
    "team leadership",
    "business analysis",
    "project management",
    "technical writing",
    "troubleshooting",
    "wireshark",
    "ansible",
    "solarwinds",
    "trello",
    "budgeting",
    "negotiation",
    "problem solving",
    "research",
    "tensorflow",
    "pytorch",
    "vulnerability assessment",
    "react",
    "javascript",
    "html",
    "css",
    "mysql",
    "postgresql",
    "supabase",
]
COMMON_EDUCATION_MARKERS = ["bachelor", "master", "phd", "b.sc", "m.sc", "degree", "university", "college"]
COMMON_EXPERIENCE_MARKERS = [
    "intern",
    "internship",
    "project",
    "experience",
    "responsibility",
    "worked",
    "developed",
    "built",
    "designed",
    "deployed",
    "tested",
    "led",
    "managed",
    "assisted",
    "automated",
    "compiled",
    "conducted",
    "contributed",
    "coordinated",
    "decreased",
    "executed",
    "fixed",
    "improved",
    "implemented",
    "launched",
    "maintained",
    "optimized",
    "partnered",
    "performed",
    "redesigned",
    "revamped",
    "reduced",
    "supported",
    "trained",
]
CORE_RESUME_SECTION_MARKERS = [
    "about me",
    "achievements",
    "awards",
    "certifications",
    "certificates",
    "education",
    "experience",
    "work experience",
    "projects",
    "skills",
    "technical skills",
]
CONTACT_MARKERS = ["linkedin", "github", "portfolio"]
ENGLISH_LANGUAGE_MARKERS = {
    "and",
    "with",
    "for",
    "experience",
    "education",
    "skills",
    "project",
    "developed",
    "software",
    "engineer",
    "university",
    "work",
}
MALAY_LANGUAGE_MARKERS = {
    "dan",
    "yang",
    "dengan",
    "untuk",
    "saya",
    "adalah",
    "pengalaman",
    "kemahiran",
    "pendidikan",
    "universiti",
    "projek",
    "pembangunan",
    "perisian",
    "kerja",
}


FLASK_SESSION_SECRET_KEY = get_flask_secret_key()
# Validate hosted PIN delivery at startup without retaining an unused setting.
get_email_pin_delivery_mode()
validate_admin_email_configuration(require_configured_admin=is_production_environment())

app = Flask(__name__)
app.secret_key = FLASK_SESSION_SECRET_KEY
app.config.update(
    APP_ENV=get_app_environment(),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="None" if CROSS_SITE_SESSION_COOKIES else "Lax",
    SESSION_COOKIE_SECURE=is_production_environment() or CROSS_SITE_SESSION_COOKIES,
    SESSION_COOKIE_PARTITIONED=PARTITIONED_SESSION_COOKIES,
    PERMANENT_SESSION_LIFETIME=timedelta(days=SESSION_LIFETIME_DAYS),
    SESSION_REFRESH_EACH_REQUEST=True,
    MAX_CONTENT_LENGTH=MAX_RESUME_UPLOAD_BYTES,
    MAX_FORM_MEMORY_SIZE=MAX_FORM_MEMORY_BYTES,
    MAX_FORM_PARTS=MAX_FORM_PARTS,
    CSRF_PROTECTION_ENABLED=True,
    ENFORCE_CSRF_IN_TESTS=False,
    ENFORCE_RATE_LIMITS_IN_TESTS=False,
)
if TRUSTED_PROXY_COUNT:
    app.wsgi_app = ProxyFix(
        app.wsgi_app,
        x_for=TRUSTED_PROXY_COUNT,
        x_proto=TRUSTED_PROXY_COUNT,
    )


CSRF_SESSION_KEY = "csrf_token"
SAFE_HTTP_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}
MAX_AVATAR_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_AVATAR_PIXELS = 12_000_000
MAX_AVATAR_EDGE = 4096
AVATAR_OUTPUT_EDGE = 512
CONTENT_SECURITY_POLICY = "; ".join(
    (
        "default-src 'self'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
        "object-src 'none'",
        "script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com",
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
        "font-src 'self' https://fonts.gstatic.com data:",
        "img-src 'self' data: blob: https://lh3.googleusercontent.com",
        "connect-src 'self'",
        "worker-src 'self' blob: https://cdnjs.cloudflare.com",
    )
)


def normalize_origin(value: str) -> str:
    """Return a scheme-and-host origin without trusting paths or malformed values."""
    try:
        parsed = urlsplit(str(value or "").strip())
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def request_same_origin() -> str:
    return normalize_origin(request.host_url)


def is_trusted_browser_origin(origin: str) -> bool:
    normalized_origin = normalize_origin(origin)
    if not normalized_origin:
        return False
    allowed_origins = {normalize_origin(value) for value in CORS_ALLOWED_ORIGINS}
    return normalized_origin == request_same_origin() or normalized_origin in allowed_origins


def csrf_protection_is_enabled() -> bool:
    if not app.config.get("CSRF_PROTECTION_ENABLED", True):
        return False
    return not (
        app.config.get("TESTING")
        and not app.config.get("ENFORCE_CSRF_IN_TESTS", False)
    )


def get_or_create_csrf_token() -> str:
    token = session.get(CSRF_SESSION_KEY)
    if isinstance(token, str) and len(token) >= 32:
        return token
    token = secrets.token_urlsafe(32)
    session[CSRF_SESSION_KEY] = token
    return token


@app.before_request
def enforce_csrf_protection():
    """Require an origin check and per-session token for every state-changing request."""
    if request.method in SAFE_HTTP_METHODS or not csrf_protection_is_enabled():
        return None

    origin = request.headers.get("Origin", "")
    referer = request.headers.get("Referer", "")
    if not is_trusted_browser_origin(origin or referer):
        return jsonify({"detail": "CSRF validation failed."}), 403

    expected_token = session.get(CSRF_SESSION_KEY)
    supplied_token = request.headers.get("X-CSRF-Token", "")
    if (
        not isinstance(expected_token, str)
        or not supplied_token
        or not hmac.compare_digest(expected_token, supplied_token)
    ):
        return jsonify({"detail": "CSRF validation failed."}), 403
    return None


@app.get("/auth/csrf-token")
def get_csrf_token():
    response = jsonify({"csrf_token": get_or_create_csrf_token()})
    response.headers["Cache-Control"] = "no-store"
    return response


@app.errorhandler(RequestEntityTooLarge)
def handle_request_entity_too_large(_error):
    maximum_megabytes = MAX_RESUME_UPLOAD_BYTES / (1024 * 1024)
    return (
        jsonify({"detail": f"Upload must not exceed {maximum_megabytes:g} MB."}),
        413,
    )


@app.after_request
def apply_browser_security_headers(response):
    """Apply a single, auditable browser-security policy to every response."""
    response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Permissions-Policy",
        "camera=(), geolocation=(), microphone=(), payment=(), usb=()",
    )
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")

    if is_production_environment() and request.is_secure:
        response.headers.setdefault(
            "Strict-Transport-Security",
            "max-age=31536000; includeSubDomains",
        )

    origin = request.headers.get("Origin", "")
    if CORS_ALLOWED_ORIGINS:
        vary_values = [
            value.strip()
            for value in response.headers.get("Vary", "").split(",")
            if value.strip()
        ]
        if "Origin" not in vary_values:
            vary_values.append("Origin")
        response.headers["Vary"] = ", ".join(vary_values)
    if origin in CORS_ALLOWED_ORIGINS:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-CSRF-Token"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
        response.headers["Access-Control-Max-Age"] = "600"
    return response


def get_authenticated_session_user() -> dict[str, Any] | None:
    """Return the live database user for this session, or clear an invalid session."""
    try:
        session_user_id = int(session.get("user_id") or 0)
    except (TypeError, ValueError):
        session_user_id = 0

    if session_user_id <= 0:
        clear_login_session()
        return None

    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT id, full_name, email, email_verified_at, is_admin, created_at
            FROM users
            WHERE id = %s
            """,
            (session_user_id,),
        )
        current_user = cursor.fetchone()
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()

    if not current_user:
        clear_login_session()
        return None

    current_user["id"] = int(current_user["id"])
    # Upgrade older valid browser-session cookies to the persistent policy and
    # refresh their bounded expiry while the account remains active.
    session.permanent = True
    session["user_id"] = current_user["id"]
    session["email"] = normalize_email(str(current_user.get("email") or ""))
    session["is_admin"] = is_user_admin(current_user)
    return current_user


def require_admin_json_response():
    """Require a live database user with current administrator permission."""
    try:
        current_user = get_authenticated_session_user()
    except DatabaseErrors:
        return jsonify({"detail": "Database error while validating administrator access"}), 500

    if not current_user:
        return jsonify({"detail": "Authentication required"}), 401
    if not is_user_admin(current_user):
        return jsonify({"detail": "Admin access required"}), 403
    return None


def authenticate_json_request() -> tuple[dict[str, Any] | None, Any | None]:
    """Return the live session user or a JSON authentication error."""
    try:
        current_user = get_authenticated_session_user()
    except DatabaseErrors:
        return None, (jsonify({"detail": "Database error while validating session"}), 500)

    if not current_user:
        return None, (jsonify({"detail": "Authentication required"}), 401)
    return current_user, None


def email_pin_subject(purpose: str) -> str:
    if purpose == "password_reset":
        return "Your UTS CS Career Navigator password reset PIN"
    if purpose.startswith("email_change_"):
        return "Confirm your new UTS CS Career Navigator email"
    return "Your UTS CS Career Navigator email verification PIN"


def email_pin_body(pin: str, purpose: str) -> str:
    if purpose == "password_reset":
        action = "reset your password"
    elif purpose.startswith("email_change_"):
        action = "confirm your new email address"
    else:
        action = "verify your email address"
    return (
        f"Your UTS CS Career Navigator PIN is {pin}.\n\n"
        f"Use this PIN to {action}. It expires in {EMAIL_PIN_TTL_MINUTES} minutes.\n\n"
        "If you did not request this, you can ignore this email."
    )


class RequestRateLimitError(Exception):
    def __init__(self, detail: str, retry_after_seconds: int):
        super().__init__(detail)
        self.detail = detail
        self.retry_after_seconds = max(1, int(retry_after_seconds))


class EmailPinRateLimitError(RequestRateLimitError):
    """Retained for the PIN workflow's public error contract."""


def build_rate_limit_response(error: RequestRateLimitError):
    response = jsonify(
        {
            "detail": error.detail,
            "retry_after_seconds": error.retry_after_seconds,
        }
    )
    response.status_code = 429
    response.headers["Retry-After"] = str(error.retry_after_seconds)
    return response


def hash_rate_limit_subject(event_type: str, subject: str) -> str:
    secret_key = app.secret_key
    key_bytes = secret_key if isinstance(secret_key, bytes) else str(secret_key).encode("utf-8")
    message = f"{event_type}:{subject}".encode("utf-8")
    return hmac.new(key_bytes, message, hashlib.sha256).hexdigest()


def normalize_rate_limit_client_ip(client_ip: str | None) -> str:
    return str(client_ip or "unknown").strip().lower()[:255] or "unknown"


def get_request_client_ip() -> str:
    return normalize_rate_limit_client_ip(request.remote_addr)


def get_rate_limit_retry_after(
    oldest_event_at: Any,
    window_minutes: int,
    now: datetime,
) -> int:
    if not isinstance(oldest_event_at, datetime):
        return max(1, int(window_minutes * 60))
    if oldest_event_at.tzinfo is not None:
        oldest_event_at = oldest_event_at.astimezone(timezone.utc).replace(tzinfo=None)
    reset_at = oldest_event_at + timedelta(minutes=window_minutes)
    return max(1, int((reset_at - now).total_seconds()) + 1)


def enforce_rate_limit_specs(
    limit_specs: list[tuple[str, str, int, int]],
    detail: str,
    error_type: type[RequestRateLimitError] = RequestRateLimitError,
) -> None:
    """Store hashed rate-limit events so limits survive worker restarts and scale out."""
    now = utc_now_naive()
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        pending_events: list[tuple[str, str]] = []

        for event_type, subject, limit, window_minutes in limit_specs:
            subject_hash = hash_rate_limit_subject(event_type, subject)
            window_start = now - timedelta(minutes=window_minutes)
            cursor.execute(
                """
                SELECT id, created_at
                FROM auth_rate_limit_events
                WHERE event_type = %s
                  AND subject_hash = %s
                  AND created_at >= %s
                ORDER BY created_at ASC, id ASC
                FOR UPDATE
                """,
                (event_type, subject_hash, window_start),
            )
            recent_events = cursor.fetchall()
            if len(recent_events) >= limit:
                retry_after = get_rate_limit_retry_after(
                    recent_events[0].get("created_at"),
                    window_minutes,
                    now,
                )
                raise error_type(detail, retry_after)
            pending_events.append((event_type, subject_hash))

        for event_type, subject_hash in pending_events:
            cursor.execute(
                """
                INSERT INTO auth_rate_limit_events (event_type, subject_hash, created_at)
                VALUES (%s, %s, %s)
                """,
                (event_type, subject_hash, now),
            )

        retention_minutes = max(window_minutes for _, _, _, window_minutes in limit_specs) * 2
        cursor.execute(
            "DELETE FROM auth_rate_limit_events WHERE created_at < %s",
            (now - timedelta(minutes=retention_minutes),),
        )
        connection.commit()
    except Exception:
        if connection is not None:
            connection.rollback()
        raise
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()


def enforce_email_pin_rate_limits(
    event_kind: str,
    email: str,
    client_ip: str,
    purpose: str = "",
) -> None:
    """Apply durable, privacy-preserving PIN limits shared by all app workers."""
    normalized_email = normalize_email(email)
    normalized_purpose = normalize_pin_purpose(purpose)
    normalized_ip = normalize_rate_limit_client_ip(client_ip)

    if event_kind == "request":
        limit_specs = [
            (
                "pin_request_email",
                f"{normalized_email}|{normalized_purpose}",
                EMAIL_PIN_REQUEST_LIMIT_PER_EMAIL,
                EMAIL_PIN_REQUEST_WINDOW_MINUTES,
            ),
            (
                "pin_request_ip",
                normalized_ip,
                EMAIL_PIN_REQUEST_LIMIT_PER_IP,
                EMAIL_PIN_REQUEST_WINDOW_MINUTES,
            ),
        ]
        detail = "Too many PIN requests. Please wait before requesting another PIN."
    elif event_kind == "attempt":
        limit_specs = [
            (
                "pin_attempt_ip",
                normalized_ip,
                EMAIL_PIN_ATTEMPT_LIMIT_PER_IP,
                EMAIL_PIN_RATE_WINDOW_MINUTES,
            )
        ]
        detail = "Too many PIN attempts. Please wait before trying again."
    else:
        raise ValueError("Unsupported email PIN rate-limit event kind")

    enforce_rate_limit_specs(limit_specs, detail, EmailPinRateLimitError)


def enforce_login_rate_limits(email: str, client_ip: str) -> None:
    normalized_email = normalize_email(email)
    normalized_ip = normalize_rate_limit_client_ip(client_ip)
    enforce_rate_limit_specs(
        [
            (
                "login_request_ip",
                normalized_ip,
                LOGIN_REQUEST_LIMIT_PER_IP,
                LOGIN_RATE_WINDOW_MINUTES,
            ),
            (
                "login_request_email",
                normalized_email,
                LOGIN_REQUEST_LIMIT_PER_EMAIL,
                LOGIN_RATE_WINDOW_MINUTES,
            ),
        ],
        "Too many login attempts. Please wait before trying again.",
    )


def enforce_analysis_rate_limit(client_ip: str) -> None:
    normalized_ip = normalize_rate_limit_client_ip(client_ip)
    enforce_rate_limit_specs(
        [
            (
                "analysis_request_ip",
                normalized_ip,
                ANALYSIS_REQUEST_LIMIT_PER_IP,
                ANALYSIS_RATE_WINDOW_MINUTES,
            )
        ],
        "Too many resume analyses. Please wait before trying again.",
    )


def should_enforce_extra_rate_limits() -> bool:
    return not (
        app.config.get("TESTING")
        and not app.config.get("ENFORCE_RATE_LIMITS_IN_TESTS", False)
    )


def send_resend_api_email_pin(email: str, pin: str, purpose: str) -> str:
    api_key = os.getenv("RESEND_API_KEY", "").strip()
    from_email = os.getenv("RESEND_FROM", "").strip()
    payload = {
        "from": from_email,
        "to": [email],
        "subject": email_pin_subject(purpose),
        "text": email_pin_body(pin, purpose),
    }
    request = urllib.request.Request(
        "https://api.resend.com/emails",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            if response.status >= 400:
                raise OSError(f"Resend API error {response.status}: {response.reason}")
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="ignore")
        raise OSError(f"Resend API error {error.code}: {detail}") from error
    except urllib.error.URLError as error:
        raise OSError(f"Resend API request failed: {error.reason}") from error
    return "resend_api"


class _RejectEmailApiRedirects(urllib.request.HTTPRedirectHandler):
    """Never forward an email provider's credentials to a redirected endpoint."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def send_brevo_api_email_pin(email: str, pin: str, purpose: str) -> str:
    payload = {
        "sender": {
            "email": os.getenv("BREVO_FROM", "").strip(),
            "name": os.getenv("BREVO_FROM_NAME", "").strip() or APP_TITLE,
        },
        "to": [{"email": email}],
        "subject": email_pin_subject(purpose),
        "textContent": email_pin_body(pin, purpose),
    }
    request = urllib.request.Request(
        "https://api.brevo.com/v3/smtp/email",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "api-key": os.getenv("BREVO_API_KEY", "").strip(),
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        opener = urllib.request.build_opener(_RejectEmailApiRedirects())
        with opener.open(request, timeout=10) as response:
            status = response.status
    except urllib.error.HTTPError as error:
        # Provider bodies can echo request data; never expose or log their contents.
        status = error.code
        error.close()
    except (OSError, ValueError):
        raise OSError("Brevo email API connection failed. Please try again later.") from None
    if status != 201:
        raise OSError(f"Brevo email API rejected the email (HTTP {status}).") from None
    return "brevo_api"


def send_email_pin(email: str, pin: str, purpose: str) -> str:
    delivery_mode = get_email_pin_delivery_mode()
    if delivery_mode == "resend_api":
        return send_resend_api_email_pin(email, pin, purpose)

    if delivery_mode == "brevo_api":
        return send_brevo_api_email_pin(email, pin, purpose)

    if delivery_mode == "dev":
        return "dev"

    if delivery_mode == "unavailable":
        raise EmailPinConfigurationError(
            "Email PIN delivery is not configured. Configure a provider or enable explicit local development mode."
        )

    smtp_host = os.getenv("SMTP_HOST", "").strip()
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_username = os.getenv("SMTP_USERNAME", "").strip()
    smtp_password = os.getenv("SMTP_PASSWORD", "")
    smtp_from = os.getenv("SMTP_FROM", "").strip()
    smtp_use_tls = os.getenv("SMTP_USE_TLS", "1").strip().lower() not in {"0", "false", "no"}

    message = EmailMessage()
    message["Subject"] = email_pin_subject(purpose)
    message["From"] = smtp_from
    message["To"] = email
    message.set_content(email_pin_body(pin, purpose))

    with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as server:
        if smtp_use_tls:
            server.starttls(context=ssl.create_default_context())
        if smtp_username:
            server.login(smtp_username, smtp_password)
        server.send_message(message)

    return "smtp"


def should_return_dev_pin(delivery: str) -> bool:
    return (
        delivery == "dev"
        and not is_production_environment()
        and email_pin_dev_mode_requested()
    )


def get_email_delivery_error_message(error: Exception) -> str:
    if is_production_environment():
        return "Could not send verification email. Please try again later."

    raw_error = ""
    if isinstance(error, smtplib.SMTPResponseException):
        smtp_error = error.smtp_error
        raw_error = smtp_error.decode("utf-8", errors="ignore") if isinstance(smtp_error, bytes) else str(smtp_error)
    else:
        raw_error = str(error)

    normalized = raw_error.lower()
    if "brevo email api" in normalized:
        return "Brevo could not send the email. Check BREVO_API_KEY, your verified BREVO_FROM sender, and your Brevo account status."
    if "only send testing emails to your own email address" in normalized:
        return (
            "Resend test mode only sends to your own Resend account email. "
            "Use that email for testing, or verify a domain in Resend before sending to other users."
        )
    if "application-specific password required" in normalized or "invalidsecondfactor" in normalized:
        return "Gmail requires an App Password. Turn on Google 2-Step Verification, create an App Password, then use it as SMTP_PASSWORD."
    if "username and password not accepted" in normalized:
        return "Gmail rejected the SMTP login. Check that SMTP_USERNAME is your Gmail and SMTP_PASSWORD is a valid Google App Password."
    if "domain" in normalized and ("verify" in normalized or "verified" in normalized):
        return "Resend requires a verified sending domain. Verify your domain, then use a From email from that domain."
    if "resend api" in normalized and ("forbidden" in normalized or "api key" in normalized):
        return "Resend API rejected the request. Check that RESEND_API_KEY is active and RESEND_FROM is allowed."
    if "authentication" in normalized or "invalid api key" in normalized or "unauthorized" in normalized:
        return "Email authentication failed. Check that SMTP_PASSWORD or RESEND_API_KEY is active."
    return "Could not send verification email. Check SMTP settings."


def email_exists(email: str) -> bool:
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute("SELECT id FROM users WHERE email = %s LIMIT 1", (email,))
        return cursor.fetchone() is not None
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()


def create_email_pin(email: str, purpose: str) -> tuple[str, datetime, int]:
    pin = f"{secrets.randbelow(10 ** EMAIL_PIN_LENGTH):0{EMAIL_PIN_LENGTH}d}"
    expires_at = utc_now_naive() + timedelta(minutes=EMAIL_PIN_TTL_MINUTES)
    pin_hash = generate_password_hash(pin)

    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute(
            f"""
            UPDATE email_verification_pins
            SET consumed_at = {utc_now_sql()}
            WHERE email = %s
              AND purpose = %s
              AND consumed_at IS NULL
            """,
            (email, purpose),
        )
        cursor.execute(
            """
            INSERT INTO email_verification_pins (email, purpose, pin_hash, expires_at)
            VALUES (%s, %s, %s, %s)
            """ + (" RETURNING id" if is_postgres_backend() else ""),
            (email, purpose, pin_hash, expires_at),
        )
        pin_id = int(cursor.fetchone()[0] if is_postgres_backend() else cursor.lastrowid)
        connection.commit()
        return pin, expires_at, pin_id
    except Exception:
        if connection is not None:
            connection.rollback()
        raise
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()


def invalidate_email_pin(pin_id: int) -> bool:
    """Best-effort invalidation used when delivery fails after PIN creation."""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute(
            f"UPDATE email_verification_pins SET consumed_at = {utc_now_sql()} WHERE id = %s",
            (int(pin_id),),
        )
        connection.commit()
        return True
    except DatabaseErrors:
        if connection is not None:
            connection.rollback()
        return False
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()


def consume_email_pin(email: str, purpose: str, pin: str) -> tuple[bool, str]:
    normalized_pin = normalize_pin(pin)
    if len(normalized_pin) != EMAIL_PIN_LENGTH:
        return False, f"Enter the {EMAIL_PIN_LENGTH}-digit PIN."

    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT id, pin_hash, attempts, expires_at
            FROM email_verification_pins
            WHERE email = %s
              AND purpose = %s
              AND consumed_at IS NULL
            ORDER BY id DESC
            LIMIT 1
            FOR UPDATE
            """,
            (email, purpose),
        )
        pin_row = cursor.fetchone()

        if not pin_row:
            connection.rollback()
            return False, "Request a new PIN first."

        pin_id = int(pin_row["id"])
        attempts = int(pin_row.get("attempts") or 0)
        expires_at = pin_row["expires_at"]

        if isinstance(expires_at, datetime):
            if expires_at.tzinfo is not None:
                expires_at = expires_at.astimezone(timezone.utc).replace(tzinfo=None)
            if expires_at < utc_now_naive():
                cursor.execute(
                    f"UPDATE email_verification_pins SET consumed_at = {utc_now_sql()} WHERE id = %s",
                    (pin_id,),
                )
                connection.commit()
                return False, "This PIN has expired. Request a new one."

        if attempts >= EMAIL_PIN_MAX_ATTEMPTS:
            cursor.execute(
                f"UPDATE email_verification_pins SET consumed_at = {utc_now_sql()} WHERE id = %s",
                (pin_id,),
            )
            connection.commit()
            raise EmailPinRateLimitError(
                "Too many wrong PIN attempts. Request a new PIN later.",
                EMAIL_PIN_RATE_WINDOW_MINUTES * 60,
            )

        if not check_password_hash(pin_row["pin_hash"], normalized_pin):
            next_attempts = attempts + 1
            if next_attempts >= EMAIL_PIN_MAX_ATTEMPTS:
                cursor.execute(
                    f"""
                    UPDATE email_verification_pins
                    SET attempts = %s, consumed_at = {utc_now_sql()}
                    WHERE id = %s
                    """,
                    (next_attempts, pin_id),
                )
                connection.commit()
                raise EmailPinRateLimitError(
                    "Too many wrong PIN attempts. Request a new PIN later.",
                    EMAIL_PIN_RATE_WINDOW_MINUTES * 60,
                )

            cursor.execute(
                "UPDATE email_verification_pins SET attempts = %s WHERE id = %s",
                (next_attempts, pin_id),
            )
            connection.commit()
            return False, "Invalid PIN."

        cursor.execute(
            f"UPDATE email_verification_pins SET consumed_at = {utc_now_sql()} WHERE id = %s",
            (pin_id,),
        )
        connection.commit()
        return True, "PIN verified."
    except EmailPinRateLimitError:
        raise
    except Exception:
        if connection is not None:
            connection.rollback()
        raise
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()


def find_job_role_match(requested_role: str) -> tuple[dict[str, Any] | None, str]:
    requested = normalize_match_text(requested_role)
    if not requested:
        return None, "none"

    roles = fetch_job_roles()
    normalized_roles = {normalize_match_text(role["role_name"]): role for role in roles}

    if requested in normalized_roles:
        return normalized_roles[requested], "exact"

    alias_target = POPULAR_ROLE_ALIASES.get(requested)
    if alias_target:
        alias_key = normalize_match_text(alias_target)
        if alias_key in normalized_roles:
            return normalized_roles[alias_key], "alias"

    best_alias_key = ""
    best_alias_score = 0.0
    for alias_key in POPULAR_ROLE_ALIASES:
        score = role_similarity_score(requested, alias_key)
        if score > best_alias_score:
            best_alias_score = score
            best_alias_key = alias_key

    if best_alias_key and best_alias_score >= 0.78:
        alias_target = POPULAR_ROLE_ALIASES[best_alias_key]
        alias_key = normalize_match_text(alias_target)
        if alias_key in normalized_roles:
            return normalized_roles[alias_key], "alias_fuzzy"

    for role in roles:
        role_key = normalize_match_text(role["role_name"])
        if requested in role_key or role_key in requested:
            return role, "partial"

    requested_tokens = set(requested.split())
    best_role: dict[str, Any] | None = None
    best_score = 0
    for role in roles:
        role_tokens = set(normalize_match_text(role["role_name"]).split())
        score = len(requested_tokens & role_tokens)
        if score > best_score:
            best_score = score
            best_role = role

    if best_role and best_score >= 2:
        return best_role, "keyword"

    fuzzy_role: dict[str, Any] | None = None
    fuzzy_score = 0.0
    for role in roles:
        score = role_similarity_score(requested, role["role_name"])
        if score > fuzzy_score:
            fuzzy_score = score
            fuzzy_role = role

    if fuzzy_role and fuzzy_score >= 0.68:
        return fuzzy_role, "fuzzy"

    return None, "none"


def group_job_requirements(requirements: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for requirement in requirements:
        grouped.setdefault(requirement["requirement_type"], []).append(requirement)
    return grouped


def build_job_description_from_requirements(role: dict[str, Any], requirements: list[dict[str, Any]]) -> str:
    lines = [f"Target role: {role['role_name']}."]
    masco_description = normalize_text(role.get("masco_description", ""))
    if masco_description:
        lines.extend(["", masco_description])

    grouped = group_job_requirements(requirements)
    for requirement_type in REQUIREMENT_TYPE_ORDER:
        items = grouped.get(requirement_type, [])
        if not items:
            continue
        label = REQUIREMENT_TYPE_LABELS.get(requirement_type, requirement_type.title())
        lines.extend(["", f"{label}:"])
        for item in items:
            lines.append(f"- {item['requirement_text']}")

    for requirement_type in sorted(set(grouped) - set(REQUIREMENT_TYPE_ORDER)):
        label = REQUIREMENT_TYPE_LABELS.get(requirement_type, requirement_type.title())
        lines.extend(["", f"{label}:"])
        for item in grouped[requirement_type]:
            lines.append(f"- {item['requirement_text']}")

    return "\n".join(lines)


def resolve_database_job_description(requested_role: str) -> dict[str, Any] | None:
    matched_role, match_type = find_job_role_match(requested_role)
    if not matched_role:
        return None

    requirements = fetch_job_requirements(int(matched_role["id"]))
    if not requirements:
        return None

    return {
        "role": matched_role,
        "requirements": requirements,
        "match_type": match_type,
        "job_description": build_job_description_from_requirements(matched_role, requirements),
    }


EMPLOYMENT_DATE_RANGE_PATTERN = re.compile(
    r"(?P<start>(?:(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+|"
    r"(?:0?[1-9]|1[0-2])[/.-])?(?:19|20)\d{2})\s*"
    r"(?:-|\u2013|\u2014|to)\s*"
    r"(?P<end>present|current|now|(?:(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|"
    r"jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+|"
    r"(?:0?[1-9]|1[0-2])[/.-])?(?:19|20)\d{2})",
    re.I,
)
JOB_TITLE_MARKER_PATTERN = re.compile(
    r"\b(?:admin|administrator|analyst|architect|assistant|consultant|coordinator|developer|director|"
    r"designer|engineer|executive|intern|lead|manager|officer|programmer|scientist|specialist|"
    r"support|technician|tester)\b",
    re.I,
)
NON_JOB_TITLE_PATTERN = re.compile(
    r"\b(?:bachelor|certificate|certification|college|degree|diploma|education|master|phd|"
    r"polytechnic|school|university)\b",
    re.I,
)
RESUME_HEADLINE_PATTERN = re.compile(
    # CSV and PDF cleaning can collapse layout spacing to one space, so the
    # heading word is the boundary instead of relying on a visual tab width.
    r"^\s*(?P<title>[A-Z][A-Z0-9/&+().,' -]{2,80}?)\s+"
    r"(?:(?:professional|executive)\s+)?(?:summary|profile|objective)\b",
    re.I | re.M,
)
RESUME_HEADLINE_JOB_MARKER_PATTERN = re.compile(
    r"\b(?:accountant|administrator|analyst|architect|assistant|associate|coach|consultant|"
    r"coordinator|developer|designer|director|engineer|executive|intern|lead|manager|nurse|"
    r"officer|operator|pharmacist|programmer|representative|scientist|specialist|supervisor|"
    r"support|teacher|technician|tester)\b",
    re.I,
)
TECHNICAL_HEADLINE_PATTERN = re.compile(
    r"\b(?:artificial intelligence|backend|bim|cad|cam|cloud|computer|cyber(?:security| security)?|"
    r"data(?:base| science| scientist| analyst)?|devops|dotnet|front(?:end| end)|full[ -]?stack|"
    r"ict|information (?:security|systems?|technology)|machine learning|network|programmer|"
    r"software|systems? (?:administrator|analyst|engineer|programmer)|technical support|"
    r"technologist|tester|testing|ui|ux|web)\b",
    re.I,
)
GENERIC_JOB_TITLE_TOKENS = {
    "admin",
    "administrator",
    "analyst",
    "architect",
    "assistant",
    "consultant",
    "coordinator",
    "developer",
    "director",
    "designer",
    "engineer",
    "executive",
    "intern",
    "junior",
    "lead",
    "manager",
    "officer",
    "programmer",
    "senior",
    "specialist",
    "support",
    "technician",
    "tester",
}


def is_plausible_job_title(value: str) -> bool:
    title = normalize_text(value).strip(" |,-\u2013\u2014")
    if not title or len(title) > 100 or len(title.split()) > 12:
        return False
    if "@" in title or re.search(r"https?://|www\.|\d{5,}", title, re.I):
        return False
    if NON_JOB_TITLE_PATTERN.search(title):
        return False
    return bool(JOB_TITLE_MARKER_PATTERN.search(title))


def clean_employment_title(value: str) -> str:
    title = normalize_text(value).strip(" |,-\u2013\u2014")
    for candidate in re.split(r"\s+(?:at|@)\s+|[|,]", title, maxsplit=1, flags=re.I):
        cleaned = normalize_text(candidate).strip(" |,-\u2013\u2014")
        if is_plausible_job_title(cleaned):
            return cleaned
    return title


def parse_resume_date_point(value: str, *, end_value: bool = False) -> tuple[int, int] | None:
    normalized = normalize_text(value).lower()
    if normalized in {"present", "current", "now"}:
        today = datetime.now(timezone.utc)
        return today.year, today.month

    year_match = re.search(r"(?:19|20)\d{2}", normalized)
    if not year_match:
        return None
    year = int(year_match.group(0))
    numeric_month = re.match(r"(0?[1-9]|1[0-2])[/.-]", normalized)
    if numeric_month:
        month = int(numeric_month.group(1))
    else:
        month_names = {
            "jan": 1,
            "feb": 2,
            "mar": 3,
            "apr": 4,
            "may": 5,
            "jun": 6,
            "jul": 7,
            "aug": 8,
            "sep": 9,
            "oct": 10,
            "nov": 11,
            "dec": 12,
        }
        month = next((number for name, number in month_names.items() if normalized.startswith(name)), 12 if end_value else 1)
    return year, month


def extract_employment_entries(resume_text: str) -> list[dict[str, Any]]:
    """Return only date ranges that can be tied to a plausible job title.

    This deliberately ignores unattached education dates. It is conservative:
    an uncertain layout yields no structured entry instead of an invented title.
    """

    lines = [normalize_text(line) for line in (resume_text or "").splitlines()]
    has_work_history_heading = any(
        normalize_match_text(line)
        in {
            "career history",
            "employment history",
            "experience",
            "previous experience",
            "professional experience",
            "work experience",
            "work history",
        }
        for line in lines
    )
    if not has_work_history_heading:
        return []

    entries: list[dict[str, Any]] = []
    in_hard_non_work_section = False
    for index, line in enumerate(lines):
        if not line:
            continue
        normalized_line = normalize_match_text(line)
        if normalized_line in {
            "career history",
            "employment history",
            "experience",
            "previous experience",
            "professional experience",
            "work experience",
            "work history",
        }:
            in_hard_non_work_section = False
            continue
        if normalized_line in {
            "academic projects",
            "certifications",
            "education",
            "personal projects",
            "projects",
            "qualifications",
        }:
            in_hard_non_work_section = True
            continue
        if in_hard_non_work_section:
            continue

        for date_match in EMPLOYMENT_DATE_RANGE_PATTERN.finditer(line):
            title = line[: date_match.start()].strip(" |,-\u2013\u2014")
            if not is_plausible_job_title(title):
                title = ""
                for prior_index in range(index - 1, max(-1, index - 4), -1):
                    prior_line = lines[prior_index].strip(" |,-\u2013\u2014")
                    if not prior_line:
                        continue
                    if is_plausible_job_title(prior_line):
                        title = prior_line
                    break
            if not title:
                # Some resumes put the employer and date range on one line,
                # followed by the job title on the next non-empty line.
                # Only inspect that adjacent content line so a later bullet or
                # section cannot be borrowed as the title for this date range.
                following_index = index + 1
                while following_index < len(lines) and not lines[following_index]:
                    following_index += 1
                if following_index < len(lines):
                    following_line = lines[following_index].strip(" |,-\u2013\u2014")
                    if (
                        not EMPLOYMENT_DATE_RANGE_PATTERN.search(following_line)
                        and is_plausible_job_title(following_line)
                    ):
                        title = following_line
            if not title:
                continue
            title = clean_employment_title(title)

            start = parse_resume_date_point(date_match.group("start"))
            end = parse_resume_date_point(date_match.group("end"), end_value=True)
            if not start or not end or end < start:
                continue
            entries.append(
                {
                    "title": title,
                    "start": start,
                    "end": end,
                    "date_text": normalize_text(date_match.group(0)),
                }
            )
    return entries


def get_most_recent_job_title(entries: list[dict[str, Any]]) -> str:
    if not entries:
        return ""
    latest = max(entries, key=lambda item: (item["start"], item["end"]))
    return str(latest["title"])


def extract_most_recent_job_title(resume_text: str) -> str:
    return get_most_recent_job_title(extract_employment_entries(resume_text))


def extract_explicit_resume_headline(resume_text: str) -> str:
    """Return a prominent job headline only when the layout marks it as one.

    This deliberately requires a job-title marker followed by a Summary/Profile/
    Objective heading. Candidate names and ordinary first lines are not inferred
    as job titles.
    """

    match = RESUME_HEADLINE_PATTERN.search((resume_text or "")[:1000])
    if not match:
        return ""
    title = normalize_text(match.group("title")).strip(" |,-\u2013\u2014")
    if not title or len(title.split()) > 10:
        return ""
    if not RESUME_HEADLINE_JOB_MARKER_PATTERN.search(title):
        return ""
    return title.title() if title.isupper() else title


def is_explicit_non_it_resume_headline(value: str) -> bool:
    headline = normalize_text(value)
    return bool(
        headline
        and RESUME_HEADLINE_JOB_MARKER_PATTERN.search(headline)
        and not TECHNICAL_HEADLINE_PATTERN.search(headline)
    )


def get_total_employment_months(entries: list[dict[str, Any]]) -> int | None:
    if not entries:
        return None

    # Union the months so simultaneous jobs are not double-counted.
    covered_months: set[int] = set()
    for entry in entries:
        start_year, start_month = entry["start"]
        end_year, end_month = entry["end"]
        start_index = (start_year * 12) + start_month - 1
        end_index = (end_year * 12) + end_month - 1
        covered_months.update(range(start_index, end_index + 1))
    return len(covered_months)


def compute_role_title_match_score(
    resume_text: str,
    role_name: str,
    most_recent_title: str | None = None,
) -> float:
    normalized_role = normalize_match_text(role_name)
    if not normalize_match_text(resume_text) or not normalized_role:
        return 0.0

    aliases = ROLE_TITLE_MATCH_ALIASES.get(normalized_role, {normalized_role})
    if most_recent_title is None:
        most_recent_title = extract_most_recent_job_title(resume_text)
    if most_recent_title:
        normalized_title_source = f" {normalize_match_text(most_recent_title)} "
        for alias in aliases:
            normalized_alias = normalize_match_text(alias)
            if normalized_alias and f" {normalized_alias} " in normalized_title_source:
                return 100.0

        # Aliases are a set; sorting makes ties resolve the same way in every
        # server process instead of following Python's per-process hash order.
        best_alias = max(sorted(aliases), key=lambda alias: role_similarity_score(alias, most_recent_title))
        similarity = role_similarity_score(best_alias, most_recent_title)
        alias_content = set(normalize_match_text(best_alias).split()) - GENERIC_JOB_TITLE_TOKENS
        title_content = set(normalize_match_text(most_recent_title).split()) - GENERIC_JOB_TITLE_TOKENS
        has_informative_overlap = bool(alias_content & title_content)
        if not has_informative_overlap:
            return min(10.0, round(similarity * 10.0, 2))
        if similarity >= 0.85:
            return 90.0
        if similarity >= 0.65:
            return 75.0
        if similarity >= 0.5:
            return 40.0
        return min(10.0, round(similarity * 20.0, 2))

    # A target role in a summary or skills list is not proof of a held title.
    # Without a confidently parsed dated job, title fit remains unavailable.
    return 0.0


def has_exact_role_title_match(
    resume_text: str,
    role_name: str,
    most_recent_title: str | None = None,
) -> bool:
    normalized_role = normalize_match_text(role_name)
    if not normalized_role:
        return False

    aliases = ROLE_TITLE_MATCH_ALIASES.get(normalized_role, {normalized_role})
    if most_recent_title is None:
        most_recent_title = extract_most_recent_job_title(resume_text)
    if not most_recent_title:
        return False
    normalized_resume = f" {normalize_match_text(most_recent_title)} "
    return any(
        bool(normalized_alias and f" {normalized_alias} " in normalized_resume)
        for normalized_alias in (normalize_match_text(alias) for alias in aliases)
    )


def compute_career_role_title_match(
    resume_text: str,
    role_name: str,
    employment_entries: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Score current and previous dated job titles without treating them equally.

    The most recent title receives full credit. Each older title receives 85% of
    the credit available to the title immediately before it. This lets genuine
    prior experience support graduates and career changers, while keeping the
    current job as the strongest title signal. Only the current title is allowed
    to trigger the exact-title confidence floors elsewhere in the scorer.
    """

    entries = employment_entries
    if entries is None:
        entries = extract_employment_entries(resume_text)
    headline = extract_explicit_resume_headline(resume_text)
    explicit_non_it_headline = is_explicit_non_it_resume_headline(headline)
    ordered_entries = sorted(
        entries,
        key=lambda item: (item["start"], item["end"]),
        reverse=True,
    )
    if not ordered_entries:
        if explicit_non_it_headline:
            return {
                "score": 0.0,
                "available": True,
                "most_recent_title": headline,
                "matched_title": headline,
                "matched_title_is_current": True,
                "exact_current_title_match": False,
                "signal_source": "non_it_headline",
                "explicit_non_it_headline": True,
                "non_it_headline": headline,
            }
        return {
            "score": 0.0,
            "available": False,
            "most_recent_title": "",
            "matched_title": "",
            "matched_title_is_current": False,
            "exact_current_title_match": False,
            "signal_source": "unavailable",
            "explicit_non_it_headline": False,
            "non_it_headline": "",
        }

    most_recent_title = str(ordered_entries[0]["title"])
    best_score = 0.0
    best_title = most_recent_title
    best_index = 0
    for index, entry in enumerate(ordered_entries):
        title = str(entry.get("title") or "")
        raw_score = compute_role_title_match_score(resume_text, role_name, title)
        recency_weight = ROLE_TITLE_HISTORY_DECAY**index
        adjusted_score = raw_score * recency_weight
        if adjusted_score > best_score:
            best_score = adjusted_score
            best_title = title
            best_index = index

    return {
        "score": round(min(100.0, best_score), 2),
        "available": True,
        "most_recent_title": most_recent_title,
        "matched_title": best_title,
        "matched_title_is_current": best_index == 0,
        "exact_current_title_match": has_exact_role_title_match(
            resume_text,
            role_name,
            most_recent_title,
        ),
        "signal_source": "employment_title",
        "explicit_non_it_headline": explicit_non_it_headline,
        "non_it_headline": headline if explicit_non_it_headline else "",
    }


def compute_database_role_confidence_score(
    combined_score: float,
    role_evidence_score: float,
    title_match_score: float,
    exact_title_match: bool,
    title_match_available: bool = True,
) -> float:
    weighted_requirement_score = combined_score * ROLE_REQUIREMENT_FIT_WEIGHT
    weighted_role_evidence_score = role_evidence_score * ROLE_EVIDENCE_WEIGHT
    title_match_available = bool(title_match_available or exact_title_match)
    if title_match_available:
        adjusted_score = (
            weighted_requirement_score
            + weighted_role_evidence_score
            + (title_match_score * ROLE_TITLE_WEIGHT)
        )
    else:
        # A parser miss is not evidence that the candidate held an unrelated
        # job. Redistribute the unavailable title component across the two
        # evidence components while preserving their 55:10 ratio.
        adjusted_score = (
            weighted_requirement_score + weighted_role_evidence_score
        ) / (ROLE_REQUIREMENT_FIT_WEIGHT + ROLE_EVIDENCE_WEIGHT)

    if exact_title_match:
        adjusted_score = max(adjusted_score, 70.0)
        if combined_score >= 30 and role_evidence_score >= 40:
            adjusted_score = max(adjusted_score, 78.0)
        if combined_score >= 38 and role_evidence_score >= 65:
            adjusted_score = max(adjusted_score, 82.0)

    return round(min(100.0, adjusted_score), 2)


def rank_database_role_matches(resume_text: str, limit: int | None = None) -> list[dict[str, Any]]:
    # The same requirement phrases appear across many roles. Keep exact scoring
    # results only for this ranking run, without retaining a student's text.
    with scoring_cache_scope():
        return _rank_database_role_matches(resume_text, limit)


def _rank_database_role_matches(resume_text: str, limit: int | None = None) -> list[dict[str, Any]]:
    roles, requirements_by_role = fetch_job_catalog()
    ranked_matches: list[dict[str, Any]] = []
    employment_entries = extract_employment_entries(resume_text)
    most_recent_title = get_most_recent_job_title(employment_entries)

    for role in roles:
        requirements = requirements_by_role.get(int(role["id"]), [])
        if not requirements:
            continue

        job_description = build_job_description_from_requirements(role, requirements)
        fit_result = evaluate_job_fit(resume_text, job_description, requirements)
        combined_score = float(fit_result["combined_score"])
        role_name = str(role.get("role_name") or "")
        role_evidence_score = compute_role_evidence_score(
            resume_text,
            role_name,
            fit_result.get("important_keywords", []),
        )
        title_result = compute_career_role_title_match(
            resume_text,
            role_name,
            employment_entries,
        )
        title_match_score = float(title_result["score"])
        exact_title_match = bool(title_result["exact_current_title_match"])
        title_match_available = bool(title_result["available"])
        adjusted_score = compute_database_role_confidence_score(
            combined_score,
            role_evidence_score,
            title_match_score,
            exact_title_match,
            title_match_available,
        )

        ranked_matches.append(
            {
                "role": role,
                "requirements": requirements,
                "match_type": "resume_auto",
                "job_description": job_description,
                "confidence_score": round(adjusted_score, 2),
                "base_confidence_score": round(combined_score, 2),
                "role_evidence_score": role_evidence_score,
                "title_match_score": title_match_score,
                "title_match_available": title_match_available,
                "exact_title_match": exact_title_match,
                "title_match_title": title_result["matched_title"],
                "title_match_source": (
                    "non_it_headline"
                    if title_result.get("signal_source") == "non_it_headline"
                    else (
                        "unavailable"
                        if not title_match_available
                        else ("current" if title_result["matched_title_is_current"] else "previous")
                    )
                ),
                "explicit_non_it_headline": bool(title_result.get("explicit_non_it_headline")),
                "non_it_headline": title_result.get("non_it_headline") or "",
                "most_recent_title": title_result.get("most_recent_title") or most_recent_title,
                "fit_result": fit_result,
            }
        )

    ranked_matches.sort(
        key=lambda item: (
            float(item.get("confidence_score") or 0),
            1 if item.get("exact_title_match") else 0,
            len(normalize_match_text(str((item.get("role") or {}).get("role_name") or "")).split()),
            float(item.get("role_evidence_score") or 0),
            str((item.get("role") or {}).get("role_name") or ""),
        ),
        reverse=True,
    )

    if limit is None:
        return ranked_matches
    return ranked_matches[: max(0, limit)]


def choose_pdf_text_candidate(plain_raw_text: str, layout_raw_text: str) -> tuple[str, str, str]:
    plain_text = clean_resume_source_text(plain_raw_text)
    layout_text = clean_resume_source_text(layout_raw_text)
    if not layout_text:
        return plain_text, plain_raw_text, "plain"
    if not plain_text:
        return layout_text, layout_raw_text, "layout"

    def candidate_score(text: str) -> float:
        section_hits = sum(1 for marker in CORE_RESUME_SECTION_MARKERS if marker in text.lower())
        skill_hits = len(extract_key_phrases(text, DEFAULT_SKILL_DICTIONARY))
        spacing_penalty = count_text_extraction_spacing_glitches(text) * 18
        return count_resume_words(text) + (section_hits * 25) + (skill_hits * 8) - spacing_penalty

    plain_score = candidate_score(plain_text)
    layout_score = candidate_score(layout_text)
    plain_words = count_resume_words(plain_text)
    layout_words = count_resume_words(layout_text)
    plain_glitches = count_text_extraction_spacing_glitches(plain_text)
    layout_glitches = count_text_extraction_spacing_glitches(layout_text)

    if (
        layout_score >= plain_score + 40
        or (
            plain_words < 180
            and layout_words >= plain_words * 1.45
            and layout_glitches <= plain_glitches
        )
    ):
        return layout_text, layout_raw_text, "layout"

    return plain_text, plain_raw_text, "plain"


def extract_pdf_text(file_storage) -> tuple[str, dict[str, Any]]:
    try:
        reader = PdfReader(BytesIO(file_storage.read()))
    except Exception as error:
        raise ValueError("Could not read the uploaded PDF. Please upload a valid, unlocked PDF file.") from error

    plain_pages: list[str] = []
    layout_pages: list[str] = []
    embedded_image_count = 0
    try:
        for page in reader.pages:
            plain_pages.append(page.extract_text() or "")
            try:
                layout_pages.append(page.extract_text(extraction_mode="layout") or "")
            except TypeError:
                layout_pages.append("")
            try:
                embedded_image_count += len(page.images)
            except Exception:
                pass
    except Exception as error:
        raise ValueError("Could not extract text from this PDF. Please upload a text-based PDF exported from Word, Google Docs, or your resume editor.") from error

    raw_text = "\n".join(plain_pages)
    layout_raw_text = "\n".join(layout_pages)
    normalized_text, chosen_raw_text, extraction_mode = choose_pdf_text_candidate(raw_text, layout_raw_text)
    return normalized_text, {
        "file_type": "pdf",
        "page_count": len(reader.pages),
        "embedded_image_count": embedded_image_count,
        "ocr_used": False,
        "raw_text_length": len(chosen_raw_text),
        "pdf_extraction_mode": extraction_mode,
        "character_spacing_repaired": bool(CHARACTER_SPACED_WORD_PATTERN.search(chosen_raw_text)),
    }


def configure_tesseract() -> None:
    if pytesseract is None:
        raise ValueError("Image resume OCR requires the pytesseract Python package. Please install backend requirements again.")

    configured_cmd = os.getenv("TESSERACT_CMD", "").strip()
    if configured_cmd:
        pytesseract.pytesseract.tesseract_cmd = configured_cmd
        if os.path.exists(configured_cmd):
            return

    if shutil.which("tesseract"):
        return

    raise ValueError(
        "Image resume OCR is not configured. Install Tesseract OCR, then set TESSERACT_CMD in env "
        "to the tesseract.exe path. You can still upload a text-based PDF."
    )


def extract_image_text(file_storage) -> tuple[str, dict[str, Any]]:
    if Image is None:
        raise ValueError("Image resume OCR requires the Pillow Python package. Please install backend requirements again.")

    configure_tesseract()
    try:
        image = Image.open(BytesIO(file_storage.read()))
        image = image.convert("RGB")
    except (UnidentifiedImageError, OSError) as error:
        raise ValueError("Could not read the uploaded image. Please upload a valid PNG or JPG file.") from error

    try:
        raw_text = pytesseract.image_to_string(image)
        return normalize_text(raw_text), {
            "file_type": get_file_extension(file_storage.filename).lstrip(".") or "image",
            "page_count": 1,
            "embedded_image_count": 1,
            "ocr_used": True,
            "raw_text_length": len(raw_text),
            "image_width": image.width,
            "image_height": image.height,
        }
    except Exception as error:
        raise ValueError("Could not extract text from the image resume. Please upload a clearer image or a text-based PDF.") from error


def extract_resume_text(file_storage, filename: str) -> tuple[str, dict[str, Any]]:
    extension = get_file_extension(filename)
    if extension in PDF_EXTENSIONS:
        return extract_pdf_text(file_storage)
    if extension in IMAGE_EXTENSIONS:
        return extract_image_text(file_storage)
    raise ValueError("Only PDF, PNG, JPG, or JPEG resumes are supported.")


def tokenize_keywords(text: str) -> list[str]:
    tokens = re.findall(r"[a-zA-Z][a-zA-Z+.#/-]{1,}", text.lower())
    cleaned_tokens = [token.strip(".,;:!?()[]{}\"'") for token in tokens]
    filtered = [token for token in cleaned_tokens if len(token) > 2 and token not in KEYWORD_STOPWORDS]
    return filtered


def normalize_keyword_token(token: str) -> str:
    cleaned = token.lower().strip(".,;:!?()[]{}\"'")
    if cleaned in CANONICAL_KEYWORD_ALIASES:
        return CANONICAL_KEYWORD_ALIASES[cleaned]
    if cleaned == "net":
        return "dotnet"
    if cleaned.endswith("ies") and len(cleaned) > 5:
        return cleaned[:-3] + "y"
    if cleaned.endswith("s") and len(cleaned) > 4 and not cleaned.endswith(("ss", "sis")):
        return cleaned[:-1]
    return cleaned


def tokenize_canonical_keywords(text: str) -> list[str]:
    cache = get_scoring_cache()
    if cache is None:
        return [normalize_keyword_token(token) for token in tokenize_keywords(text)]
    tokens = cache.canonical_tokens.get(text)
    if tokens is None:
        tokens = tuple(normalize_keyword_token(token) for token in tokenize_keywords(text))
        cache.canonical_tokens[text] = tokens
    return list(tokens)


def scoped_normalize_match_text(value: str) -> str:
    """normalize_match_text, remembered for the current scoring run only."""
    cache = get_scoring_cache()
    if cache is None:
        return normalize_match_text(value)
    normalized = cache.match_text.get(value)
    if normalized is None:
        normalized = normalize_match_text(value)
        cache.match_text[value] = normalized
    return normalized


def get_semantic_equivalents(token: str) -> set[str]:
    canonical = normalize_keyword_token(token)
    equivalents = {canonical}

    direct_matches = SEMANTIC_KEYWORD_EQUIVALENTS.get(canonical, set())
    equivalents.update(normalize_keyword_token(match) for match in direct_matches)
    return equivalents


def token_has_resume_evidence(token: str, resume_tokens: set[str]) -> bool:
    canonical = normalize_keyword_token(token)
    strict_tokens = STRICT_TECHNICAL_KEYWORD_EVIDENCE.get(canonical)
    if strict_tokens is not None:
        return bool(strict_tokens & resume_tokens)
    return bool(get_semantic_equivalents(canonical) & resume_tokens)


NEGATED_EVIDENCE_MARKERS = {
    "lack",
    "lacked",
    "lacking",
    "lacks",
    "never",
    "no",
    "not",
    "unfamiliar",
    "without",
}


def get_resume_clause_index(resume_text: str) -> tuple[list[list[str]], dict[str, list[tuple[int, int]]]]:
    """Split and normalize the resume once, with the positions of every token."""
    cache = get_scoring_cache()
    if cache is not None and resume_text in cache.clause_index:
        return cache.clause_index[resume_text]

    # Treat contrast words as clause boundaries so "no Python, but strong Java"
    # does not incorrectly negate Java as well.
    clauses = re.split(r"[\n.!?;]+|\b(?:but|however|although)\b", resume_text or "", flags=re.I)
    clause_tokens_list: list[list[str]] = []
    token_positions: dict[str, list[tuple[int, int]]] = {}
    for clause in clauses:
        # Keep short negation words such as "no" and ordinary stop words for
        # the context window, while still normalizing skill word forms.
        clause_tokens = [
            normalize_keyword_token(token)
            for token in re.findall(r"[a-zA-Z][a-zA-Z+.#/-]*", clause.lower())
        ]
        clause_number = len(clause_tokens_list)
        clause_tokens_list.append(clause_tokens)
        for position, token in enumerate(clause_tokens):
            token_positions.setdefault(token, []).append((clause_number, position))

    clause_index = (clause_tokens_list, token_positions)
    if cache is not None:
        cache.clause_index[resume_text] = clause_index
    return clause_index


def phrase_has_positive_resume_evidence(phrase: str, resume_text: str) -> bool:
    cache = get_scoring_cache()
    cache_key = (phrase, resume_text)
    if cache is not None and cache_key in cache.phrase:
        return cache.phrase[cache_key]

    phrase_tokens = tokenize_canonical_keywords(phrase)
    if not phrase_tokens:
        return False

    clause_tokens_list, token_positions = get_resume_clause_index(resume_text)
    width = len(phrase_tokens)
    found = False
    for clause_number, index in token_positions.get(phrase_tokens[0], ()):
        clause_tokens = clause_tokens_list[clause_number]
        if clause_tokens[index : index + width] != phrase_tokens:
            continue
        preceding = clause_tokens[max(0, index - 6) : index]
        if not (set(preceding) & NEGATED_EVIDENCE_MARKERS):
            found = True
            break
    if cache is not None:
        cache.phrase[cache_key] = found
    return found


def token_has_positive_resume_evidence(token: str, resume_text: str, resume_tokens: set[str]) -> bool:
    canonical = normalize_keyword_token(token)
    if not token_has_resume_evidence(canonical, resume_tokens):
        return False

    strict_tokens = STRICT_TECHNICAL_KEYWORD_EVIDENCE.get(canonical)
    candidates = strict_tokens if strict_tokens is not None else get_semantic_equivalents(canonical)
    return any(phrase_has_positive_resume_evidence(candidate, resume_text) for candidate in candidates)


def keyword_matches_resume(keyword: str, resume_text: str, resume_tokens: set[str]) -> bool:
    keyword_tokens = tokenize_canonical_keywords(keyword)
    if not keyword_tokens:
        return False

    canonical_keyword = " ".join(keyword_tokens)
    if phrase_has_positive_resume_evidence(canonical_keyword, resume_text):
        return True

    return all(
        token_has_positive_resume_evidence(token, resume_text, resume_tokens)
        for token in keyword_tokens
    )


def key_phrase_exists(text: str, phrase: str) -> bool:
    cleaned_phrase = normalize_text(phrase).lower()
    if not cleaned_phrase:
        return False
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(cleaned_phrase)}(?![a-z0-9])", text.lower()))


def extract_key_phrases(text: str, vocabulary: list[str]) -> list[str]:
    matches: list[str] = []
    for term in vocabulary:
        if key_phrase_exists(text, term):
            matches.append(term)
    return sorted(set(matches))


def extract_sentences(text: str) -> list[str]:
    raw_sentences = re.split(r"(?<=[.!?])\s+|\n+", text)
    sentences = [normalize_text(sentence) for sentence in raw_sentences]
    return [sentence for sentence in sentences if len(sentence) > 20]


def extract_resume_profile(text: str) -> ParsedResume:
    skills = extract_key_phrases(text, DEFAULT_SKILL_DICTIONARY)
    education = extract_key_phrases(text, COMMON_EDUCATION_MARKERS)
    experience = extract_key_phrases(text, COMMON_EXPERIENCE_MARKERS)
    sentences = extract_sentences(text)
    excerpt = " ".join(sentences[:3]) if sentences else text[:400]
    return ParsedResume(
        text=text,
        skills=skills,
        education=education,
        experience=experience,
        excerpt=excerpt,
    )


def build_quality_criterion(
    category: str,
    status: str,
    title: str,
    detail: str,
    suggestion: str,
    penalty: int = 0,
) -> dict[str, Any]:
    return {
        "category": category,
        "status": status,
        "title": title,
        "detail": detail,
        "suggestion": suggestion,
        "penalty": penalty,
    }


def count_language_markers(text: str, markers: set[str]) -> int:
    tokens = re.findall(r"[a-zA-Z]+", text.lower())
    return sum(1 for token in tokens if token in markers)


def has_measurable_impact(text: str) -> bool:
    """Detect a real result signal without treating years or the word 'users' as metrics."""

    value = normalize_text(text)
    if not value:
        return False
    if re.search(r"(?:\d+(?:\.\d+)?\s*%|[$€£]\s*\d|\b\d+\+)", value):
        return True
    if re.search(
        r"\b(?!19\d{2}\b|20\d{2}\b)\d+(?:,\d{3})*(?:\.\d+)?\s*"
        r"(?:users?|customers?|clients?|records?|transactions?|requests?|projects?|"
        r"hours?|days?|weeks?|months?|minutes?|seconds?)\b",
        value,
        re.I,
    ):
        return True
    return bool(
        re.search(
            r"\b(?:reduced|increased|improved|boosted|decreased|saved|grew|accelerated|optimized)\b",
            value,
            re.I,
        )
    )


def count_text_extraction_spacing_glitches(text: str) -> int:
    candidates = re.findall(
        r"\b(?:[a-z]{3,}[A-Z][A-Za-z]{2,}|\d{2,}[A-Za-z]{3,}|[A-Za-z]{3,}\d{2,})\b",
        text,
    )
    expected_technical_tokens = {
        "css3",
        "html5",
        "ipv4",
        "ipv6",
        "nodejs",
        "oauth2",
        "reactjs",
        "restjson",
        "vuejs",
        "webapi",
    }
    return sum(1 for token in candidates if normalize_match_text(token) not in expected_technical_tokens)


def has_interleaved_section_heading_risk(text: str) -> bool:
    """Detect an obvious multi-column reading-order collision conservatively."""

    raw_lines = [normalize_text(line) for line in (text or "").splitlines() if normalize_text(line)]
    normalized_lines = [normalize_match_text(line) for line in raw_lines]
    work_headings = {
        "employment history",
        "professional experience",
        "work experience",
        "work history",
    }
    sidebar_headings = {"contact", "core skills", "skills", "technical skills", "tools and software"}
    for index, line in enumerate(normalized_lines):
        if line not in work_headings:
            continue
        next_dated_entry = next(
            (
                candidate_index
                for candidate_index in range(index + 1, min(len(raw_lines), index + 18))
                if EMPLOYMENT_DATE_RANGE_PATTERN.search(raw_lines[candidate_index])
                and is_plausible_job_title(EMPLOYMENT_DATE_RANGE_PATTERN.sub("", raw_lines[candidate_index]).strip(" |,-\u2013\u2014"))
            ),
            None,
        )
        if next_dated_entry is None:
            continue
        if any(normalized_lines[candidate_index] in sidebar_headings for candidate_index in range(index + 1, next_dated_entry)):
            return True
    return False


def analyze_resume_quality(
    resume_text: str,
    uploaded_name: str,
    document_info: dict[str, Any] | None,
    resume_profile: ParsedResume,
) -> dict[str, Any]:
    info = document_info or {}
    file_type = str(info.get("file_type") or get_file_extension(uploaded_name).lstrip(".") or "unknown").lower()
    word_count = count_resume_words(resume_text)
    lower_text = resume_text.lower()
    criteria: list[dict[str, Any]] = []
    section_hits = sorted({marker for marker in CORE_RESUME_SECTION_MARKERS if marker in lower_text})
    has_email = bool(re.search(r"[^@\s]+@[^@\s]+\.[^@\s]+", resume_text))
    has_phone = bool(re.search(r"(\+?\d[\d\s().-]{7,}\d)", resume_text))
    has_profile_link = any(marker in lower_text for marker in CONTACT_MARKERS)
    raw_text_length = int(info.get("raw_text_length") or len(resume_text or ""))
    has_resume_signals = len(section_hits) >= 2 or bool(resume_profile.skills and (resume_profile.education or resume_profile.experience))
    has_readable_pdf_text = word_count >= 180 or (raw_text_length >= 1200 and has_resume_signals)

    def add(
        category: str,
        status: str,
        title: str,
        detail: str,
        suggestion: str,
        penalty: int = 0,
    ) -> None:
        criteria.append(build_quality_criterion(category, status, title, detail, suggestion, penalty))

    if info.get("ocr_used"):
        add(
            "Format",
            "warning",
            "Image or scanned resume",
            "The uploaded file was read with OCR, which is less reliable than selectable PDF text.",
            "Export the resume as a text-based PDF from Word, Google Docs, Canva, or your resume editor.",
            18,
        )
    elif file_type == "pdf" and has_readable_pdf_text:
        add(
            "Format",
            "pass",
            "Text-based PDF detected",
            "The resume contains selectable text, so the analyzer can read it directly.",
            "Keep using a text-based PDF for ATS submissions.",
            0,
        )
    elif file_type == "pdf":
        add(
            "Format",
            "warning",
            "Low extracted text",
            "The PDF produced a small amount of readable text, which may mean the layout is image-heavy or scanned.",
            "Use a cleaner one-column text PDF and avoid exporting the whole resume as an image.",
            16,
        )

    embedded_image_count = int(info.get("embedded_image_count") or 0)
    if info.get("ocr_used"):
        add(
            "Photo",
            "warning",
            "Photo/image resume risk",
            "Image resumes can include visual content that ATS systems may skip or misread.",
            "For ATS applications, remove passport-style photos and use normal selectable text.",
            10,
        )
    elif embedded_image_count > 0:
        add(
            "Photo",
            "warning",
            "Embedded image detected",
            f"The PDF contains {embedded_image_count} embedded image(s). If one is a passport photo, logo, or decorative graphic, ATS systems may ignore it.",
            "Use photos only when the employer asks for them; otherwise remove passport photos and decorative images.",
            6,
        )
    else:
        add(
            "Photo",
            "pass",
            "No embedded image risk found",
            "No obvious image or passport-photo signal was detected in the extracted document.",
            "Keep the resume text-focused for ATS uploads.",
            0,
        )

    if file_type == "pdf" and has_interleaved_section_heading_risk(resume_text):
        add(
            "Parsing",
            "warning",
            "Possible multi-column reading-order issue",
            "A sidebar heading appears inside the extracted work-history flow before the first dated job entry.",
            "Use a single-column ATS version, or verify that copied PDF text keeps headings and work entries in the intended order.",
            8,
        )

    english_hits = count_language_markers(resume_text, ENGLISH_LANGUAGE_MARKERS)
    malay_hits = count_language_markers(resume_text, MALAY_LANGUAGE_MARKERS)
    non_latin_count = len(re.findall(r"[\u4e00-\u9fff\u3040-\u30ff\u0600-\u06ff\u0900-\u097f\u0b80-\u0bff]", resume_text))
    if english_hits >= 4 and (malay_hits >= 3 or non_latin_count > 20):
        add(
            "Language",
            "warning",
            "Mixed language detected",
            "The resume appears to mix English with Malay or another script. Some ATS keyword matching is language-specific.",
            "Use one main language for the ATS version, normally English for CS roles, and translate key skills consistently.",
            10,
        )
    else:
        add(
            "Language",
            "pass",
            "Single-language text",
            "No strong bilingual-language risk was detected from the extracted text.",
            "Keep job titles, skills, and project descriptions in the same language as the job posting.",
            0,
        )

    spacing_glitches = count_text_extraction_spacing_glitches(resume_text)
    ligature_count = len(re.findall(r"[\ufb00-\ufb06]", resume_text))
    if spacing_glitches >= 14 or ligature_count > 0:
        add(
            "Parsing",
            "warning",
            "Text extraction spacing issue",
            "Some words appear joined together or contain special ligature characters after extraction.",
            "Use a simple one-column layout and standard fonts so ATS parsers do not join words together.",
            10,
        )
    else:
        add(
            "Parsing",
            "pass",
            "Clean text extraction",
            "The extracted text does not show major spacing or encoding problems.",
            "Keep fonts standard and avoid complex text boxes.",
            0,
        )

    if len(section_hits) < 3:
        add(
            "Structure",
            "warning",
            "Missing standard resume sections",
            "The analyzer found fewer than three standard sections such as Education, Skills, Experience, or Projects.",
            "Use clear section headings: Education, Technical Skills, Projects, and Work Experience.",
            12,
        )
    else:
        add(
            "Structure",
            "pass",
            "Standard sections found",
            "The resume includes recognizable ATS-friendly section headings.",
            "Keep headings simple and avoid replacing them with icons only.",
            0,
        )

    has_work_history_heading = bool(
        re.search(
            r"\b(?:work experience|professional experience|employment history|work history)\b",
            lower_text,
        )
    )
    employment_entries = extract_employment_entries(resume_text)
    if has_work_history_heading and not employment_entries:
        add(
            "Timeline",
            "warning",
            "Employment dates not recognizable",
            "A work-history section was found, but no job title could be paired with a standard start/end date range.",
            "Use a consistent format such as MM/YYYY - MM/YYYY or Month YYYY - Present beside each job title.",
            4,
        )
    elif employment_entries:
        add(
            "Timeline",
            "pass",
            "Employment dates recognizable",
            f"The analyzer paired {len(employment_entries)} dated employment entr{'y' if len(employment_entries) == 1 else 'ies'} with job titles.",
            "Keep date ranges consistent and place each one beside its job title.",
            0,
        )

    if not has_email or not has_phone:
        add(
            "Contact",
            "warning",
            "Incomplete contact details",
            "The extracted text does not clearly show both email and phone details.",
            "Put email, phone, LinkedIn, and GitHub/portfolio as selectable text near the top.",
            8,
        )
    elif not has_profile_link:
        add(
            "Contact",
            "pass",
            "Contact details readable",
            "Email and phone are visible. A LinkedIn, GitHub, or portfolio link is optional and is not required for ATS parsing.",
            "Add a relevant professional link only when it strengthens the application.",
            0,
        )
    else:
        add(
            "Contact",
            "pass",
            "Contact details readable",
            "Email, phone, and professional profile markers are visible in the extracted text.",
            "Keep contact details as plain selectable text.",
            0,
        )

    quantified_evidence = sum(
        1
        for sentence in extract_sentences(resume_text)
        if has_measurable_impact(sentence)
    )
    if quantified_evidence < 3:
        add(
            "Impact",
            "warning",
            "Limited measurable impact",
            "The resume has limited numeric or outcome-based evidence.",
            "Quantify projects and experience with users, speed, accuracy, revenue, cost, or time improvements.",
            8,
        )
    else:
        add(
            "Impact",
            "pass",
            "Measurable impact found",
            "The resume includes numbers or outcome language that helps ATS and recruiters understand impact.",
            "Keep measurable results in project and work-experience bullets.",
            0,
        )

    if word_count < 250:
        add(
            "Length",
            "warning",
            "Resume may be too short",
            f"Only about {word_count} words were extracted.",
            "Add enough project, skills, and experience detail for the target role.",
            10,
        )
    elif word_count > 1300:
        add(
            "Length",
            "warning",
            "Resume may be too long",
            f"About {word_count} words were extracted.",
            "Trim older or less relevant details so the ATS version stays focused.",
            6,
        )
    else:
        add(
            "Length",
            "pass",
            "Reasonable resume length",
            f"About {word_count} words were extracted.",
            "Keep the ATS version concise and role-focused.",
            0,
        )

    skill_section_headings = {
        "core skills",
        "skills",
        "technical competencies",
        "technical skills",
        "tools and software",
    }
    has_skill_section = any(
        normalize_match_text(line) in skill_section_headings
        for line in resume_text.splitlines()
    )
    if has_skill_section:
        add(
            "Skills",
            "pass",
            "Dedicated skills section found",
            f"A clearly labelled skills section was extracted, with {len(resume_profile.skills)} term(s) recognized by the current vocabulary.",
            "Keep role-relevant tools and skills explicit; database requirement matching evaluates their relevance separately.",
            0,
        )
    elif len(resume_profile.skills) < 4:
        add(
            "Skills",
            "warning",
            "Dedicated technical skills section not found",
            "Few recognizable technical skills were detected and no clearly labelled skills section was extracted.",
            "Add a Technical Skills section with relevant languages, frameworks, databases, tools, or platforms.",
            8,
        )
    else:
        add(
            "Skills",
            "pass",
            "Technical skills detected",
            "The resume includes several recognizable technical skills.",
            "Keep the most relevant target-role skills near the top.",
            0,
        )

    total_penalty = sum(int(item.get("penalty") or 0) for item in criteria)
    quality_score = max(0, min(100, 100 - total_penalty))
    if quality_score >= 82:
        risk_level = "Low"
    elif quality_score >= 62:
        risk_level = "Medium"
    else:
        risk_level = "High"

    issues = [item for item in criteria if item["status"] != "pass"]
    summary = (
        f"ATS quality risk is {risk_level.lower()} with {len(issues)} issue(s) found."
        if issues
        else "ATS quality risk is low. The resume format looks readable and structured."
    )

    return {
        "quality_score": quality_score,
        "risk_level": risk_level,
        "word_count": word_count,
        "most_recent_job_title": get_most_recent_job_title(employment_entries) or None,
        "employment_duration_months": get_total_employment_months(employment_entries),
        "employment_entry_count": len(employment_entries),
        "document_info": info,
        "criteria": criteria,
        "issues": issues,
        "summary": summary,
    }


def extract_job_keywords(job_description: str, limit: int = 10) -> list[str]:
    candidates = tokenize_canonical_keywords(job_description)
    counts = Counter(candidates)
    ordered = [word for word, _ in counts.most_common()]
    return ordered[:limit]


def rule_based_feedback(resume_profile: ParsedResume, job_keywords: list[str]) -> tuple[list[str], list[str]]:
    resume_tokens = set(tokenize_canonical_keywords(resume_profile.text))
    keyword_hits = [keyword for keyword in job_keywords if keyword_matches_resume(keyword, resume_profile.text, resume_tokens)]
    missing_keywords = [keyword for keyword in job_keywords if keyword not in keyword_hits]

    feedback: list[str] = []
    if missing_keywords:
        feedback.append("Add or emphasize these job keywords: " + ", ".join(missing_keywords[:5]) + ".")
    else:
        feedback.append("Your resume already reflects the main job keywords well.")

    if not resume_profile.skills:
        feedback.append("Add a dedicated skills section with measurable technical tools and frameworks.")
    else:
        feedback.append("Highlight your strongest technical skills higher in the resume.")

    if not resume_profile.experience:
        feedback.append("Make your experience section more explicit by using action verbs and project outcomes.")
    else:
        feedback.append("Quantify achievements in each project or work experience bullet.")

    if not resume_profile.education:
        feedback.append("Add education details if they are missing or hard to locate.")
    else:
        feedback.append("Show relevant coursework, capstones, or research if it supports the target role.")

    return feedback, missing_keywords


def get_requirement_score_weight(requirement: dict[str, Any]) -> float:
    try:
        importance = int(requirement.get("importance_weight") or 3)
    except (TypeError, ValueError):
        importance = 3
    importance = max(1, min(5, importance))
    requirement_type = str(requirement.get("requirement_type") or "").lower()
    type_weight = REQUIREMENT_TYPE_SCORE_WEIGHTS.get(requirement_type, 1.0)
    weighted_score = importance * type_weight

    requirement_tokens = set(tokenize_canonical_keywords(str(requirement.get("requirement_text") or "")))
    optional_stack_terms = {"dotnet", "jquery", "tomcat"}
    if requirement_tokens & optional_stack_terms:
        weighted_score *= 0.65

    return weighted_score


def score_requirement_against_resume(
    resume_text: str,
    resume_tokens: set[str],
    requirement_text: str,
) -> dict[str, Any]:
    requirement_tokens = list(dict.fromkeys(tokenize_canonical_keywords(requirement_text)))
    matched_tokens = [
        token
        for token in requirement_tokens
        if keyword_matches_resume(token, resume_text, resume_tokens)
    ]

    if not requirement_tokens:
        return {"score": 0.0, "matched_tokens": [], "missing_tokens": []}

    token_coverage = (len(matched_tokens) / len(requirement_tokens)) * 100.0
    phrase_similarity = compute_tfidf_score(resume_text, requirement_text)
    score = (token_coverage * 0.85) + (phrase_similarity * 0.15)

    normalized_requirement = scoped_normalize_match_text(requirement_text)
    if normalized_requirement and normalized_requirement in scoped_normalize_match_text(resume_text):
        score = 100.0
        matched_tokens = requirement_tokens

    return {
        "score": round(min(100.0, score), 2),
        "matched_tokens": matched_tokens,
        "missing_tokens": [token for token in requirement_tokens if token not in matched_tokens],
    }


def evaluate_weighted_requirements(
    resume_text: str,
    requirements: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not requirements:
        return None

    resume_tokens = set(tokenize_canonical_keywords(resume_text))
    scored_requirements: list[dict[str, Any]] = []
    total_weight = 0.0
    weighted_score = 0.0
    all_keywords: list[str] = []
    matched_keywords: list[str] = []
    missing_keywords: list[str] = []

    for requirement in requirements:
        requirement_text = normalize_text(str(requirement.get("requirement_text") or ""))
        if not requirement_text:
            continue

        score_detail = score_requirement_against_resume(resume_text, resume_tokens, requirement_text)
        weight = get_requirement_score_weight(requirement)
        total_weight += weight
        weighted_score += score_detail["score"] * weight
        all_keywords.extend(tokenize_canonical_keywords(requirement_text))
        matched_keywords.extend(score_detail["matched_tokens"])
        missing_keywords.extend(score_detail["missing_tokens"])
        scored_requirements.append(
            {
                "requirement_text": requirement_text,
                "requirement_type": requirement.get("requirement_type"),
                "importance_weight": requirement.get("importance_weight", 3),
                "match_score": score_detail["score"],
                "matched_tokens": score_detail["matched_tokens"],
                "missing_tokens": score_detail["missing_tokens"],
            }
        )

    if not scored_requirements or total_weight <= 0:
        return None

    coverage_score = weighted_score / total_weight
    matched_requirements = [item for item in scored_requirements if item["match_score"] >= 50]
    missing_requirements = [item for item in scored_requirements if item["match_score"] < 50]

    ordered_keywords = [word for word, _ in Counter(all_keywords).most_common(20)]
    ordered_matched = [word for word, _ in Counter(matched_keywords).most_common(20)]
    matched_keyword_set = set(ordered_matched)
    ordered_missing = [word for word, _ in Counter(missing_keywords).most_common(20) if word not in matched_keyword_set]

    return {
        "requirement_coverage_score": round(coverage_score, 2),
        "important_keywords": ordered_keywords,
        "matched_keywords": ordered_matched,
        "missing_keywords": ordered_missing[:12],
        "matched_requirements": matched_requirements[:8],
        "missing_requirements": missing_requirements[:8],
        "scored_requirements": scored_requirements,
    }


def evaluate_job_fit(
    resume_text: str,
    job_description: str,
    requirements: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    similarity_score = compute_tfidf_score(resume_text, job_description)
    requirement_result = evaluate_weighted_requirements(resume_text, requirements or [])

    if requirement_result:
        job_keywords = requirement_result["important_keywords"]
        matched_keywords = requirement_result["matched_keywords"]
        missing_keywords = requirement_result["missing_keywords"]
        keyword_overlap_score = requirement_result["requirement_coverage_score"]
    else:
        resume_tokens = set(tokenize_canonical_keywords(resume_text))
        job_keywords = extract_job_keywords(job_description, limit=20)
        matched_keywords = [
            keyword
            for keyword in job_keywords
            if keyword_matches_resume(keyword, resume_text, resume_tokens)
        ]
        missing_keywords = [keyword for keyword in job_keywords if keyword not in matched_keywords]
        keyword_overlap_score = (len(matched_keywords) / max(1, len(job_keywords))) * 100.0

    combined_score = (keyword_overlap_score * 0.7) + (similarity_score * 0.3)

    result = {
        "similarity_score": round(similarity_score, 2),
        "keyword_overlap_score": round(keyword_overlap_score, 2),
        "combined_score": round(combined_score, 2),
        "important_keywords": job_keywords,
        "matched_keywords": matched_keywords,
        "missing_keywords": missing_keywords,
    }

    if requirement_result:
        result["matched_requirements"] = requirement_result["matched_requirements"]
        result["missing_requirements"] = requirement_result["missing_requirements"]
        result["scored_requirements"] = requirement_result["scored_requirements"]

    return result


def compute_group_evidence_score(resume_text: str, evidence_groups: list[set[str]]) -> float:
    if not evidence_groups:
        return 0.0

    resume_tokens = set(tokenize_canonical_keywords(resume_text))
    matched_groups = 0
    matched_group_terms: set[str] = set()

    for group in evidence_groups:
        if any(keyword_matches_resume(term, resume_text, resume_tokens) for term in group):
            matched_groups += 1
            matched_group_terms.update(group)

    group_score = (matched_groups / len(evidence_groups)) * 100.0
    impact_bonus = 0.0
    impact_pattern = re.compile(
        r"(\d+(?:\.\d+)?%|\d+\+|\$|users?|revenue|increased|reduced|improved|optimized|enabled|delivered)",
        re.I,
    )
    for segment in re.split(r"[\n.!?;]+", resume_text):
        if not impact_pattern.search(segment):
            continue
        segment_tokens = set(tokenize_canonical_keywords(segment))
        if any(
            keyword_matches_resume(term, segment, segment_tokens)
            for term in matched_group_terms
        ):
            impact_bonus = 8.0
            break

    return round(min(100.0, (group_score * 0.9) + impact_bonus), 2)


def compute_role_evidence_score(resume_text: str, role_name: str, job_keywords: list[str]) -> float:
    role_context = normalize_match_text(role_name)
    role_profile_key = {
        "application developer": "software developer",
        "fullstack developer": "full stack developer",
        "programmer": "computer programmer",
        "software engineer": "software developer",
    }.get(role_context, role_context)
    role_specific_groups = ROLE_SPECIFIC_SOFTWARE_EVIDENCE_GROUPS.get(role_profile_key)
    if role_specific_groups:
        return compute_group_evidence_score(resume_text, role_specific_groups)
    if any(term in role_context for term in ("cyber", "security", "forensic")):
        return compute_group_evidence_score(resume_text, CYBERSECURITY_EVIDENCE_GROUPS)
    if any(term in role_context for term in ("database", "oracle", "sql")):
        return compute_group_evidence_score(resume_text, DATABASE_EVIDENCE_GROUPS)
    if "network" in role_context:
        return compute_group_evidence_score(resume_text, NETWORK_EVIDENCE_GROUPS)
    if "web" in role_context:
        return compute_group_evidence_score(resume_text, WEB_DEVELOPMENT_EVIDENCE_GROUPS)
    if any(term in role_context for term in ("tester", "testing", "quality assurance")):
        return compute_group_evidence_score(resume_text, QA_TESTING_EVIDENCE_GROUPS)
    if any(term in role_context for term in ("software", "developer", "develop", "programmer", "programming")):
        return compute_group_evidence_score(resume_text, SOFTWARE_DEVELOPER_EVIDENCE_GROUPS)

    resume_tokens = set(tokenize_canonical_keywords(resume_text))
    if not job_keywords:
        return 0.0

    matched_keywords = [
        keyword
        for keyword in job_keywords
        if keyword_matches_resume(keyword, resume_text, resume_tokens)
    ]
    return round((len(matched_keywords) / max(1, len(job_keywords))) * 100.0, 2)


def compute_experience_alignment_score(
    resume_profile: ParsedResume,
    keyword_coverage: float,
    similarity_score: float,
) -> float:
    if not resume_profile.experience:
        return round((keyword_coverage * 0.45) + (similarity_score * 0.15), 2)

    marker_score = min(40, len(resume_profile.experience) * 6)
    return round(min(100, 20 + marker_score + (keyword_coverage * 0.35) + (similarity_score * 0.1)), 2)


def compute_academic_rigor_score(
    resume_profile: ParsedResume,
    keyword_coverage: float,
    similarity_score: float,
) -> float:
    education_score = 30 if resume_profile.education else 10
    skill_score = min(25, len(resume_profile.skills) * 6)
    return round(min(100, education_score + skill_score + (keyword_coverage * 0.25) + (similarity_score * 0.1)), 2)


def compute_final_readiness_score(
    keyword_match: float,
    role_evidence: float,
    experience_alignment: float,
    resume_quality_score: float,
    academic_rigor: float,
    similarity_score: float,
) -> float:
    weighted_score = (
        (keyword_match * 0.30)
        + (role_evidence * 0.25)
        + (experience_alignment * 0.15)
        + (resume_quality_score * 0.20)
        + (academic_rigor * 0.07)
        + (similarity_score * 0.03)
    )
    if (
        resume_quality_score >= 95
        and role_evidence >= 90
        and experience_alignment >= 75
        and keyword_match >= 50
    ):
        weighted_score = max(weighted_score, 82.0)

    return round(min(100.0, weighted_score), 2)


def get_database_role_name(job_match: dict[str, Any] | None) -> str:
    if not job_match:
        return ""
    role = job_match.get("role") or {}
    return str(role.get("role_name") or "")


def get_role_family(role_name: str) -> str:
    normalized = normalize_match_text(role_name)
    if not normalized:
        return ""
    if normalized in ROLE_FAMILY_ALIASES:
        return ROLE_FAMILY_ALIASES[normalized]
    for alias, family in ROLE_FAMILY_ALIASES.items():
        if alias in normalized or normalized in alias:
            return family
    return normalized


def get_calibrated_job_fit_score(
    resume_text: str,
    job_match: dict[str, Any] | None,
    fit_result: dict[str, Any] | None,
) -> float:
    if not job_match or not fit_result:
        return 0.0
    role_name = get_database_role_name(job_match)
    combined_score = float(fit_result.get("combined_score") or 0)
    role_evidence_score = compute_role_evidence_score(
        resume_text,
        role_name,
        list(fit_result.get("important_keywords") or []),
    )
    title_result = compute_career_role_title_match(
        resume_text,
        role_name,
    )
    return compute_database_role_confidence_score(
        combined_score,
        role_evidence_score,
        float(title_result["score"]),
        bool(title_result["exact_current_title_match"]),
        bool(title_result["available"]),
    )


def pick_recommended_database_job(
    resume_text: str,
    selected_job: dict[str, Any] | None,
    detected_job: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    selected_fit = evaluate_job_fit(
        resume_text,
        selected_job["job_description"],
        selected_job.get("requirements", []),
    ) if selected_job else None
    detected_fit = evaluate_job_fit(
        resume_text,
        detected_job["job_description"],
        detected_job.get("requirements", []),
    ) if detected_job else None

    if selected_job and detected_job:
        selected_name = normalize_match_text(get_database_role_name(selected_job))
        detected_name = normalize_match_text(get_database_role_name(detected_job))
        same_role_family = bool(selected_name and detected_name and get_role_family(selected_name) == get_role_family(detected_name))
        selected_score = get_calibrated_job_fit_score(resume_text, selected_job, selected_fit)
        detected_score = get_calibrated_job_fit_score(resume_text, detected_job, detected_fit)

        if selected_score > 50.0:
            return selected_job, selected_fit, detected_fit

        if selected_name != detected_name and not same_role_family and detected_score >= selected_score + ROLE_RECOMMENDATION_MARGIN:
            return detected_job, selected_fit, detected_fit
        return selected_job, selected_fit, detected_fit

    if detected_job:
        return detected_job, selected_fit, detected_fit
    if selected_job:
        return selected_job, selected_fit, detected_fit
    return None, selected_fit, detected_fit


def build_top_role_match_items(
    top_role_matches: list[dict[str, Any]] | None,
    selected_database_role: str,
    recommended_database_role: str,
) -> list[dict[str, Any]]:
    selected_key = normalize_match_text(selected_database_role)
    recommended_key = normalize_match_text(recommended_database_role)
    items: list[dict[str, Any]] = []

    for rank, match in enumerate((top_role_matches or [])[:3], start=1):
        role = match.get("role") or {}
        role_name = str(role.get("role_name") or "")
        role_key = normalize_match_text(role_name)
        fit_result = match.get("fit_result") or {}
        matched_keywords = list(dict.fromkeys(fit_result.get("matched_keywords", []) or []))[:8]
        missing_keywords = list(dict.fromkeys(fit_result.get("missing_keywords", []) or []))[:8]
        score = float(match.get("confidence_score") or fit_result.get("combined_score") or 0)

        if score >= 75:
            confidence_label = "Strong evidence"
        elif score >= 55:
            confidence_label = "Good evidence"
        elif score >= 35:
            confidence_label = "Moderate evidence"
        else:
            confidence_label = "Limited evidence"

        if matched_keywords:
            reason = "Evidence found: " + ", ".join(matched_keywords[:5]) + "."
        else:
            reason = "The resume has limited direct keyword evidence for this role."
        if missing_keywords:
            reason += " Improve with: " + ", ".join(missing_keywords[:4]) + "."

        items.append(
            {
                "rank": rank,
                "role": role_name,
                "database_role": role_name,
                "score": round(score, 2),
                "confidence_label": confidence_label,
                "keyword_match": fit_result.get("keyword_overlap_score"),
                "similarity_score": fit_result.get("similarity_score"),
                "role_evidence_score": match.get("role_evidence_score"),
                "title_match_score": match.get("title_match_score"),
                "title_match_title": match.get("title_match_title"),
                "title_match_source": match.get("title_match_source"),
                "explicit_non_it_headline": bool(match.get("explicit_non_it_headline")),
                "non_it_headline": match.get("non_it_headline"),
                "matched_keywords": matched_keywords,
                "missing_keywords": missing_keywords,
                "is_selected_database_role": bool(selected_key and role_key == selected_key),
                "is_final_recommended": bool(recommended_key and role_key == recommended_key),
                "reason": reason,
            }
        )

    return items


def build_role_recommendation(
    requested_role: str,
    selected_job: dict[str, Any] | None,
    detected_job: dict[str, Any] | None,
    recommended_job: dict[str, Any] | None,
    selected_fit: dict[str, Any] | None,
    detected_fit: dict[str, Any] | None,
    top_role_matches: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    selected_label = normalize_text(requested_role)
    selected_database_role = get_database_role_name(selected_job)
    detected_database_role = get_database_role_name(detected_job)
    recommended_database_role = get_database_role_name(recommended_job)

    recommended_fit = None
    if recommended_database_role and detected_database_role and normalize_match_text(recommended_database_role) == normalize_match_text(detected_database_role):
        recommended_fit = detected_fit
    elif recommended_database_role and selected_database_role and normalize_match_text(recommended_database_role) == normalize_match_text(selected_database_role):
        recommended_fit = selected_fit

    def clean_score(value: Any) -> float | None:
        try:
            return round(float(value), 2)
        except (TypeError, ValueError):
            return None

    def calibrated_match_score(role_name: str) -> float | None:
        role_key = normalize_match_text(role_name)
        if not role_key:
            return None
        for match in top_role_matches or []:
            match_role = match.get("role") or {}
            match_role_key = normalize_match_text(str(match_role.get("role_name") or ""))
            if match_role_key != role_key:
                continue
            return clean_score(
                match.get("confidence_score")
                or (match.get("fit_result") or {}).get("combined_score")
            )
        return None

    def fit_combined_score(fit_result: dict[str, Any] | None) -> float | None:
        return clean_score((fit_result or {}).get("combined_score"))

    selected_score = calibrated_match_score(selected_database_role)
    if selected_score is None:
        selected_score = fit_combined_score(selected_fit)
    detected_score = calibrated_match_score(detected_database_role)
    if detected_score is None:
        detected_score = fit_combined_score(detected_fit)
    recommended_score = calibrated_match_score(recommended_database_role)
    if recommended_score is None:
        recommended_score = fit_combined_score(recommended_fit)

    different_from_selected = bool(
        selected_database_role
        and recommended_database_role
        and normalize_match_text(selected_database_role) != normalize_match_text(recommended_database_role)
    )

    matched_signals = (recommended_fit or {}).get("matched_keywords", [])
    selected_missing = (selected_fit or {}).get("missing_keywords", [])

    if different_from_selected:
        selected_name = selected_label or selected_database_role
        reason_summary = (
            f"The resume is stronger for {recommended_database_role} than {selected_name} because it matches "
            f"{', '.join(matched_signals[:5]) or 'more of the database requirements'}."
        )
        if selected_missing:
            reason_summary += " The selected role still needs stronger evidence for " + ", ".join(selected_missing[:5]) + "."
    elif selected_database_role:
        reason_summary = (
            f"The selected role maps to {selected_database_role}, and it remains the best database match for this resume."
        )
        if matched_signals:
            reason_summary += " Strong signals include " + ", ".join(matched_signals[:5]) + "."
    elif recommended_database_role:
        reason_summary = (
            f"No selected database role was used, so the system recommends {recommended_database_role} from the resume content."
        )
        if matched_signals:
            reason_summary += " The strongest signals are " + ", ".join(matched_signals[:5]) + "."
    else:
        reason_summary = "The system could not compare this resume with a database role."

    ranked_scores = [
        score
        for score in (
            clean_score(
                match.get("confidence_score")
                or (match.get("fit_result") or {}).get("combined_score")
            )
            for match in (top_role_matches or [])[:2]
        )
        if score is not None
    ]
    top_score_gap = round(abs(ranked_scores[0] - ranked_scores[1]), 2) if len(ranked_scores) == 2 else None
    is_close_match = bool(top_score_gap is not None and top_score_gap < 3.0)
    selected_key = normalize_match_text(selected_label)
    has_explicit_selection = bool(
        selected_key
        and selected_key not in {"selected role", "auto detect", "auto detect from resume", "sample role"}
    )
    explicit_non_it_match = next(
        (
            match
            for match in (top_role_matches or [])
            if bool(match.get("explicit_non_it_headline"))
            or str(match.get("title_match_source") or "") == "non_it_headline"
        ),
        None,
    )
    explicit_non_it_headline = bool(explicit_non_it_match)
    non_it_headline = normalize_text(
        str(
            (explicit_non_it_match or {}).get("non_it_headline")
            or (
                (explicit_non_it_match or {}).get("title_match_title")
                if (explicit_non_it_match or {}).get("title_match_source") == "non_it_headline"
                else ""
            )
        )
    )
    review_threshold = (
        NON_IT_HEADLINE_REVIEW_THRESHOLD
        if explicit_non_it_headline
        else CS_SAMPLE_ONLY_TOP_MATCH_THRESHOLD
    )

    def score_needs_review(score: float | None) -> bool:
        if score is None:
            return False
        return bool(
            score < CS_SAMPLE_ONLY_TOP_MATCH_THRESHOLD
            or (
                explicit_non_it_headline
                and score < NON_IT_HEADLINE_REVIEW_THRESHOLD
            )
        )

    if (
        has_explicit_selection
        and score_needs_review(selected_score)
    ):
        match_status = "review_required"
        human_review_required = True
        reason_summary = (
            f"The selected role has {selected_score:.2f}/100 evidence, below the applicable "
            f"{review_threshold:.0f}/100 review threshold"
            + (f" and the resume headline is {non_it_headline}." if non_it_headline else ".")
            + " Treat this as REVIEW REQUIRED, "
            "not as a confirmed match; the resume builder can show a separate selected-role example."
        )
    elif (
        not has_explicit_selection
        and score_needs_review(recommended_score)
    ):
        match_status = "no_suitable_role"
        human_review_required = True
        reason_summary = (
            f"No database role reached the applicable {review_threshold:.0f}/100 evidence review threshold. "
            f"The closest role was {recommended_database_role or 'unavailable'} at {recommended_score:.2f}/100, "
            + (f"while the explicit resume headline is {non_it_headline}. " if non_it_headline else "")
            + "It is shown only for human review and is not a confirmed recommendation."
        )
    elif recommended_database_role or selected_database_role:
        match_status = "matched"
        human_review_required = False
    elif has_explicit_selection:
        match_status = "custom_role"
        human_review_required = False
    else:
        match_status = "unavailable"
        human_review_required = True

    if is_close_match and not human_review_required:
        reason_summary += (
            f" The top two roles are separated by only {top_score_gap:.2f} points, so this is a close evidence match; "
            "review both role descriptions instead of treating the first result as certain."
        )

    return {
        "selected_role": selected_label,
        "selected_database_role": selected_database_role,
        "detected_database_role": detected_database_role,
        "recommended_role": recommended_database_role or selected_label,
        "recommended_database_role": recommended_database_role,
        "is_different_from_selected": different_from_selected,
        "selected_score": selected_score,
        "detected_score": detected_score,
        "recommended_score": recommended_score,
        "match_status": match_status,
        "human_review_required": human_review_required,
        "review_threshold": review_threshold,
        "explicit_non_it_headline": explicit_non_it_headline,
        "non_it_headline": non_it_headline,
        "closest_role": recommended_database_role,
        "closest_role_score": recommended_score,
        "top_score_gap": top_score_gap,
        "is_close_match": is_close_match,
        "matched_resume_signals": matched_signals,
        "recommended_missing_keywords": (recommended_fit or {}).get("missing_keywords", []),
        "selected_missing_keywords": selected_missing,
        "top_role_matches": build_top_role_match_items(
            top_role_matches,
            selected_database_role,
            recommended_database_role,
        ),
        "reason_summary": reason_summary,
    }


def build_recommendation_action(
    priority: int,
    category: str,
    title: str,
    detail: str,
    action: str,
    keywords: list[Any] | None = None,
    source: str = "analysis",
) -> dict[str, Any]:
    return {
        "priority": priority,
        "category": category,
        "title": normalize_text(title),
        "detail": normalize_text(detail),
        "action": normalize_text(action),
        "keywords": unique_clean_values(list(keywords or []), 6),
        "source": source,
    }


def build_part8_recommendation_module(analysis: dict[str, Any]) -> dict[str, Any]:
    role_recommendation = analysis.get("role_recommendation") if isinstance(analysis.get("role_recommendation"), dict) else {}
    job_analysis = analysis.get("job_description_analysis") if isinstance(analysis.get("job_description_analysis"), dict) else {}
    resume_summary = analysis.get("resume_summary") if isinstance(analysis.get("resume_summary"), dict) else {}
    resume_quality = analysis.get("resume_quality") if isinstance(analysis.get("resume_quality"), dict) else {}
    compatibility = analysis.get("compatibility") if isinstance(analysis.get("compatibility"), dict) else {}

    recommended_role = normalize_text(
        str(
            role_recommendation.get("recommended_role")
            or role_recommendation.get("recommended_database_role")
            or analysis.get("target_role")
            or analysis.get("selected_role")
            or "selected role"
        )
    )
    selected_role = normalize_text(str(role_recommendation.get("selected_role") or analysis.get("selected_role") or ""))
    matched_signals = unique_clean_values(
        list(role_recommendation.get("matched_resume_signals") or [])
        + list(job_analysis.get("matched_keywords") or [])
        + list(resume_summary.get("skills") or []),
        12,
    )
    missing_keywords = unique_clean_values(
        list(role_recommendation.get("recommended_missing_keywords") or [])
        + list(job_analysis.get("missing_keywords") or []),
        12,
    )
    selected_missing_keywords = unique_clean_values(list(role_recommendation.get("selected_missing_keywords") or []), 8)
    display_missing_keywords = unique_clean_values([format_resume_keyword(keyword) for keyword in missing_keywords], 10)
    display_matched_signals = unique_clean_values([format_resume_keyword(keyword) for keyword in matched_signals], 10)
    quality_issues = [
        item
        for item in list(resume_quality.get("issues") or [])
        if isinstance(item, dict)
    ]
    keyword_match = compatibility.get("keyword_match")
    role_evidence = compatibility.get("role_evidence")
    experience_alignment = compatibility.get("experience_alignment")
    academic_rigor = compatibility.get("academic_rigor")
    resume_quality_score = compatibility.get("resume_quality", resume_quality.get("quality_score"))
    match_status = normalize_match_text(str(role_recommendation.get("match_status") or ""))
    human_review_required = bool(role_recommendation.get("human_review_required"))
    fallback_sample_role = normalize_text(str(role_recommendation.get("fallback_sample_role") or ""))

    actions: list[dict[str, Any]] = []

    if human_review_required:
        selected_review = match_status == "review required" and bool(selected_role)
        actions.append(
            build_recommendation_action(
                1,
                "role_fit_review",
                "Review the role evidence before choosing a target",
                str(role_recommendation.get("reason_summary") or "The available role evidence is below the review threshold."),
                (
                    f"Use the separate {selected_role} example only as a learning reference and add only evidence you genuinely have."
                    if selected_review
                    else "Review the closest role cards manually. The resume builder will provide a clearly labelled fallback example, not rewrite unrelated experience."
                ),
                display_matched_signals[:5],
                "role_review_gate",
            )
        )
    elif role_recommendation.get("is_different_from_selected") and selected_role:
        actions.append(
            build_recommendation_action(
                1,
                "role_fit",
                "Confirm the best target role",
                f"The system found stronger evidence for {recommended_role} than {selected_role}.",
                f"Use {recommended_role} as the main resume target, or rewrite the resume with clearer evidence for {selected_role}.",
                display_matched_signals[:5],
                "role_recommendation",
            )
        )
    elif recommended_role:
        actions.append(
            build_recommendation_action(
                1,
                "role_fit",
                "Use the confirmed comparison role",
                f"The resume is being compared against {recommended_role}.",
                f"Keep the resume headline, summary, skills, and project bullets focused on {recommended_role}.",
                display_matched_signals[:5],
                "role_recommendation",
            )
        )

    if display_missing_keywords:
        actions.append(
            build_recommendation_action(
                len(actions) + 1,
                "keyword_gap",
                "Add missing role keywords with proof",
                "The database requirements include keywords that are weak or missing in the resume.",
                "Add these terms only inside truthful project, coursework, internship, or work-experience evidence.",
                display_missing_keywords[:6],
                "database_requirements",
            )
        )

    if percent_status_label(keyword_match) == "needs_improvement" or percent_status_label(role_evidence) == "needs_improvement":
        actions.append(
            build_recommendation_action(
                len(actions) + 1,
                "evidence_gap",
                "Strengthen database requirement evidence",
                "The resume needs clearer proof that it satisfies the selected role requirements.",
                "Rewrite two or three bullets using this pattern: action verb + tool/skill + task + measurable result.",
                display_missing_keywords[:4] or display_matched_signals[:4],
                "score_breakdown",
            )
        )

    if percent_status_label(experience_alignment) == "needs_improvement" or not resume_summary.get("experience_markers"):
        actions.append(
            build_recommendation_action(
                len(actions) + 1,
                "experience",
                "Improve project and experience bullets",
                "Experience evidence is one of the main areas that can raise the readiness score.",
                "Add project scope, your responsibility, technologies used, and measurable outcomes such as users, accuracy, speed, or completion time.",
                display_matched_signals[:4],
                "resume_summary",
            )
        )

    if quality_issues:
        first_issue = quality_issues[0]
        actions.append(
            build_recommendation_action(
                len(actions) + 1,
                "ats_quality",
                str(first_issue.get("title") or "Fix ATS readability issue"),
                str(first_issue.get("detail") or resume_quality.get("summary") or "ATS quality issue detected."),
                str(first_issue.get("suggestion") or "Fix the highest-risk formatting issue before exporting the resume."),
                [],
                "ats_quality",
            )
        )

    if percent_status_label(academic_rigor) == "needs_improvement":
        actions.append(
            build_recommendation_action(
                len(actions) + 1,
                "education",
                "Connect education to the target role",
                "Education or coursework evidence is not strong enough for this role yet.",
                "Add relevant coursework, capstone, research, certification, or academic project evidence if it is true.",
                display_missing_keywords[:4],
                "resume_summary",
            )
        )

    if len(actions) <= 2 and percent_status_label(resume_quality_score) == "strong":
        actions.append(
            build_recommendation_action(
                len(actions) + 1,
                "impact",
                "Polish with measurable impact",
                "The resume has a usable foundation, so the next gain is stronger evidence quality.",
                "Add numbers or outcomes to the strongest bullets, then keep only the most relevant role keywords.",
                display_matched_signals[:4],
                "polish",
            )
        )

    actions = actions[:5]
    for index, action in enumerate(actions, start=1):
        action["priority"] = index

    section_targets = []
    action_categories = {action["category"] for action in actions}
    if "role_fit" in action_categories:
        section_targets.extend(["headline", "professional summary"])
    if "keyword_gap" in action_categories or "evidence_gap" in action_categories:
        section_targets.extend(["technical skills", "projects", "experience bullets"])
    if "experience" in action_categories or "impact" in action_categories:
        section_targets.extend(["professional experience", "projects"])
    if "education" in action_categories:
        section_targets.extend(["education", "certifications"])
    if "ats_quality" in action_categories:
        section_targets.append("resume format")

    strongest_action = actions[0]["title"] if actions else "review the resume evidence"
    if human_review_required:
        summary = f"Review required: no role is confirmed. Priority: {strongest_action}."
    else:
        summary = (
            f"Recommended role: {recommended_role}. Priority: {strongest_action}."
            if recommended_role
            else f"Priority: {strongest_action}."
        )
    if display_missing_keywords:
        summary += " Main keyword targets: " + ", ".join(display_missing_keywords[:5]) + "."
    elif display_matched_signals:
        summary += " Main evidence to keep: " + ", ".join(display_matched_signals[:5]) + "."

    return {
        "stage": "Part 8 - Recommendation and generated resume support",
        "status": "review_required" if human_review_required else "ready",
        "method": "Converts hybrid NLP text matching, weighted database requirements, missing keywords, score components, and ATS quality checks into user-facing improvement actions.",
        "recommended_role": recommended_role,
        "selected_role": selected_role,
        "summary": summary,
        "priority_actions": actions,
        "resume_generation_strategy": {
            "target_role": fallback_sample_role if match_status == "no suitable role" and fallback_sample_role else recommended_role,
            "strategy_summary": (
                (
                    f"Show a clearly labelled {fallback_sample_role or 'technology'} example for learning; do not rewrite the unrelated resume as a matched CS resume."
                    if match_status == "no suitable role"
                    else f"Generate the ATS resume around {recommended_role}, keep proven resume signals, and add missing keywords only where the user can support them with real evidence."
                )
            ),
            "sections_to_update": unique_clean_values(section_targets, 7),
            "keyword_targets": display_missing_keywords[:8],
            "evidence_to_keep": display_matched_signals[:8],
            "selected_role_gaps": selected_missing_keywords[:6],
        },
    }


def build_analysis_payload(
    resume_text: str,
    job_description: str,
    uploaded_name: str,
    requirements: list[dict[str, Any]] | None = None,
    target_role: str = "",
    document_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resume_profile = extract_resume_profile(resume_text)
    resume_quality = analyze_resume_quality(resume_text, uploaded_name, document_info, resume_profile)
    fit_result = evaluate_job_fit(resume_text, job_description, requirements)
    job_keywords = fit_result["important_keywords"]
    rule_feedback, missing_keywords = rule_based_feedback(resume_profile, job_keywords)
    similarity_score = fit_result["similarity_score"]
    keyword_coverage = fit_result["keyword_overlap_score"]

    keyword_match = round(keyword_coverage)
    role_evidence = round(compute_role_evidence_score(resume_text, target_role, job_keywords))
    experience_alignment = round(compute_experience_alignment_score(resume_profile, keyword_coverage, similarity_score))
    academic_rigor = round(compute_academic_rigor_score(resume_profile, keyword_coverage, similarity_score))
    resume_quality_score = round(resume_quality["quality_score"])
    ats_quality_penalty = max(0, 100 - resume_quality_score)
    ats_score = compute_final_readiness_score(
        keyword_match,
        role_evidence,
        experience_alignment,
        resume_quality_score,
        academic_rigor,
        similarity_score,
    )
    title_result = compute_career_role_title_match(resume_text, target_role)
    title_match_score = float(title_result["score"])
    exact_title_match = bool(title_result["exact_current_title_match"])
    if (
        resume_quality_score >= 95
        and role_evidence >= 90
        and experience_alignment >= 70
        and (exact_title_match or title_match_score >= 70)
        and keyword_match >= 35
    ):
        ats_score = max(ats_score, 82.0)

    summary = "Resume parsing identified {} skill(s), {} education marker(s), and {} experience marker(s).".format(
        len(resume_profile.skills), len(resume_profile.education), len(resume_profile.experience)
    )
    recommendation = rule_feedback[0] if rule_feedback else "Refine the resume to better match the target role."
    quality_feedback = [
        f"ATS format issue: {issue['suggestion']}"
        for issue in resume_quality.get("issues", [])[:2]
    ]

    return {
        "analysis_title": APP_TITLE,
        "uploaded_resume_name": uploaded_name,
        "matching_percentage": ats_score,
        "resume_excerpt": resume_profile.excerpt,
        "resume_summary": {
            "skills": resume_profile.skills,
            "education_markers": resume_profile.education,
            "experience_markers": resume_profile.experience,
        },
        "resume_quality": resume_quality,
        "job_description_analysis": {
            "important_keywords": job_keywords,
            "missing_keywords": missing_keywords,
            "matched_keywords": fit_result["matched_keywords"],
            "matched_requirements": fit_result.get("matched_requirements", []),
            "missing_requirements": fit_result.get("missing_requirements", []),
        },
        "compatibility": {
            "matching_percentage": ats_score,
            "keyword_match": keyword_match,
            "role_evidence": role_evidence,
            "experience_alignment": experience_alignment,
            "academic_rigor": academic_rigor,
            "resume_quality": resume_quality_score,
        },
        "score_breakdown": {
            "ats_score": ats_score,
            "keyword_coverage": keyword_match,
            "role_evidence": role_evidence,
            "experience_alignment": experience_alignment,
            "academic_rigor": academic_rigor,
            "resume_quality": resume_quality_score,
            "ats_quality_penalty": ats_quality_penalty,
            "ats_weighted_deduction": round(ats_quality_penalty * 0.20, 2),
            "tfidf_similarity": round(similarity_score, 2),
            "title_match": round(title_match_score),
            "most_recent_job_title": title_result.get("most_recent_title") or None,
            "employment_duration_months": resume_quality.get("employment_duration_months"),
            "employment_duration_note": "Advisory extraction only; minimum-experience filtering is not applied without a structured job requirement.",
            "formula": "30% database requirement coverage + 25% role evidence + 15% experience evidence + 20% ATS format quality + 7% education/skills evidence + 3% TF-IDF similarity, with a strong-evidence calibration floor",
        },
        "feedback": " ".join(rule_feedback),
        "structured_feedback": quality_feedback + rule_feedback,
        "summary": summary,
        "recommendation": recommendation,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


RESUME_FILE_CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}
_resume_file_table_ready = False


class ResumeUpload(NamedTuple):
    file_name: str
    content_type: str
    data: bytes


def read_resume_upload(file_storage) -> ResumeUpload | None:
    """Return the uploaded bytes after text extraction has already validated them."""
    content_type = RESUME_FILE_CONTENT_TYPES.get(get_file_extension(file_storage.filename or ""))
    if not content_type:
        return None
    try:
        file_storage.stream.seek(0)
        data = file_storage.stream.read()
    except (OSError, ValueError):
        return None
    if not data:
        return None
    return ResumeUpload(str(file_storage.filename)[:255], content_type, bytes(data))


def ensure_resume_file_table(connection) -> bool:
    """Create the original-file table on first use.

    Render Free has no pre-deploy step, so a deployment can start before
    `manage.py migrate` has been run against the hosted database.
    """
    global _resume_file_table_ready
    if _resume_file_table_ready:
        return True
    cursor = connection.cursor()
    try:
        cursor.execute(resume_files_table_sql())
        connection.commit()
        _resume_file_table_ready = True
    except DatabaseErrors:
        connection.rollback()
        app.logger.warning("Original resume file storage is unavailable.", exc_info=True)
    finally:
        cursor.close()
    return _resume_file_table_ready


def insert_resume_file(cursor, analysis_id: int, upload: ResumeUpload) -> None:
    # A savepoint keeps the analysis when only the file insert fails.
    cursor.execute("SAVEPOINT resume_file_upload")
    try:
        cursor.execute(
            """
            INSERT INTO resume_files (analysis_id, file_name, content_type, file_size, file_data)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (analysis_id, upload.file_name, upload.content_type, len(upload.data), upload.data),
        )
    except DatabaseErrors:
        cursor.execute("ROLLBACK TO SAVEPOINT resume_file_upload")
        app.logger.warning("Could not store the original resume file for analysis %s.", analysis_id, exc_info=True)
    else:
        cursor.execute("RELEASE SAVEPOINT resume_file_upload")


def persist_analysis(
    payload: dict[str, Any],
    job_description: str,
    target_role: str,
    user_id: int,
    resume_upload: ResumeUpload | None = None,
) -> int:
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        store_file = resume_upload is not None and ensure_resume_file_table(connection)
        cursor = connection.cursor()
        cursor.execute(
            """
            INSERT INTO resume_analyses (
                user_id,
                uploaded_resume_name,
                target_role,
                matching_percentage,
                feedback,
                job_description,
                resume_excerpt,
                analysis_json
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """ + (" RETURNING id" if is_postgres_backend() else ""),
            (
                int(user_id),
                payload.get("uploaded_resume_name", "resume.pdf"),
                target_role or "selected role",
                payload["compatibility"]["matching_percentage"],
                payload["feedback"],
                job_description,
                payload.get("resume_excerpt", ""),
                json.dumps(payload),
            ),
        )
        analysis_id = int(cursor.fetchone()[0] if is_postgres_backend() else cursor.lastrowid)
        if store_file:
            insert_resume_file(cursor, analysis_id, resume_upload)
        connection.commit()
        return analysis_id
    except Exception:
        if connection is not None:
            connection.rollback()
        raise
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()


def infer_accuracy_resume_id(uploaded_name: str) -> str:
    """Read an explicit test ID from a filename without deriving ground truth."""
    name = os.path.splitext(os.path.basename(uploaded_name or ""))[0].lower()
    direct_match = re.search(r"(?<![a-z0-9])(ats|sd|da|cy|ml)[\s_-]*0?(\d{1,2})(?!\d)", name)
    if direct_match:
        prefix = direct_match.group(1).upper()
        separator = "-" if prefix == "ATS" else ""
        return f"{prefix}{separator}{int(direct_match.group(2)):02d}"
    return ""


def get_analysis_recommended_role(analysis: dict[str, Any]) -> str:
    role_recommendation = analysis.get("role_recommendation") or {}
    match_status = normalize_match_text(str(role_recommendation.get("match_status") or ""))
    if match_status == "no suitable role":
        return "NO SUITABLE ROLE"
    return normalize_text(
        role_recommendation.get("recommended_role")
        or role_recommendation.get("recommended_database_role")
        or ""
    )


def build_accuracy_missing_keywords_note(analysis: dict[str, Any]) -> str:
    job_analysis = analysis.get("job_description_analysis") or {}
    role_recommendation = analysis.get("role_recommendation") or {}
    missing_keywords = list(job_analysis.get("missing_keywords") or [])
    if not missing_keywords:
        missing_keywords = list(role_recommendation.get("recommended_missing_keywords") or [])

    note_parts: list[str] = []
    if missing_keywords:
        note_parts.append("Missing: " + ", ".join(str(item) for item in missing_keywords[:8]))

    matched_keywords = list(job_analysis.get("matched_keywords") or [])
    if matched_keywords:
        note_parts.append("Matched: " + ", ".join(str(item) for item in matched_keywords[:8]))

    return " | ".join(note_parts)[:500]


ACCURACY_TESTING_HEADER_ALIASES = {
    "test id": "test_id",
    "resume id": "test_id",
    "test mode": "test_mode",
    "resume scenario": "scenario",
    "target role": "target_role",
    "selected role": "selected_role",
    "expected role": "expected_role",
    "evidence basis": "evidence",
    "professional resume evidence": "evidence",
    "expected fit": "expected_fit",
    "expected answer": "expected_answer",
    "recommended role": "recommended_role",
    "ats score": "ats_score",
    "ats feedback": "ats_feedback",
    "system fit": "system_fit",
    "system answer": "system_answer",
    "check": "check",
    "keyword notes": "keyword_notes",
    "explanation notes": "explanation_notes",
    "source file": "source_file",
}

ACCURACY_TESTING_UNREGISTERED_WARNING = (
    "The uploaded resume is not registered in Accuracy Testing. "
    "Set its Source File, Test Mode, and Selected Role (when used), or include its prepared "
    "test ID in the filename before running the test."
)
ACCURACY_TESTING_CASES_TABLE = "AccuracyCasesTable"


def normalize_accuracy_case_id(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def normalize_accuracy_testing_mode(value: Any) -> str:
    normalized = normalize_match_text(str(value or ""))
    if normalized in {"selected", "selected role", "target role"}:
        return "selected_role"
    if normalized in {"auto", "auto detect", "auto detected", "automatic"}:
        return "auto_detect"
    return ""


def canonicalize_accuracy_role(value: Any) -> str:
    """Return an exact comparison key, resolving only known role aliases.

    This deliberately does not use ``get_role_family``: auto-detect accuracy
    needs to distinguish, for example, Data Analyst from Data Scientist even
    though both roles belong to the broader data/AI family.
    """

    normalized = normalize_match_text(str(value or ""))
    if not normalized:
        return ""
    alias_target = POPULAR_ROLE_ALIASES.get(normalized)
    return normalize_match_text(alias_target) if alias_target else normalized


def get_accuracy_analysis_mode(analysis: dict[str, Any]) -> str:
    return "selected_role" if normalize_text(str(analysis.get("selected_role") or "")) else "auto_detect"


def get_combined_accuracy_testing_data_end_row(sheet, header_row: int) -> int:
    """Return the combined-case table boundary with a legacy-size fallback."""
    fallback_end_row = header_row + 20
    tables = getattr(sheet, "tables", None)
    if not tables:
        return fallback_end_row

    try:
        table = tables.get(ACCURACY_TESTING_CASES_TABLE)
    except (AttributeError, KeyError, TypeError):
        return fallback_end_row
    table_ref = getattr(table, "ref", "")
    match = re.fullmatch(
        r"\s*\$?[A-Z]{1,3}\$?(\d+):\$?[A-Z]{1,3}\$?(\d+)\s*",
        str(table_ref or ""),
        flags=re.IGNORECASE,
    )
    if not match:
        return fallback_end_row

    table_header_row = int(match.group(1))
    table_end_row = int(match.group(2))
    if table_header_row != header_row or table_end_row <= header_row:
        return fallback_end_row
    return table_end_row


def get_accuracy_testing_schema(sheet) -> dict[str, Any] | None:
    """Discover the combined, simplified, or legacy testing columns."""
    max_row = int(getattr(sheet, "max_row", 0) or 0)
    max_column = max(15, int(getattr(sheet, "max_column", 0) or 0))
    for header_row in range(1, min(max_row, 15) + 1):
        columns: dict[str, int] = {}
        raw_headers: set[str] = set()
        for column in range(1, min(max_column, 30) + 1):
            raw_header = normalize_match_text(str(sheet.cell(row=header_row, column=column).value or ""))
            if not raw_header:
                continue
            raw_headers.add(raw_header)
            canonical = ACCURACY_TESTING_HEADER_ALIASES.get(raw_header)
            if canonical:
                columns[canonical] = column

        if {
            "test_id",
            "test_mode",
            "selected_role",
            "expected_role",
            "evidence",
            "expected_fit",
            "ats_score",
            "recommended_role",
            "system_fit",
            "check",
            "source_file",
        }.issubset(columns):
            return {
                "kind": "combined",
                "header_row": header_row,
                "data_end_row": get_combined_accuracy_testing_data_end_row(
                    sheet,
                    header_row,
                ),
                "columns": columns,
            }

        if "test id" in raw_headers and {
            "test_id",
            "target_role",
            "expected_answer",
            "ats_score",
            "ats_feedback",
            "system_answer",
            "check",
        }.issubset(columns):
            return {"kind": "simplified", "header_row": header_row, "columns": columns}

        if "resume id" in raw_headers and {
            "test_id",
            "expected_role",
            "recommended_role",
            "ats_score",
            "keyword_notes",
            "explanation_notes",
        }.issubset(columns):
            legacy_columns = dict(columns)
            legacy_columns["target_role"] = columns["expected_role"]
            return {"kind": "legacy", "header_row": header_row, "columns": legacy_columns}
    return None


def get_accuracy_testing_sheet(workbook):
    for sheet_name in ("Accuracy Testing", "Two Examples"):
        if sheet_name in workbook.sheetnames:
            return workbook[sheet_name]
    return None


def validate_accuracy_testing_case(sheet, schema: dict[str, Any], row: int) -> str:
    columns = schema["columns"]
    case_id = normalize_text(str(sheet.cell(row=row, column=columns["test_id"]).value or ""))
    if schema["kind"] == "combined":
        if not case_id:
            return "The prepared test case needs a Test ID before testing."

        mode = normalize_accuracy_testing_mode(
            sheet.cell(row=row, column=columns["test_mode"]).value
        )
        selected_role = normalize_text(
            str(sheet.cell(row=row, column=columns["selected_role"]).value or "")
        )
        expected_role = normalize_text(
            str(sheet.cell(row=row, column=columns["expected_role"]).value or "")
        )
        evidence = normalize_text(
            str(sheet.cell(row=row, column=columns["evidence"]).value or "")
        )
        expected_fit = normalize_text(
            str(sheet.cell(row=row, column=columns["expected_fit"]).value or "")
        ).upper()

        if mode == "selected_role":
            if not selected_role or expected_fit not in {"MATCH", "NOT MATCH"}:
                return (
                    "A Selected role test needs a Selected Role and an Expected Fit "
                    "of MATCH or NOT MATCH before testing."
                )
            if not evidence:
                return "A Selected role test needs an Evidence Basis before testing."
            return ""

        if mode == "auto_detect":
            if not expected_role:
                return "An Auto-detect test needs an Expected Role before testing."
            if selected_role or expected_fit:
                return "An Auto-detect test must leave Selected Role and Expected Fit blank."
            if not evidence:
                return "An Auto-detect test needs an Evidence Basis before testing."
            return ""

        return "Test Mode must be Selected role or Auto-detect before testing."

    target_role = normalize_text(str(sheet.cell(row=row, column=columns["target_role"]).value or ""))
    if not case_id or not target_role:
        return "The matching test case needs a Test ID and Target Role before testing."
    if schema["kind"] == "simplified":
        expected_answer = normalize_text(
            str(sheet.cell(row=row, column=columns["expected_answer"]).value or "")
        ).upper()
        if expected_answer not in {"MATCH", "NOT MATCH"}:
            return "The matching test case needs an Expected Answer of MATCH or NOT MATCH before testing."
    return ""


def combined_accuracy_row_matches_analysis(
    sheet,
    schema: dict[str, Any],
    row: int,
    analysis: dict[str, Any],
) -> bool:
    columns = schema["columns"]
    actual_mode = get_accuracy_analysis_mode(analysis)
    row_mode = normalize_accuracy_testing_mode(
        sheet.cell(row=row, column=columns["test_mode"]).value
    )
    if row_mode != actual_mode:
        return False
    if actual_mode == "auto_detect":
        return True

    actual_selected_role = canonicalize_accuracy_role(analysis.get("selected_role"))
    prepared_selected_role = canonicalize_accuracy_role(
        sheet.cell(row=row, column=columns["selected_role"]).value
    )
    return bool(
        actual_selected_role
        and prepared_selected_role
        and actual_selected_role == prepared_selected_role
    )


def find_accuracy_testing_row(workbook, analysis: dict[str, Any]) -> tuple[Any | None, int | None, str]:
    sheet = get_accuracy_testing_sheet(workbook)
    if sheet is None:
        return None, None, "Accuracy Testing sheet was not found."

    schema = get_accuracy_testing_schema(sheet)
    if schema is None:
        return sheet, None, "Accuracy Testing headers were not recognised."

    uploaded_name = str(analysis.get("uploaded_resume_name") or "")
    uploaded_key = normalize_match_text(os.path.basename(uploaded_name))
    inferred_resume_id = infer_accuracy_resume_id(uploaded_name)
    columns = schema["columns"]
    first_data_row = int(schema["header_row"]) + 1
    last_data_row = int(getattr(sheet, "max_row", first_data_row) or first_data_row)
    if schema["kind"] == "combined":
        last_data_row = min(last_data_row, int(schema.get("data_end_row") or last_data_row))

    source_matches: list[int] = []
    source_column = columns.get("source_file")
    if source_column:
        for row in range(first_data_row, last_data_row + 1):
            source_file = normalize_match_text(
                os.path.basename(str(sheet.cell(row=row, column=source_column).value or ""))
            )
            if uploaded_key and source_file and uploaded_key == source_file:
                source_matches.append(row)

    if schema["kind"] == "combined":
        source_matches = [
            row
            for row in source_matches
            if combined_accuracy_row_matches_analysis(sheet, schema, row, analysis)
        ]

    if len(source_matches) > 1:
        duplicate_description = "source, mode, and selected role" if schema["kind"] == "combined" else "source"
        return sheet, None, (
            "The uploaded resume is assigned to more than one prepared test case "
            f"with the same {duplicate_description}."
        )
    if source_matches:
        row = source_matches[0]
        warning = validate_accuracy_testing_case(sheet, schema, row)
        if warning:
            return sheet, None, warning
        return sheet, row, ""

    id_matches: list[int] = []
    for row in range(first_data_row, last_data_row + 1):
        resume_id = sheet.cell(row=row, column=columns["test_id"]).value
        if inferred_resume_id and normalize_accuracy_case_id(resume_id) == normalize_accuracy_case_id(inferred_resume_id):
            if schema["kind"] != "combined" or combined_accuracy_row_matches_analysis(
                sheet, schema, row, analysis
            ):
                id_matches.append(row)

    if len(id_matches) > 1:
        return sheet, None, "The prepared test ID has more than one matching mode and selected-role case."
    if id_matches:
        row = id_matches[0]
        warning = validate_accuracy_testing_case(sheet, schema, row)
        if warning:
            return sheet, None, warning
        return sheet, row, ""

    if schema["kind"] == "legacy" and "Resume Dataset" in workbook.sheetnames:
        dataset = workbook["Resume Dataset"]
        for data_row in range(5, dataset.max_row + 1):
            dataset_resume_id = normalize_text(str(dataset.cell(row=data_row, column=1).value or ""))
            fake_file_name = normalize_match_text(
                os.path.basename(str(dataset.cell(row=data_row, column=4).value or ""))
            )
            if uploaded_key and fake_file_name and uploaded_key == fake_file_name:
                for row in range(first_data_row, last_data_row + 1):
                    row_resume_id = normalize_text(
                        str(sheet.cell(row=row, column=columns["test_id"]).value or "")
                    )
                    if row_resume_id == dataset_resume_id:
                        warning = validate_accuracy_testing_case(sheet, schema, row)
                        if warning:
                            return sheet, None, warning
                        return sheet, row, ""

    return sheet, None, ACCURACY_TESTING_UNREGISTERED_WARNING


def compact_accuracy_testing_analysis(analysis: dict[str, Any]) -> dict[str, Any]:
    role_recommendation = analysis.get("role_recommendation") or {}
    job_analysis = analysis.get("job_description_analysis") or {}
    tested_at = (
        analysis.get("accuracy_tested_at")
        or analysis.get("queued_at")
        or analysis.get("generated_at")
        or datetime.now().astimezone().isoformat()
    )
    top_role_matches = list(
        analysis.get("top_role_matches")
        or role_recommendation.get("top_role_matches")
        or []
    )[:3]
    return {
        "analysis_id": analysis.get("analysis_id"),
        "uploaded_resume_name": analysis.get("uploaded_resume_name"),
        "target_role": analysis.get("target_role"),
        "selected_role": analysis.get("selected_role"),
        "matching_percentage": analysis.get("matching_percentage"),
        "summary": analysis.get("summary"),
        "feedback": analysis.get("feedback"),
        "review_status": analysis.get("review_status"),
        "generated_at": analysis.get("generated_at"),
        "accuracy_tested_at": tested_at,
        "job_source": dict(analysis.get("job_source") or {}),
        "top_role_matches": top_role_matches,
        "job_description_analysis": {
            "missing_keywords": list(job_analysis.get("missing_keywords") or [])[:12],
            "matched_keywords": list(job_analysis.get("matched_keywords") or [])[:12],
        },
        "role_recommendation": {
            "selected_role": role_recommendation.get("selected_role"),
            "selected_database_role": role_recommendation.get("selected_database_role"),
            "detected_database_role": role_recommendation.get("detected_database_role"),
            "recommended_role": role_recommendation.get("recommended_role"),
            "recommended_database_role": role_recommendation.get("recommended_database_role"),
            "selected_score": role_recommendation.get("selected_score"),
            "detected_score": role_recommendation.get("detected_score"),
            "recommended_score": role_recommendation.get("recommended_score"),
            "match_status": role_recommendation.get("match_status"),
            "human_review_required": role_recommendation.get("human_review_required"),
            "review_threshold": role_recommendation.get("review_threshold"),
            "explicit_non_it_headline": role_recommendation.get("explicit_non_it_headline"),
            "non_it_headline": role_recommendation.get("non_it_headline"),
            "fallback_sample_role": role_recommendation.get("fallback_sample_role"),
            "closest_role": role_recommendation.get("closest_role"),
            "closest_role_score": role_recommendation.get("closest_role_score"),
            "top_role_matches": top_role_matches,
            "recommended_missing_keywords": list(role_recommendation.get("recommended_missing_keywords") or [])[:12],
            "reason_summary": role_recommendation.get("reason_summary"),
        },
        "queued_at": analysis.get("queued_at") or datetime.now().astimezone().isoformat(),
    }


def queue_accuracy_testing_analysis(analysis: dict[str, Any]) -> None:
    pending_path = os.path.abspath(ACCURACY_TESTING_PENDING_FILE)
    os.makedirs(os.path.dirname(pending_path), exist_ok=True)
    with open(pending_path, "a", encoding="utf-8") as pending_file:
        pending_file.write(json.dumps(compact_accuracy_testing_analysis(analysis)) + "\n")


def load_pending_accuracy_testing_analyses() -> list[dict[str, Any]]:
    pending_path = os.path.abspath(ACCURACY_TESTING_PENDING_FILE)
    if not os.path.exists(pending_path):
        return []

    pending_items: list[dict[str, Any]] = []
    with open(pending_path, "r", encoding="utf-8") as pending_file:
        for line in pending_file:
            line = line.strip()
            if not line:
                continue
            try:
                pending_items.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return pending_items


def clear_pending_accuracy_testing_analyses() -> None:
    pending_path = os.path.abspath(ACCURACY_TESTING_PENDING_FILE)
    if os.path.exists(pending_path):
        os.remove(pending_path)


ACCURACY_TESTING_HISTORY_SHEET = "Test Run History"
ACCURACY_TESTING_HISTORY_TABLE = "TestRunHistoryTable"
ACCURACY_TESTING_HISTORY_HEADERS = (
    "Run #",
    "Tested At",
    "Test ID",
    "Test Mode",
    "Source File",
    "Selected Role",
    "Expected Role",
    "Evidence Basis",
    "Expected Fit",
    "ATS Score",
    "Recommended Role",
    "System Fit",
    "Result",
    "Recommendation Score",
    "Top 3 Role Matches",
    "Matched Keywords",
    "Missing Keywords",
    "Recommendation Reason",
)
ACCURACY_TESTING_HISTORY_MAX_TEXT = 32700
ACCURACY_TESTING_HISTORY_ILLEGAL_CHARACTERS = re.compile(
    r"[\x00-\x08\x0B\x0C\x0E-\x1F]"
)


def sanitize_accuracy_history_text(value: Any, limit: int = ACCURACY_TESTING_HISTORY_MAX_TEXT) -> str:
    """Return a safe, bounded Excel cell string for test-run history."""
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple, set)):
        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(value)
    else:
        text = str(value)
    text = ACCURACY_TESTING_HISTORY_ILLEGAL_CHARACTERS.sub(" ", text).strip()
    if text.startswith(("=", "+", "-", "@")):
        # Keep externally supplied workbook text from being interpreted as a
        # formula when the history sheet is opened in Excel.
        text = "'" + text
    return text[: max(0, min(int(limit), ACCURACY_TESTING_HISTORY_MAX_TEXT))]


def get_accuracy_history_keywords(analysis: dict[str, Any], key: str) -> str:
    job_analysis = analysis.get("job_description_analysis") or {}
    role_recommendation = analysis.get("role_recommendation") or {}
    values = list(job_analysis.get(key) or [])
    if not values and key == "matched_keywords":
        values = list(role_recommendation.get("matched_resume_signals") or [])
    if not values and key == "missing_keywords":
        values = list(role_recommendation.get("recommended_missing_keywords") or [])

    unique_values: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = sanitize_accuracy_history_text(value, 160)
        comparison_key = normalize_match_text(cleaned)
        if not cleaned or comparison_key in seen:
            continue
        seen.add(comparison_key)
        unique_values.append(cleaned)
        if len(unique_values) >= 20:
            break
    return sanitize_accuracy_history_text(", ".join(unique_values), 3000)


def format_accuracy_history_top_matches(analysis: dict[str, Any]) -> str:
    role_recommendation = analysis.get("role_recommendation") or {}
    matches = list(
        analysis.get("top_role_matches")
        or role_recommendation.get("top_role_matches")
        or []
    )[:3]
    formatted: list[str] = []
    for position, match in enumerate(matches, start=1):
        if not isinstance(match, dict):
            continue
        raw_role = match.get("role") or match.get("database_role") or ""
        if isinstance(raw_role, dict):
            raw_role = raw_role.get("role_name") or raw_role.get("name") or ""
        role = sanitize_accuracy_history_text(raw_role, 180)
        if not role:
            continue

        fit_result = match.get("fit_result") or {}
        raw_score = match.get("score")
        if raw_score is None:
            raw_score = match.get("confidence_score")
        if raw_score is None and isinstance(fit_result, dict):
            raw_score = fit_result.get("combined_score")
        try:
            numeric_score = float(raw_score)
        except (TypeError, ValueError):
            numeric_score = math.nan

        rank = match.get("rank") or position
        try:
            rank_label = str(int(rank))
        except (TypeError, ValueError):
            rank_label = str(position)
        if math.isfinite(numeric_score):
            formatted.append(f"{rank_label}. {role} ({numeric_score:.2f})")
        else:
            formatted.append(f"{rank_label}. {role}")
    return sanitize_accuracy_history_text("; ".join(formatted), 4000)


def get_accuracy_history_recommendation_score(analysis: dict[str, Any]) -> float | None:
    role_recommendation = analysis.get("role_recommendation") or {}
    job_source = analysis.get("job_source") or {}
    raw_score = role_recommendation.get("recommended_score")
    if raw_score is None:
        raw_score = job_source.get("confidence_score")
    try:
        score = float(raw_score)
    except (TypeError, ValueError):
        return None
    return round(score, 2) if math.isfinite(score) else None


def get_accuracy_history_timestamp(analysis: dict[str, Any]) -> datetime:
    """Return the original test time as an Excel-safe local naive datetime."""
    raw_timestamp = (
        analysis.get("accuracy_tested_at")
        or analysis.get("queued_at")
        or analysis.get("generated_at")
    )
    if isinstance(raw_timestamp, datetime):
        tested_at = raw_timestamp
    else:
        timestamp_text = str(raw_timestamp or "").strip()
        if timestamp_text.endswith("Z"):
            timestamp_text = timestamp_text[:-1] + "+00:00"
        try:
            tested_at = datetime.fromisoformat(timestamp_text) if timestamp_text else datetime.now()
        except ValueError:
            tested_at = datetime.now()

    if tested_at.tzinfo is not None:
        local_timezone = datetime.now().astimezone().tzinfo
        tested_at = tested_at.astimezone(local_timezone).replace(tzinfo=None)
    return tested_at


def get_accuracy_system_fit(
    selected_role: Any,
    recommended_role: Any,
    role_recommendation: dict[str, Any] | None = None,
) -> str:
    """Return the system's broad selected-role fit without supplying ground truth."""
    match_status = normalize_match_text(
        str((role_recommendation or {}).get("match_status") or "")
    )
    if match_status in {"review required", "no suitable role"}:
        return "NOT MATCH"

    selected_family = get_role_family(normalize_text(str(selected_role or "")))
    recommended_family = get_role_family(normalize_text(str(recommended_role or "")))
    return (
        "MATCH"
        if selected_family and recommended_family and selected_family == recommended_family
        else "NOT MATCH"
    )


def initialize_accuracy_testing_history_sheet(sheet) -> None:
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side  # type: ignore

    header_fill = PatternFill("solid", fgColor="1F4E78")
    header_font = Font(name="Arial", size=10, bold=True, color="FFFFFF")
    header_alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    thin_border = Border(bottom=Side(style="thin", color="A6A6A6"))

    for column, header in enumerate(ACCURACY_TESTING_HISTORY_HEADERS, start=1):
        cell = sheet.cell(row=1, column=column)
        cell.value = header
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = header_alignment
        cell.border = thin_border

    widths = (9, 20, 14, 16, 38, 28, 28, 48, 14, 12, 30, 14, 12, 21, 55, 45, 45, 72)
    for column, width in enumerate(widths, start=1):
        sheet.column_dimensions[sheet.cell(row=1, column=column).column_letter].width = width
    sheet.row_dimensions[1].height = 32
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = "A1:R1"
    sheet.sheet_view.showGridLines = False
    sheet.sheet_properties.tabColor = "1F4E78"


def ensure_accuracy_testing_history_sheet(workbook) -> tuple[Any | None, str]:
    created = False
    if ACCURACY_TESTING_HISTORY_SHEET in workbook.sheetnames:
        sheet = workbook[ACCURACY_TESTING_HISTORY_SHEET]
    else:
        try:
            sheet = workbook.create_sheet(ACCURACY_TESTING_HISTORY_SHEET)
        except (AttributeError, TypeError, ValueError) as error:
            return None, f"Could not create the Test Run History sheet: {error}"
        created = True

    existing_headers = [
        normalize_text(str(sheet.cell(row=1, column=column).value or ""))
        for column in range(1, len(ACCURACY_TESTING_HISTORY_HEADERS) + 1)
    ]
    if created or not any(existing_headers):
        initialize_accuracy_testing_history_sheet(sheet)
    elif existing_headers != list(ACCURACY_TESTING_HISTORY_HEADERS):
        return None, (
            "Test Run History headers do not match the required A:R history layout. "
            "Restore the history headers before running another accuracy test."
        )
    for other_sheet in getattr(workbook, "worksheets", []):
        if other_sheet is sheet:
            continue
        if ACCURACY_TESTING_HISTORY_TABLE in other_sheet.tables:
            return None, (
                f"Excel table name {ACCURACY_TESTING_HISTORY_TABLE} is already used "
                "on another worksheet."
            )
    return sheet, ""


def get_next_accuracy_history_position(sheet) -> tuple[int, int]:
    """Return the next run number and row without relying on formatted rows."""
    maximum_run_number = 0
    last_run_row = 1
    for row in range(2, int(getattr(sheet, "max_row", 1) or 1) + 1):
        value = sheet.cell(row=row, column=1).value
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        last_run_row = row
        if isinstance(value, bool):
            continue
        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(numeric_value):
            maximum_run_number = max(maximum_run_number, int(numeric_value))
    return maximum_run_number + 1, last_run_row + 1


def style_accuracy_testing_history_row(sheet, row: int) -> None:
    from openpyxl.styles import Alignment, Font, PatternFill  # type: ignore

    for column in range(1, len(ACCURACY_TESTING_HISTORY_HEADERS) + 1):
        cell = sheet.cell(row=row, column=column)
        cell.font = Font(name="Arial", size=10, color="1F1F1F")
        cell.alignment = Alignment(
            horizontal="center" if column in {1, 2, 3, 4, 9, 10, 12, 13, 14} else "left",
            vertical="top",
            wrap_text=column in {5, 6, 7, 8, 11, 15, 16, 17, 18},
        )
    sheet.cell(row=row, column=2).number_format = "yyyy-mm-dd hh:mm:ss"
    sheet.cell(row=row, column=10).number_format = "0.00"
    sheet.cell(row=row, column=14).number_format = "0.00"
    result_cell = sheet.cell(row=row, column=13)
    result_style = {
        "PASS": ("2E7D32", "E2F0D9"),
        "REVIEW": ("C62828", "FCE8E6"),
        "UNREVIEWED": ("9C6500", "FFF2CC"),
    }.get(str(result_cell.value or "").upper(), ("1F1F1F", "FFFFFF"))
    result_cell.font = Font(
        name="Arial",
        size=10,
        bold=True,
        color=result_style[0],
    )
    result_cell.fill = PatternFill(
        "solid",
        fgColor=result_style[1],
    )
    sheet.row_dimensions[row].height = 45


def extend_accuracy_testing_history_table(workbook, sheet, last_row: int) -> str:
    from openpyxl.worksheet.table import Table, TableStyleInfo  # type: ignore

    table = sheet.tables.get(ACCURACY_TESTING_HISTORY_TABLE)
    if table is None:
        for other_sheet in workbook.worksheets:
            if other_sheet is sheet:
                continue
            if ACCURACY_TESTING_HISTORY_TABLE in other_sheet.tables:
                return (
                    f"Excel table name {ACCURACY_TESTING_HISTORY_TABLE} is already used "
                    "on another worksheet."
                )
        table = Table(
            displayName=ACCURACY_TESTING_HISTORY_TABLE,
            ref=f"A1:R{last_row}",
        )
        table.tableStyleInfo = TableStyleInfo(
            name="TableStyleMedium2",
            showFirstColumn=False,
            showLastColumn=False,
            showRowStripes=True,
            showColumnStripes=False,
        )
        sheet.add_table(table)
    else:
        table.ref = f"A1:R{last_row}"
    sheet.auto_filter.ref = f"A1:R{last_row}"
    return ""


def append_accuracy_testing_history_values(
    workbook,
    analysis: dict[str, Any],
    *,
    test_id: Any,
    test_mode: str,
    source_file: Any,
    selected_role: Any,
    expected_role: Any,
    evidence: Any,
    expected_fit: str,
    score: float,
    recommended_role: str,
    system_fit: str,
    result: str,
) -> tuple[int | None, str]:
    """Append one immutable history record from prepared or unreviewed data."""
    history_sheet, warning = ensure_accuracy_testing_history_sheet(workbook)
    if warning or history_sheet is None:
        return None, warning

    role_recommendation = analysis.get("role_recommendation") or {}
    recommendation_reason = (
        role_recommendation.get("reason_summary")
        or analysis.get("summary")
        or analysis.get("feedback")
        or ""
    )
    run_number, history_row = get_next_accuracy_history_position(history_sheet)
    values: tuple[Any, ...] = (
        run_number,
        get_accuracy_history_timestamp(analysis),
        sanitize_accuracy_history_text(test_id, 120),
        sanitize_accuracy_history_text(test_mode, 40),
        sanitize_accuracy_history_text(source_file, 500),
        sanitize_accuracy_history_text(selected_role, 300),
        sanitize_accuracy_history_text(expected_role, 300),
        sanitize_accuracy_history_text(evidence, 5000),
        sanitize_accuracy_history_text(expected_fit, 40),
        score,
        sanitize_accuracy_history_text(recommended_role, 300),
        sanitize_accuracy_history_text(system_fit, 40),
        sanitize_accuracy_history_text(result, 40),
        get_accuracy_history_recommendation_score(analysis),
        format_accuracy_history_top_matches(analysis),
        get_accuracy_history_keywords(analysis, "matched_keywords"),
        get_accuracy_history_keywords(analysis, "missing_keywords"),
        sanitize_accuracy_history_text(recommendation_reason, 8000),
    )
    for column, value in enumerate(values, start=1):
        history_sheet.cell(row=history_row, column=column).value = value
    style_accuracy_testing_history_row(history_sheet, history_row)
    warning = extend_accuracy_testing_history_table(workbook, history_sheet, history_row)
    if warning:
        return None, warning
    return history_row, ""


def append_accuracy_testing_history_row(
    workbook,
    prepared_sheet,
    schema: dict[str, Any],
    prepared_row: int,
    analysis: dict[str, Any],
    score: float,
    recommended_role: str,
    system_fit: str,
) -> tuple[int | None, str]:
    columns = schema["columns"]
    mode = normalize_accuracy_testing_mode(
        prepared_sheet.cell(row=prepared_row, column=columns["test_mode"]).value
    )
    test_mode = "Selected role" if mode == "selected_role" else "Auto-detect"
    test_id = prepared_sheet.cell(row=prepared_row, column=columns["test_id"]).value
    source_file = prepared_sheet.cell(row=prepared_row, column=columns["source_file"]).value
    selected_role = prepared_sheet.cell(row=prepared_row, column=columns["selected_role"]).value
    expected_role = prepared_sheet.cell(row=prepared_row, column=columns["expected_role"]).value
    evidence = prepared_sheet.cell(row=prepared_row, column=columns["evidence"]).value
    expected_fit = normalize_text(
        str(prepared_sheet.cell(row=prepared_row, column=columns["expected_fit"]).value or "")
    ).upper()

    if mode == "selected_role":
        result = "PASS" if expected_fit == system_fit else "REVIEW"
    else:
        expected_key = normalize_text(str(expected_role or "")).casefold()
        recommended_key = normalize_text(str(recommended_role or "")).casefold()
        result = "PASS" if expected_key and expected_key == recommended_key else "REVIEW"

    return append_accuracy_testing_history_values(
        workbook,
        analysis,
        test_id=test_id,
        test_mode=test_mode,
        source_file=source_file,
        selected_role=selected_role,
        expected_role=expected_role,
        evidence=evidence,
        expected_fit=expected_fit,
        score=score,
        recommended_role=recommended_role,
        system_fit=system_fit,
        result=result,
    )


def get_unregistered_accuracy_test_id(analysis: dict[str, Any]) -> str:
    raw_analysis_id = analysis.get("analysis_id")
    if isinstance(raw_analysis_id, bool):
        return ""
    try:
        analysis_id = int(raw_analysis_id)
    except (TypeError, ValueError):
        return ""
    return f"AN-{analysis_id}" if analysis_id > 0 else ""


def append_unregistered_accuracy_testing_history_row(
    workbook,
    analysis: dict[str, Any],
    score: float,
    recommended_role: str,
) -> tuple[int | None, str]:
    selected_role = normalize_text(str(analysis.get("selected_role") or ""))
    mode = get_accuracy_analysis_mode(analysis)
    system_fit = (
        get_accuracy_system_fit(
            selected_role,
            recommended_role,
            analysis.get("role_recommendation") or {},
        )
        if mode == "selected_role"
        else ""
    )
    return append_accuracy_testing_history_values(
        workbook,
        analysis,
        test_id=get_unregistered_accuracy_test_id(analysis),
        test_mode="Selected role" if mode == "selected_role" else "Auto-detect",
        source_file=os.path.basename(str(analysis.get("uploaded_resume_name") or "")),
        selected_role=selected_role,
        expected_role="",
        evidence="",
        expected_fit="",
        score=score,
        recommended_role=recommended_role,
        system_fit=system_fit,
        result="UNREVIEWED",
    )


def write_accuracy_testing_analysis(
    workbook,
    analysis: dict[str, Any],
) -> tuple[dict[str, Any] | None, str]:
    """Record an analysis and return explicit prepared/history row metadata."""
    sheet, row, warning = find_accuracy_testing_row(workbook, analysis)
    unregistered = warning == ACCURACY_TESTING_UNREGISTERED_WARNING
    if sheet is None:
        return None, warning

    schema = get_accuracy_testing_schema(sheet)
    if schema is None:
        return None, "Accuracy Testing headers were not recognised."
    if warning and not (unregistered and schema["kind"] == "combined"):
        return None, warning

    recommended_role = get_analysis_recommended_role(analysis)
    if not recommended_role:
        return None, "The analysis did not contain a role recommendation."

    raw_score = analysis.get("matching_percentage")
    try:
        score = float(raw_score)
    except (TypeError, ValueError):
        return None, "The analysis did not contain a valid ATS score."
    if not math.isfinite(score):
        return None, "The analysis did not contain a valid ATS score."

    if unregistered:
        history_row, history_warning = append_unregistered_accuracy_testing_history_row(
            workbook,
            analysis,
            score,
            recommended_role,
        )
        if history_warning or history_row is None:
            return None, history_warning
        return {
            "prepared_row": None,
            "history_row": history_row,
            "history_only": True,
            "history_result": "UNREVIEWED",
        }, ""

    if row is None:
        return None, warning or "The prepared accuracy testing row could not be resolved."

    columns = schema["columns"]

    if schema["kind"] == "combined":
        mode = normalize_accuracy_testing_mode(
            sheet.cell(row=row, column=columns["test_mode"]).value
        )
        if mode == "selected_role":
            selected_role = normalize_text(
                str(sheet.cell(row=row, column=columns["selected_role"]).value or "")
            )
            system_fit = get_accuracy_system_fit(
                selected_role,
                recommended_role,
                analysis.get("role_recommendation") or {},
            )
        else:
            # Auto-detect accuracy is calculated by the workbook's exact-role
            # formula in Check (Expected Role versus Recommended Role).
            system_fit = ""

        history_sheet, history_warning = ensure_accuracy_testing_history_sheet(workbook)
        if history_warning or history_sheet is None:
            return None, history_warning

        sheet.cell(row=row, column=columns["ats_score"]).value = score
        sheet.cell(row=row, column=columns["recommended_role"]).value = recommended_role
        sheet.cell(row=row, column=columns["system_fit"]).value = system_fit or None
        history_row, history_warning = append_accuracy_testing_history_row(
            workbook,
            sheet,
            schema,
            row,
            analysis,
            score,
            recommended_role,
            system_fit,
        )
        if history_warning or history_row is None:
            return None, history_warning
        history_result = workbook[ACCURACY_TESTING_HISTORY_SHEET].cell(
            row=history_row,
            column=13,
        ).value
        return {
            "prepared_row": row,
            "history_row": history_row,
            "history_only": False,
            "history_result": history_result,
        }, ""

    if schema["kind"] == "simplified":
        target_role = normalize_text(
            str(sheet.cell(row=row, column=columns["target_role"]).value or "")
        )
        recommended_family = get_role_family(recommended_role)
        target_family = get_role_family(target_role)
        system_answer = (
            "MATCH"
            if recommended_family and target_family and recommended_family == target_family
            else "NOT MATCH"
        )

        role_recommendation = analysis.get("role_recommendation") or {}
        reason = normalize_text(
            role_recommendation.get("reason_summary")
            or analysis.get("summary")
            or analysis.get("feedback")
            or ""
        )
        keyword_note = build_accuracy_missing_keywords_note(analysis)
        feedback_parts = [f"Recommended role: {recommended_role}"]
        if reason:
            feedback_parts.append(reason)
        if keyword_note:
            feedback_parts.append(keyword_note)

        sheet.cell(row=row, column=columns["ats_score"]).value = score
        sheet.cell(row=row, column=columns["ats_feedback"]).value = "\n".join(feedback_parts)[:1000]
        sheet.cell(row=row, column=columns["system_answer"]).value = system_answer
        return {
            "prepared_row": row,
            "history_row": None,
            "history_only": False,
            "history_result": None,
        }, ""

    sheet.cell(row=row, column=columns["recommended_role"]).value = recommended_role
    sheet.cell(row=row, column=columns["ats_score"]).value = score

    sheet.cell(row=row, column=columns["keyword_notes"]).value = build_accuracy_missing_keywords_note(analysis)
    role_recommendation = analysis.get("role_recommendation") or {}
    sheet.cell(row=row, column=columns["explanation_notes"]).value = normalize_text(
        role_recommendation.get("reason_summary")
        or analysis.get("summary")
        or analysis.get("feedback")
        or ""
    )[:500]

    return {
        "prepared_row": row,
        "history_row": None,
        "history_only": False,
        "history_result": None,
    }, ""


def write_accuracy_testing_analysis_row(workbook, analysis: dict[str, Any]) -> tuple[int | None, str]:
    """Compatibility wrapper returning only the prepared Accuracy Testing row."""
    result, warning = write_accuracy_testing_analysis(workbook, analysis)
    if warning or result is None:
        return None, warning
    return result.get("prepared_row"), ""


def update_accuracy_testing_workbook(analysis: dict[str, Any]) -> dict[str, Any]:
    if is_production_environment():
        return {
            "accuracy_testing_saved": False,
            "accuracy_testing_warning": "Accuracy testing auto export is disabled in production.",
        }

    if not get_accuracy_testing_auto_export_enabled():
        return {"accuracy_testing_saved": False, "accuracy_testing_warning": "Accuracy testing auto export is disabled."}

    workbook_path = os.path.abspath(ACCURACY_TESTING_WORKBOOK)
    if not os.path.exists(workbook_path):
        return {
            "accuracy_testing_attempted": True,
            "accuracy_testing_saved": False,
            "accuracy_testing_warning": f"Accuracy testing workbook was not found: {workbook_path}",
        }

    try:
        from openpyxl import load_workbook  # type: ignore
    except ImportError:
        return {
            "accuracy_testing_attempted": True,
            "accuracy_testing_saved": False,
            "accuracy_testing_warning": "Install openpyxl to auto-update the Excel testing workbook.",
        }

    workbook = None
    current_result: dict[str, Any] | None = None
    try:
        workbook = load_workbook(workbook_path)
        pending_items = load_pending_accuracy_testing_analyses()
        saved_rows: list[int] = []
        saved_history_rows: list[int] = []
        for pending_analysis in pending_items:
            pending_result, warning = write_accuracy_testing_analysis(
                workbook,
                pending_analysis,
            )
            if warning or pending_result is None:
                workbook.close()
                return {
                    "accuracy_testing_attempted": True,
                    "accuracy_testing_saved": False,
                    "accuracy_testing_warning": warning
                    or "A queued accuracy testing result could not be recorded.",
                }
            pending_row = pending_result.get("prepared_row")
            pending_history_row = pending_result.get("history_row")
            if isinstance(pending_row, int):
                saved_rows.append(pending_row)
            if isinstance(pending_history_row, int):
                saved_history_rows.append(pending_history_row)

        current_result, warning = write_accuracy_testing_analysis(workbook, analysis)
        if warning or current_result is None:
            workbook.close()
            return {
                "accuracy_testing_attempted": True,
                "accuracy_testing_saved": False,
                "accuracy_testing_warning": warning
                or "The accuracy testing result could not be recorded.",
            }
        row = current_result.get("prepared_row")
        history_row = current_result.get("history_row")
        if isinstance(row, int):
            saved_rows.append(row)
        if isinstance(history_row, int):
            saved_history_rows.append(history_row)

        try:
            from openpyxl.workbook.properties import CalcProperties  # type: ignore

            if workbook.calculation is None:
                workbook.calculation = CalcProperties()
            workbook.calculation.calcMode = "auto"
            workbook.calculation.fullCalcOnLoad = True
            workbook.calculation.forceFullCalc = True
        except (AttributeError, ImportError):
            pass

        workbook.save(workbook_path)
        workbook.close()
        if pending_items:
            clear_pending_accuracy_testing_analyses()
        return {
            "accuracy_testing_attempted": True,
            "accuracy_testing_saved": True,
            "accuracy_testing_row": row,
            "accuracy_testing_history_row": history_row,
            "accuracy_testing_history_only": bool(current_result.get("history_only")),
            "accuracy_testing_history_result": current_result.get("history_result"),
            "accuracy_testing_saved_rows": saved_rows,
            "accuracy_testing_saved_history_rows": saved_history_rows,
            "accuracy_testing_pending_flushed": len(pending_items),
            "accuracy_testing_workbook": workbook_path,
        }
    except PermissionError:
        if workbook is not None:
            try:
                workbook.close()
            except Exception:
                pass
        try:
            queue_accuracy_testing_analysis(analysis)
        except OSError as error:
            return {
                "accuracy_testing_attempted": True,
                "accuracy_testing_saved": False,
                "accuracy_testing_pending": False,
                "accuracy_testing_warning": (
                    "Excel workbook is open, and the result could not be queued. "
                    f"Close ats_resume_fyp_testing.xlsx and run the test again. Detail: {error}"
                ),
            }
        return {
            "accuracy_testing_attempted": True,
            "accuracy_testing_saved": False,
            "accuracy_testing_pending": True,
            "accuracy_testing_history_only": (
                bool(current_result.get("history_only")) if current_result else None
            ),
            "accuracy_testing_warning": "Excel workbook is open. Result was queued; close ats_resume_fyp_testing.xlsx and run one more analysis to flush pending results.",
        }
    except OSError as error:
        return {
            "accuracy_testing_attempted": True,
            "accuracy_testing_saved": False,
            "accuracy_testing_warning": f"Could not update accuracy testing workbook: {error}",
        }


CHARACTER_SPACED_WORD_PATTERN = re.compile(r"(?<![A-Za-z])(?:[A-Za-z][ \t]){2,}[A-Za-z](?![A-Za-z])")
CHARACTER_SPACED_DIGIT_PATTERN = re.compile(r"(?<!\d)(?:\d[ \t])+\d(?!\d)")
PDF_SYMBOL_ARTIFACT_PATTERN = re.compile(r"[\uf000-\uf8ff\ufffd\u25a0-\u25ff\u2610-\u2612\u2022\u2023\u2043]")
SHORT_SPACED_WORD_REPAIRS = (
    ("a m", "am"),
    ("a n", "an"),
    ("a s", "as"),
    ("a t", "at"),
    ("b e", "be"),
    ("b y", "by"),
    ("i f", "if"),
    ("i n", "in"),
    ("i s", "is"),
    ("i t", "it"),
    ("m y", "my"),
    ("o f", "of"),
    ("o n", "on"),
    ("o r", "or"),
    ("q a", "QA"),
    ("t o", "to"),
)


def sanitize_resume_text_artifacts(value: str) -> str:
    cleaned = str(value or "")
    cleaned = cleaned.translate({
        0x2018: ord("'"),
        0x2019: ord("'"),
        0x201C: ord('"'),
        0x201D: ord('"'),
        0x2013: ord("-"),
        0x2014: ord("-"),
    })
    ligature_replacements = {
        "\ufb00": "ff",
        "\ufb01": "fi",
        "\ufb02": "fl",
        "\ufb03": "ffi",
        "\ufb04": "ffl",
    }
    for source, replacement in ligature_replacements.items():
        cleaned = cleaned.replace(source, replacement)
    cleaned = PDF_SYMBOL_ARTIFACT_PATTERN.sub("\n", cleaned)
    cleaned = re.sub(r"\b(?:cid|wingdings|symbol)\s*:\s*\d+\b", " ", cleaned, flags=re.I)
    cleaned = re.sub(r"_{3,}", "\n", cleaned)
    cleaned = re.sub(r"(?<=\d)(?=[A-Za-z])", " ", cleaned)
    cleaned = re.sub(r"(?<=%)(?=[A-Za-z])", " ", cleaned)
    cleaned = re.sub(
        r"(?<=[a-z])(?=(?:Access|Analyst|Contact|Developer|Education|Engineer|Experience|Full|Functional|Other|Present|Previous|Skills|Work)\b)",
        " ",
        cleaned,
    )
    cleaned = re.sub(r"(?<=[a-z])(?=(?:CSS|HTML|JavaScript|Node|PHP|Python|React)\b)", " ", cleaned)
    cleaned = re.sub(
        r"(?<=[a-z])(?=(?:agile|coding|cost|customer|customers|data|enterprise|features|library|major|months|network|retrieve|satisfaction|software|support|testing|traffic|using|website|websites)\b)",
        " ",
        cleaned,
        flags=re.I,
    )
    cleaned = re.sub(r"\.\s*c\s*o\s*m+\b", ".com", cleaned, flags=re.I)
    cleaned = re.sub(r"(?<=[A-Za-z])\s+(?=\d+@)", "", cleaned)
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", cleaned)
    return cleaned


def repair_character_spaced_pdf_text(value: str) -> str:
    """Recover words from PDFs that extract text as `R e s u m e`."""

    def collapse_line(line: str) -> str:
        compact_tokens = [token for token in re.split(r"[ \t]+", line.strip()) if token]
        if (
            3 <= len(compact_tokens) <= 24
            and not re.search(r"[ \t]{2,}", line)
            and all(token.isalpha() and len(token) <= 2 for token in compact_tokens)
            and sum(1 for token in compact_tokens if len(token) == 1) >= (len(compact_tokens) + 1) // 2
        ):
            # Some two-column PDF templates expose headings and wrapped word
            # fragments as `E d u c at i on` or `s e r v i c e s`, without the
            # double spaces that the normal character-spacing repair relies on.
            return "".join(compact_tokens)
        if not CHARACTER_SPACED_WORD_PATTERN.search(line):
            return line
        if not re.search(r"[ \t]{2,}", line):
            return line
        parts = re.split(r"([ \t]{2,})", line)
        repaired_parts: list[str] = []
        for part in parts:
            if re.fullmatch(r"[ \t]{2,}", part or ""):
                repaired_parts.append(" ")
                continue
            repaired_parts.append(
                CHARACTER_SPACED_WORD_PATTERN.sub(
                    lambda match: match.group(0).replace(" ", "").replace("\t", ""),
                    part,
                )
            )
        return "".join(repaired_parts)

    repaired = "\n".join(collapse_line(line) for line in sanitize_resume_text_artifacts(value).splitlines())
    repaired = CHARACTER_SPACED_DIGIT_PATTERN.sub(lambda match: re.sub(r"[ \t]+", "", match.group(0)), repaired)
    for spaced_word, replacement in SHORT_SPACED_WORD_REPAIRS:
        repaired = re.sub(rf"\b{re.escape(spaced_word)}\b", replacement, repaired, flags=re.I)
    repaired = re.sub(r"\bAsa\b", "As a", repaired)
    repaired = re.sub(r"\bso\s+ware\b", "software", repaired, flags=re.I)
    repaired = re.sub(r"(?<!\w)\.\s+NET\b", ".NET", repaired, flags=re.I)
    repaired = re.sub(r"\bC\s*\+\s*\+\b", "C++", repaired, flags=re.I)
    repaired = re.sub(r"\bM\s*4\s+FOOD\b", "M4FOOD", repaired, flags=re.I)
    repaired = re.sub(r"(?<=\w)\s*@\s*(?=\w)", "@", repaired)
    repaired = re.sub(r"(?<=\w)\s*\.\s*(?=(?:com|net|org|edu|io|dev|me)\b)", ".", repaired, flags=re.I)
    repaired = re.sub(r"\s+/\s+", " / ", repaired)
    return repaired


OBVIOUS_JOBSEEKER_TEMPLATE_ADVICE_PATTERNS = (
    re.compile(
        r"^\s*(?:how to|tips?\s+(?:for|on)|guide\s+to)\s+"
        r"(?:find|finding|land|search(?:ing)?\s+for|apply(?:ing)?\s+for)\s+"
        r"(?:a\s+|the\s+|your\s+)?(?:job|jobs|work|employment)\b",
        re.I,
    ),
    re.compile(r"^\s*(?:for\s+more\s+)?(?:job|career)\s+(?:search|hunting)\s+(?:tips?|advice|guide)\b", re.I),
    re.compile(r"^\s*(?:resume|cv|curriculum\s+vitae)\s+(?:writing\s+)?(?:tips?|advice|guide|template\s+instructions?)\b", re.I),
    re.compile(r"^\s*how\s+to\s+(?:write|format|improve|tailor)\s+(?:a\s+|an\s+|your\s+)?(?:resume|cv)\b", re.I),
    re.compile(r"^\s*(?:use|download|edit|customi[sz]e|fill\s+in|complete)\s+(?:this\s+|the\s+)?(?:resume|cv)\s+template\b", re.I),
    re.compile(r"^\s*(?:replace|insert|enter)\s+(?:this\s+|the\s+)?(?:sample\s+|placeholder\s+)?(?:text|content|details?|information)\b", re.I),
    re.compile(r"^\s*(?:click|tap)\s+here\s+to\b", re.I),
)


def is_obvious_jobseeker_template_advice_line(value: str) -> bool:
    """Identify generic template instructions without removing resume evidence."""

    line = normalize_text(value)
    return bool(line) and any(pattern.search(line) for pattern in OBVIOUS_JOBSEEKER_TEMPLATE_ADVICE_PATTERNS)


def clean_resume_source_text(value: str) -> str:
    cleaned = repair_character_spaced_pdf_text(str(value or ""))
    cleaned = sanitize_resume_text_artifacts(cleaned)
    cleaned = re.sub(r"\bM\s*4\s+FOOD\b", "M4FOOD", cleaned, flags=re.I)
    cleaned = re.sub(r"\bA\s+I\b", "AI", cleaned)
    cleaned = re.sub(r"(?<=[A-Za-z])\s+-\s+(?=[A-Za-z])", "-", cleaned)
    cleaned = re.sub(r"\s+:\s*", ": ", cleaned)
    cleaned = re.sub(r"\(\s+", "(", cleaned)
    cleaned = re.sub(r"\s+\)", ")", cleaned)
    cleaned = re.sub(r"\r\n?", "\n", cleaned)
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = "\n".join(
        line
        for line in cleaned.splitlines()
        if not is_obvious_jobseeker_template_advice_line(line)
    )
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def unique_clean_values(values: list[Any], limit: int | None = None) -> list[str]:
    seen: set[str] = set()
    cleaned_values: list[str] = []
    for value in values:
        cleaned = normalize_text(str(value or ""))
        if not cleaned:
            continue
        key = normalize_match_text(cleaned)
        if not key or key in seen:
            continue
        seen.add(key)
        cleaned_values.append(cleaned)
        if limit is not None and len(cleaned_values) >= limit:
            break
    return cleaned_values


def select_resume_template_profile(target_role: str) -> dict[str, str]:
    normalized_role = normalize_match_text(target_role)
    if any(term in normalized_role for term in ("cyber", "security")):
        family = "Cybersecurity"
    elif any(term in normalized_role for term in ("devops", "cloud", "operation", "infrastructure")):
        family = "DevOps / IT Operations"
    elif "quality" in normalized_role or "tester" in normalized_role or "qa" in normalized_role:
        family = "Quality Assurance"
    elif "network" in normalized_role:
        family = "Network Engineering"
    elif "java" in normalized_role:
        family = "Java Developer"
    elif "python" in normalized_role:
        family = "Python Developer"
    elif "full" in normalized_role or "web" in normalized_role or "front" in normalized_role:
        family = "Full Stack / Web Developer"
    else:
        family = "Software Developer"

    return {
        "name": family + " ATS Template",
        "headline": target_role or family,
        "experience_heading": "Professional Experience",
        "project_heading": "Projects",
        "certification_heading": "Certifications",
        "achievement_heading": "Achievements",
        "skills_heading": "Technical Skills",
        "education_heading": "Education",
        "other_heading": "Additional ATS Evidence",
    }


def get_resume_generation_role(analysis: dict[str, Any], requested_role: str = "") -> str:
    role_recommendation = analysis.get("role_recommendation") or {}
    return normalize_text(
        requested_role
        or role_recommendation.get("recommended_role")
        or role_recommendation.get("recommended_database_role")
        or analysis.get("target_role")
        or analysis.get("selected_role")
        or "Target Role"
    )


def infer_resume_name_from_text(text: str) -> str:
    lines = [normalize_text(line) for line in clean_resume_source_text(text).splitlines()]
    ignored_terms = set(
        CORE_RESUME_SECTION_MARKERS
        + CONTACT_MARKERS
        + [
            "academic projects",
            "activities",
            "contact",
            "email",
            "language",
            "mobile",
            "phone",
            "profile",
            "resume",
            "tech stack",
            "technical stack",
            "curriculum vitae",
        ]
    )
    role_terms = {"developer", "engineer", "analyst", "manager", "intern", "student", "assistant", "support"}

    # Multi-column PDFs often expose a name as two adjacent all-capital lines,
    # for example `CHENG` then `QING HONG`. Join those lines before evaluating
    # ordinary one-line candidates, and never treat a section heading as a name.
    uppercase_name_parts: list[str] = []
    for line_index, raw_line in enumerate(lines[:20]):
        normalized = normalize_match_text(raw_line)
        words = re.findall(r"[A-Za-z][A-Za-z'-]*", raw_line)
        is_heading = any(normalized == term or normalized.startswith(term + " ") for term in ignored_terms)
        is_uppercase_name_part = (
            bool(words)
            and len(words) <= 3
            and not is_heading
            and not re.search(r"\d|@|https?://", raw_line, re.I)
            and all(word.upper() == word for word in words)
            and not (set(normalized.split()) & role_terms)
        )
        if is_uppercase_name_part:
            uppercase_name_parts.extend(words)
            if 2 <= len(uppercase_name_parts) <= 5:
                next_index = line_index + 1
                next_line = lines[next_index] if next_index < len(lines) else ""
                next_words = re.findall(r"[A-Za-z][A-Za-z'-]*", next_line)
                if not next_words or not all(word.upper() == word for word in next_words):
                    return " ".join(uppercase_name_parts).title()
        else:
            uppercase_name_parts = []

    for raw_line in lines[:14]:
        line = re.sub(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", "", raw_line)
        line = re.sub(r"(?:\+?\d[\d\s().-]{7,}\d)", "", line)
        line = re.sub(r"^(?:name|candidate)\s*:\s*", "", line, flags=re.I)
        line = re.sub(r"([a-z])([A-Z])", r"\1 \2", line)
        line = re.split(
            r"\b(?:entry level|junior|senior|lead)?\s*(?:software|web|full stack|front end|back end|react|java|python|computer|desktop|support|network|qa|quality|data|cyber|cloud)\b",
            line,
            maxsplit=1,
            flags=re.I,
        )[0]
        line = normalize_text(line.strip(" |,;:-"))
        if not line:
            continue
        normalized = normalize_match_text(line)
        if not normalized or any(normalized == term or normalized.startswith(term + " ") for term in ignored_terms):
            continue
        words = [word for word in re.split(r"\s+", line) if word]
        if 2 <= len(words) <= 5 and not (set(normalized.split()) & role_terms):
            if sum(1 for word in words if word[:1].isupper() or word.isupper()) >= max(1, len(words) - 1):
                return line
    return ""


def infer_resume_name_from_filename(filename: str) -> str:
    stem = os.path.splitext(os.path.basename(str(filename or "")))[0]
    cleaned = re.sub(r"[_-]+", " ", stem)
    cleaned = re.sub(r"\b(?:resume|cv|curriculum|vitae|ats|final|latest|updated)\b", " ", cleaned, flags=re.I)
    cleaned = normalize_text(cleaned.strip(" ,;-"))
    if not cleaned:
        return ""
    words = [word for word in cleaned.split() if not re.fullmatch(r"\d+", word)]
    if 2 <= len(words) <= 5:
        return " ".join(words)
    return ""


def extract_resume_phone(text: str) -> str:
    candidates = re.findall(r"(?:\+?\s*\d[\d\s().-]{7,}\d)", text)
    for candidate in candidates:
        cleaned = re.sub(r"^\+\s*", "+", normalize_text(candidate))
        if re.fullmatch(r"(?:19|20)\d{2}\s*[-/]\s*(?:19|20)\d{2}", cleaned):
            continue
        digits = re.sub(r"\D", "", cleaned)
        if len(digits) < 8:
            continue
        if len(digits) == 8 and re.fullmatch(r"(?:19|20)\d{2}(?:19|20)\d{2}", digits):
            continue
        return cleaned
    return ""


def extract_resume_email(text: str) -> str:
    candidates = re.findall(r"[\w.+-]+@[\w-]+(?:\s*\.\s*[\w-]+)+", text)
    for line in str(text or "").splitlines():
        if "@" not in line:
            continue
        compact_line = re.sub(r"\s+", "", line)
        candidates.extend(re.findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", compact_line))
    emails: list[tuple[str, str, int]] = []
    for index, candidate in enumerate(candidates):
        cleaned = re.sub(r"\s+", "", candidate).strip(".,;:()[]{}<>")
        cleaned = re.sub(r"\.comm\b", ".com", cleaned, flags=re.I)
        if not re.fullmatch(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", cleaned):
            continue
        local_part, domain = cleaned.split("@", 1)
        if len(local_part) < 3:
            continue
        normalized = cleaned.lower()
        emails.append((normalized, normalized, index))

    if not emails:
        return ""

    counts = Counter(email for email, _, _ in emails)
    first_seen: dict[str, int] = {}
    for email, _, index in emails:
        first_seen.setdefault(email, index)

    return max(
        counts,
        key=lambda email: (counts[email], len(email.split("@", 1)[0]), -first_seen[email]),
    )


def extract_resume_contact(
    resume_text: str,
    candidate_name: str = "",
    extra_links: list[str] | None = None,
) -> dict[str, Any]:
    text = clean_resume_source_text(resume_text)
    email = extract_resume_email(text)
    phone = extract_resume_phone(text)
    link_scan_text = text
    compact_link_lines = [
        re.sub(r"\s+", "", line)
        for line in text.splitlines()
        if re.search(r"h\s*t\s*t\s*p|linkedin|github", line, re.I)
    ]
    if compact_link_lines:
        link_scan_text += "\n" + "\n".join(compact_link_lines)
    extracted_links = re.findall(
        r"(?:https?://)?(?:www\.)?(?:linkedin\.com|github\.com|[\w.-]+\.(?:app|dev|io|me|com)/)[^\s,;)]*",
        link_scan_text,
        re.I,
    )
    links = unique_clean_values(
        [normalize_resume_link(link) for link in list(extra_links or []) + extracted_links],
        5,
    )

    provided_name = normalize_text(candidate_name)
    resume_name = infer_resume_name_from_text(text)
    # The uploaded resume is the source of truth for the candidate identity.
    # The signed-in profile name is only a fallback when the document does not
    # expose a credible name (users may legitimately analyse another resume).
    inferred_name = resume_name or provided_name

    return {
        "name": inferred_name or "Candidate Name",
        "email": email,
        "phone": phone,
        "links": links,
        "link_labels": [get_link_label(link) for link in links],
    }


def clean_resume_sentence(sentence: str) -> str:
    cleaned = sanitize_resume_text_artifacts(sentence)
    cleaned = normalize_text(re.sub(r"^[\-\*\d.)\s]+", "", cleaned or ""))
    cleaned = re.sub(r"\s+[,;:.]", lambda match: match.group(0).strip(), cleaned)
    if len(cleaned) > 220:
        cleaned = cleaned[:217].rstrip(" ,;:.") + "..."
    if cleaned and cleaned[-1] not in ".!?":
        cleaned += "."
    return cleaned


def has_pdf_text_artifacts(sentence: str) -> bool:
    single_letters = [
        token
        for token in re.findall(r"\b[A-Za-z]\b", sentence)
        if token.lower() not in {"a", "i"}
    ]
    if len(single_letters) >= 2:
        return True
    if re.search(r"\b[b-hj-z]\s+[a-z]{2,}\b", sentence):
        return True
    if re.search(r"[a-z][A-Z]\s+[a-z]", sentence):
        return True
    normalized = normalize_match_text(sentence)
    heading_hits = sum(
        1
        for marker in ("about", "experience", "education", "contact", "skill")
        if marker in normalized
    )
    return heading_hits >= 2 and len(sentence) > 110


def polish_resume_evidence_sentence(sentence: str) -> str:
    cleaned = clean_resume_sentence(sentence)
    cleaned = re.sub(r"^(?:Responsibility|Responsibilities)\s*:\s*", "", cleaned, flags=re.I)
    replacements = (
        (r"\bI have been design and deploy for\b", "Designed and deployed"),
        (r"\bI have been design and deploy\b", "Designed and deployed"),
        (r"\bI have been QA for testing\b", "Performed QA testing"),
        (r"\bI have giving the training to the client by how to use\b", "Provided client training on how to use"),
        (r"\bI have giving the training\b", "Provided training"),
        (r"\bI have getting my internship on\b", "Completed internship from"),
        (r"\bI have getting this job half year on\b", "Worked a half-year role from"),
        (r"\bI have been fixes the problem like installing\b", "Fixed setup issues by installing"),
        (r"\bI have knowledge by for creating\b", "Created"),
        (r"\ban webapp\b", "a web app"),
        (r"\bwebapp\b", "web app"),
        (r"\bsql\b", "SQL"),
        (r"\bpower point\b", "PowerPoint"),
        (r"\bfro\b", "for"),
        (r"\bsoftrware\b", "software"),
        (r"\bpastthrough the message to developer to solve\b", "reported to developers for resolution"),
        (r"\bpassed through the message to developer to solve\b", "reported to developers for resolution"),
        (r"\bbug will be record\b", "bugs were recorded"),
        (r"\bhave been done\b", "before release"),
        (r"\bbefore release before deploy for the customer\b", "before customer deployment"),
    )
    for pattern, replacement in replacements:
        cleaned = re.sub(pattern, replacement, cleaned, flags=re.I)
    return clean_resume_sentence(cleaned)


def is_skill_inventory_sentence(sentence: str, sentence_tokens: set[str], action_terms: set[str]) -> bool:
    normalized = normalize_match_text(sentence)
    if sentence_tokens & action_terms:
        return False
    inventory_starts = (
        "additional skills",
        "computer skills",
        "languages",
        "operating systems",
        "programming language",
        "programming languages",
        "relevant courses",
        "software",
        "technical skills",
        "tools",
    )
    if normalized.startswith(inventory_starts):
        return True
    if re.search(r"\b(?:gpa|honors|relevant courses|dean s list)\b", normalized):
        return True
    if sentence.count(",") >= 6:
        return True
    return False


def normalized_phrase_in_text(phrase: str, text: str) -> bool:
    return f" {phrase} " in f" {text} "


def polish_resume_education_sentence(sentence: str) -> str:
    cleaned = clean_resume_sentence(sentence)
    cleaned = re.sub(r"\bTechnolgy\b", "Technology", cleaned, flags=re.I)
    cleaned = re.sub(r"\bCommunicatio n\b", "Communication", cleaned, flags=re.I)
    cleaned = re.sub(r"\bSarawa k\b", "Sarawak", cleaned, flags=re.I)
    cleaned = re.sub(r"\bPolyethnic Mukah\b", "Politeknik Mukah", cleaned, flags=re.I)
    cleaned = re.sub(r"\(\s*((?:19|20)\d{2})\s*-\s*((?:19|20)\d{2})\s*\)", r"(\1 - \2)", cleaned)
    date_match = re.search(r"((?:19|20)\d{2})\s*-\s*((?:19|20)\d{2})", cleaned)
    degree_match = re.search(
        r"\b(Bachelor|Master|PhD|Diploma)\s+(of|in)\s+([A-Za-z &/]{2,80})",
        cleaned,
        re.I,
    )
    if degree_match:
        subject = normalize_text(degree_match.group(3))
        subject = re.split(
            r"\b(?:Bachelor|Master|PhD|Diploma|University|Politeknik|Polytechnic|College|School|Contact)\b",
            subject,
            maxsplit=1,
            flags=re.I,
        )[0].strip(" ,;:.")
        if subject:
            degree = f"{degree_match.group(1).title()} {degree_match.group(2).lower()} {subject}"
            if date_match:
                degree += f" ({date_match.group(1)} - {date_match.group(2)})"
            return clean_resume_sentence(degree)
    if date_match and date_match.end() < len(cleaned):
        trailing = cleaned[date_match.end() :]
        if re.search(r"\b(?:Bachelor|Master|PhD|Diploma|University|Politeknik|Polytechnic|Contact)\b|@|\+?\d", trailing, re.I):
            cleaned = cleaned[: date_match.end()].rstrip(" ,;:.") + "."
    return clean_resume_sentence(cleaned)


def normalize_education_scan_text(resume_text: str) -> str:
    text = normalize_text(clean_resume_source_text(resume_text))
    replacements = (
        (r"\bTechnolgy\b", "Technology"),
        (r"\bCommunicatio\s*n\b", "Communication"),
        (r"\bCommunicationn\b", "Communication"),
        (r"\bSarawa\s*k\b", "Sarawak"),
        (r"\bPolyethnic Mukah\b", "Politeknik Mukah"),
        (r"\bU niversity\b", "University"),
        (r"\bP\s+oliteknik\b", "Politeknik"),
        (r"\bM\s+ukah\b", "Mukah"),
        (r"\bD\s+iploma\b", "Diploma"),
    )
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text, flags=re.I)
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def find_education_institution(context: str) -> str:
    known_institutions = (
        ("University of Technology Sarawak", r"University\s+of\s+Technology\s+Sarawa\s*k"),
        ("Politeknik Mukah Sarawak", r"Politeknik\s+Mukah\s+Sarawa\s*k"),
        ("Politeknik Mukah", r"Politeknik\s+Mukah"),
        ("Polytechnic Mukah Sarawak", r"Polytechnic\s+Mukah\s+Sarawa\s*k"),
    )
    for label, pattern in known_institutions:
        if re.search(pattern, context, re.I):
            return label

    generic_matches = re.findall(
        r"\b(?:University|Politeknik|Polytechnic|College|Institute|School)\s+(?:of\s+)?[A-Za-z]+(?:\s+[A-Za-z]+){0,5}",
        context,
        re.I,
    )
    if not generic_matches:
        return ""

    institution = normalize_text(generic_matches[-1])
    institution = re.split(
        r"\b(?:Bachelor|Master|PhD|Diploma|Undergraduate|Contact|Email|Phone)\b",
        institution,
        maxsplit=1,
        flags=re.I,
    )[0].strip(" ,;:.")
    return " ".join(word.upper() if len(word) <= 3 and word.isupper() else word.capitalize() for word in institution.split())


def clean_education_subject(value: str) -> str:
    subject = normalize_text(value)
    subject = re.sub(r"\bD\s+iploma\b", "Diploma", subject, flags=re.I)
    subject = re.sub(r"\bCommunicatio\s*n\b", "Communication", subject, flags=re.I)
    subject = re.split(
        r"\b(?:Bachelor|Master|PhD|Diploma|University|Politeknik|Polytechnic|College|School|Contact|Responsibility|I have)\b",
        subject,
        maxsplit=1,
        flags=re.I,
    )[0]
    subject = subject.strip(" ,;:.()")
    subject = re.sub(r"\bComputer science\b", "Computer Science", subject, flags=re.I)
    subject = re.sub(r"\bTechnology\b", "Technology", subject, flags=re.I)
    return normalize_text(subject)


def extract_structured_education_lines(resume_text: str) -> list[str]:
    scan_text = normalize_education_scan_text(resume_text)
    degree_pattern = re.compile(
        r"\b(Bachelor|Master|PhD|Diploma)\s+(of|in)\s+([A-Za-z &/]{3,90}?)(?:\s*\(\s*((?:19|20)\d{2})\s*-\s*((?:19|20)\d{2}|Present)\s*\)|(?=\s+(?:Bachelor|Master|PhD|Diploma|University|Politeknik|Polytechnic|College|School|Contact|Responsibility|I have|$)))",
        re.I,
    )
    structured: list[tuple[int, str]] = []
    seen: set[tuple[str, str, str]] = set()

    for match in degree_pattern.finditer(scan_text):
        degree_type = match.group(1).title()
        connector = match.group(2).lower()
        subject = clean_education_subject(match.group(3))
        if not subject:
            continue
        start_year = match.group(4) or ""
        end_year = match.group(5) or ""
        if not start_year or not end_year:
            nearby = scan_text[match.end() : match.end() + 260]
            year_match = re.search(r"((?:19|20)\d{2})\s*-\s*((?:19|20)\d{2}|Present)", nearby, re.I)
            if year_match:
                start_year, end_year = year_match.group(1), year_match.group(2)

        before_context = scan_text[max(0, match.start() - 280) : match.start()]
        after_context = scan_text[match.end() : match.end() + 260]
        institution = find_education_institution(after_context) or find_education_institution(before_context)
        signature = (degree_type.lower(), normalize_match_text(subject), f"{start_year}-{end_year}")
        if signature in seen:
            continue
        seen.add(signature)

        line = f"{degree_type} {connector} {subject}"
        if institution:
            line += f" - {institution}"
        if start_year and end_year:
            line += f" ({start_year} - {end_year})"
        sort_year = int(start_year) if start_year.isdigit() else 0
        structured.append((sort_year, clean_resume_sentence(line)))

    stpm_match = re.search(r"\bSijil\s+Tinggi\s+Persekolahan\s+Malaysia\s*\(\s*STPM\s*\)", scan_text, re.I)
    if stpm_match:
        source_lines = [normalize_text(line) for line in clean_resume_source_text(resume_text).splitlines()]
        school = next((line for line in source_lines if re.match(r"^SMK\b", line, re.I)), "")
        preceding = scan_text[max(0, stpm_match.start() - 220) : stpm_match.start()]
        date_matches = re.findall(r"((?:19|20)\d{2})\s*-\s*((?:19|20)\d{2}|Present)", preceding, re.I)
        start_year, end_year = date_matches[-1] if date_matches else ("", "")
        line = "Sijil Tinggi Persekolahan Malaysia (STPM)"
        if school:
            line += " - " + school
        if start_year and end_year:
            line += f" ({start_year} - {end_year})"
        structured.append((int(start_year) if start_year.isdigit() else 0, clean_resume_sentence(line)))

    structured.sort(key=lambda item: item[0], reverse=True)
    return unique_clean_values([line for _, line in structured], 3)


def is_resume_section_heading(line: str) -> bool:
    normalized = normalize_match_text(line)
    if not normalized:
        return False
    headings = set(
        CORE_RESUME_SECTION_MARKERS
        + [
            "academic projects",
            "activities",
            "contact",
            "language",
            "my contact",
            "portfolio",
            "professional summary",
            "profile",
            "tech stack",
            "technical stack",
        ]
    )
    return len(line) <= 60 and any(normalized == heading or normalized.startswith(heading + " ") for heading in headings)


def split_resume_statement_parts(text: str) -> list[str]:
    lines = [normalize_text(line) for line in clean_resume_source_text(text).splitlines()]
    parts: list[str] = []
    buffer = ""
    starts_new_statement = re.compile(
        r"^(?:responsibilit(?:y|ies)|project|achievement|experience|work|internship|employment|bachelor|master|phd|diploma|university|applied|applies|authored|built|configured|contributed|created|designed|designing|developed|developing|ensured|established|fixed|implemented|improved|integrated|integrates|maintained|managed|performed|provided|repaired|resolved|supported|tested|updated|worked)\b",
        re.I,
    )

    def flush_buffer() -> None:
        nonlocal buffer
        if buffer:
            parts.append(buffer)
            buffer = ""

    for line in lines:
        if not line:
            flush_buffer()
            continue
        if re.search(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+|(?:\+?\d[\d\s().-]{7,}\d)", line):
            flush_buffer()
            continue
        if is_resume_section_heading(line):
            flush_buffer()
            continue
        if buffer and starts_new_statement.search(line):
            flush_buffer()
        buffer = (buffer + " " + line).strip() if buffer else line
        if re.search(r"[.!?]$", line) or len(buffer) >= 240:
            flush_buffer()

    flush_buffer()

    split_parts: list[str] = []
    for part in parts:
        split_parts.extend(
            re.split(
                r"(?<=[.!?])\s+|(?=\b(?:Project|Projects|Experience|Work|Education|Skills|Responsibility)\b)",
                part,
            )
        )
    return [normalize_text(part) for part in split_parts if len(normalize_text(part)) > 10]


def extract_resume_evidence_sentences(resume_text: str, keywords: list[str], limit: int = 6) -> list[str]:
    raw_parts = split_resume_statement_parts(resume_text)
    terms = {
        normalized
        for term in keywords
        if (normalized := normalize_match_text(term)) and len(normalized.replace(" ", "")) > 1
    }
    action_terms = {
        "achieved",
        "analyzed",
        "administered",
        "applied",
        "applies",
        "assisted",
        "built",
        "configured",
        "contributed",
        "create",
        "created",
        "debugged",
        "deployed",
        "deploy",
        "designed",
        "designing",
        "developed",
        "developing",
        "ensured",
        "implemented",
        "improved",
        "integrated",
        "integrates",
        "led",
        "managed",
        "monitored",
        "optimized",
        "performed",
        "recorded",
        "resolved",
        "supported",
        "trained",
        "training",
        "tested",
        "troubleshot",
        "updated",
        "worked",
    }
    education_terms = {"bachelor", "master", "phd", "degree", "university", "college", "diploma"}
    scored_sentences: list[tuple[int, str]] = []
    seen: set[str] = set()

    for raw_part in raw_parts:
        sentence = polish_resume_evidence_sentence(raw_part)
        if len(sentence) < 24:
            continue
        if has_pdf_text_artifacts(sentence):
            continue
        if re.search(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", sentence):
            continue
        if re.search(r"(?:\+?\d[\d\s().-]{7,}\d)", sentence):
            continue
        key = normalize_match_text(sentence)
        if not key or key in seen:
            continue
        seen.add(key)

        normalized_sentence = normalize_match_text(sentence)
        sentence_tokens = set(normalized_sentence.split())
        if normalized_sentence.startswith(
            (
                "computer science student",
                "latest cgpa",
                "possesses ",
                "profile ",
                "skilled in ",
                "student currently ",
                "the ability to ",
            )
        ):
            continue
        if is_skill_inventory_sentence(sentence, sentence_tokens, action_terms):
            continue
        if sentence_tokens & education_terms and not (sentence_tokens & action_terms):
            continue
        term_hits = sum(1 for term in terms if term and normalized_phrase_in_text(term, normalized_sentence))
        has_action = bool(sentence_tokens & action_terms)
        has_metric = bool(re.search(r"\d+(?:\.\d+)?%|\d+\+|\b\d{2,}\b|users?|accuracy|reduced|increased|improved", sentence, re.I))
        has_technical_context = any(
            normalized_phrase_in_text(marker, normalized_sentence)
            for marker in ("project", "intern", "system", "application", "app", "chatbot", "platform", "web", "website", "webapp", "software", "database", "network", "networks", "api", "program", "code", "technical")
        )
        if not has_action and re.search(r"\b(?:present|(?:19|20)\d{2}|[01]?\d\s*/\s*(?:19|20)\d{2})\b", sentence, re.I):
            if any(normalized_phrase_in_text(term, normalized_sentence) for term in ("developer", "engineer", "analyst", "assistant", "intern", "support")):
                continue
        if not has_action and len(sentence_tokens) <= 7:
            continue
        if term_hits == 0 and not has_technical_context:
            continue
        if not has_action and term_hits < 2 and not has_technical_context:
            continue
        score = 0
        score += term_hits
        score += 2 if has_action else 0
        score += 2 if has_metric else 0
        score += 1 if has_technical_context else 0
        if score <= 0:
            continue
        scored_sentences.append((score, sentence))

    scored_sentences.sort(key=lambda item: (item[0], len(item[1])), reverse=True)
    return [sentence for _, sentence in scored_sentences[:limit]]


def extract_education_lines(resume_text: str, education_markers: list[str]) -> list[str]:
    structured_lines = extract_structured_education_lines(resume_text)
    if structured_lines:
        return structured_lines

    education_terms = {"bachelor", "master", "phd", "degree", "diploma", "university", "college", "faculty", "school"}
    raw_parts = split_resume_statement_parts(resume_text)
    scored_lines: list[tuple[int, str]] = []
    for raw_part in raw_parts:
        sentence = polish_resume_education_sentence(raw_part)
        normalized_sentence = normalize_match_text(sentence)
        if len(sentence) < 16:
            continue
        if re.search(r"(?:19|20)\d{2}\s*-\s*(?:bachelor|master|phd|diploma|university|politeknik|polytechnic)", sentence, re.I):
            continue
        if any(term in normalized_sentence for term in education_terms):
            score = 0
            score += 4 if re.search(r"\b(?:Bachelor|Master|PhD|Diploma)\b", sentence, re.I) else 0
            score += 3 if re.search(r"(?:19|20)\d{2}\s*-\s*(?:19|20)\d{2}", sentence) else 0
            score += 2 if any(term in normalized_sentence for term in ("university", "college", "politeknik", "polytechnic", "school")) else 0
            score -= 4 if has_pdf_text_artifacts(sentence) else 0
            score -= 3 if re.search(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+|(?:\+?\d[\d\s().-]{7,}\d)", sentence) else 0
            score -= 1 if normalized_sentence.startswith(("firstly", "currently", "i ")) else 0
            scored_lines.append((score, sentence))

    if scored_lines:
        scored_lines.sort(key=lambda item: (item[0], len(item[1])), reverse=True)
        ranked_lines = unique_clean_values([sentence for _, sentence in scored_lines], 5)
        filtered_lines: list[str] = []
        filtered_keys: list[str] = []
        seen_education_signatures: set[tuple[str, str]] = set()
        for line in ranked_lines:
            key = normalize_match_text(line)
            if len(filtered_lines) >= 2 and key.startswith(("currently ", "firstly ", "for my ", "i ")):
                continue
            degree_match = re.search(r"\b(bachelor|master|phd|diploma)\b", line, re.I)
            date_match = re.search(r"((?:19|20)\d{2})\s*-\s*((?:19|20)\d{2})", line)
            signature = (
                degree_match.group(1).lower() if degree_match else "",
                f"{date_match.group(1)}-{date_match.group(2)}" if date_match else "",
            )
            if signature[0] and signature[1] and signature in seen_education_signatures:
                continue
            if any(existing_key.startswith(key) and len(existing_key) > len(key) + 8 for existing_key in filtered_keys):
                continue
            filtered_lines.append(line)
            filtered_keys.append(key)
            if signature[0] and signature[1]:
                seen_education_signatures.add(signature)
            if len(filtered_lines) >= 3:
                break
        return filtered_lines
    if education_markers:
        return ["Education details detected in the original resume. Confirm degree, institution, and graduation date."]
    return []


def is_generated_placeholder(value: str) -> bool:
    return normalize_text(value).startswith("[Add ")


def is_resume_advice_line(value: str) -> bool:
    normalized = normalize_match_text(value)
    if not normalized:
        return False
    advice_starts = (
        "add evidence if you have it",
        "add a projects section only if",
        "add certifications only if",
        "add awards or achievements only if",
        "add those details only if",
        "draft uses extracted resume evidence",
        "if available add more measurable",
        "limited source information",
        "only keep a missing keyword",
        "target keywords to add with evidence",
    )
    return any(normalized.startswith(prefix) for prefix in advice_starts)


def real_resume_items(values: list[Any]) -> list[str]:
    return [
        normalize_text(str(value or ""))
        for value in values
        if normalize_text(str(value or ""))
        and not is_generated_placeholder(str(value or ""))
        and not is_resume_advice_line(str(value or ""))
    ]


def is_project_like_sentence(sentence: str) -> bool:
    normalized = normalize_match_text(sentence)
    project_terms = (
        "app",
        "application",
        "capstone",
        "dashboard",
        "mobile app",
        "platform",
        "portal",
        "project",
        "website",
        "web app",
    )
    return any(normalized_phrase_in_text(term, normalized) for term in project_terms)


def extract_project_title_lines(resume_text: str, limit: int = 6) -> list[str]:
    """Extract explicit project titles without confusing metadata for titles."""

    title_markers = ("app", "application", "chatbot", "platform", "project", "system", "website")
    action_starts = (
        "applied ",
        "applies ",
        "built ",
        "created ",
        "designed ",
        "designing ",
        "developed ",
        "developing ",
        "implemented ",
        "integrated ",
        "integrates ",
    )
    titles: list[str] = []
    for raw_line in clean_resume_source_text(resume_text).splitlines():
        line = normalize_text(raw_line).split("|", 1)[0].strip(" |,-")
        normalized = normalize_match_text(line)
        if not normalized or len(line) > 120 or len(line.split()) > 16:
            continue
        if is_resume_section_heading(line) or normalized.startswith(action_starts):
            continue
        if normalized.startswith(("individual project", "role ", "team assignment", "technologies ")):
            continue
        if "course project" in normalized or not any(normalized_phrase_in_text(marker, normalized) for marker in title_markers):
            continue
        if normalized.startswith("final year project "):
            line = re.sub(r"^Final\s+Year\s+Project\s*:\s*", "Final Year Project: ", line, flags=re.I)
        titles.append(line.rstrip("."))
    return unique_clean_values(titles, limit)


def build_project_entries(resume_text: str, evidence_bullets: list[str], limit: int = 6) -> list[str]:
    """Keep every explicit project and attach the closest truthful source evidence."""

    titles = extract_project_title_lines(resume_text, limit)
    if not titles:
        return unique_clean_values(
            [bullet for bullet in evidence_bullets if is_project_like_sentence(bullet)],
            limit,
        )

    stop_words = {
        "a",
        "ai",
        "and",
        "app",
        "application",
        "based",
        "final",
        "for",
        "information",
        "project",
        "system",
        "the",
        "web",
        "year",
    }

    def project_terms(value: str) -> set[str]:
        normalized = normalize_match_text(value)
        normalized = normalized.replace("website", "web").replace("chat bot", "chatbot")
        return {
            token
            for token in normalized.split()
            if len(token) >= 3 and token not in stop_words
        }

    action_terms = {
        "applied",
        "built",
        "created",
        "designed",
        "developed",
        "implemented",
        "integrated",
        "managed",
        "optimized",
        "tested",
    }
    unused_evidence = list(evidence_bullets)
    normalized_titles = [normalize_match_text(title) for title in titles]
    clean_evidence: list[str] = []
    for bullet in unused_evidence:
        normalized_bullet = normalize_match_text(bullet)
        matching_titles = [title for title in normalized_titles if title and title in normalized_bullet]
        bullet_tokens = set(normalized_bullet.split())
        if matching_titles and not (bullet_tokens & action_terms):
            # A title/technology inventory is metadata, not a project outcome.
            continue
        clean_evidence.append(bullet)
    unused_evidence = clean_evidence
    entries: list[str] = []
    for title in titles:
        title_terms = project_terms(title)
        ranked: list[tuple[int, int, str]] = []
        for index, bullet in enumerate(unused_evidence):
            bullet_terms = project_terms(bullet)
            overlap = len(title_terms & bullet_terms)
            score = overlap * 4
            if "chatbot" in title_terms and any(term in bullet_terms for term in ("chatbot", "conversation", "gemini", "nlp")):
                score += 5
            if "residential" in title_terms and any(term in bullet_terms for term in ("apartment", "database", "houses", "platform")):
                score += 5
            if any(term in title_terms for term in ("donation", "food", "m4food")) and any(term in bullet_terms for term in ("bakeries", "food", "mobile", "surplus")):
                score += 5
            if "land" in title_terms and any(term in bullet_terms for term in ("crop", "environmental", "land", "soil", "suitability")):
                score += 5
            if score > 0:
                ranked.append((score, -index, bullet))
        if ranked:
            _, _, best_bullet = max(ranked)
            unused_evidence.remove(best_bullet)
            title_pattern = re.escape(title).replace(r"\ ", r"\s+")
            best_bullet = re.sub(
                rf"^\s*{title_pattern}\s*(?:[|:\-]\s*)?",
                "",
                best_bullet,
                count=1,
                flags=re.I,
            ) or best_bullet
            best_bullet = re.split(r"\b(?:Technologies|Role)\s*:\s*", best_bullet, maxsplit=1, flags=re.I)[0]
            best_bullet = clean_resume_sentence(best_bullet)
            entries.append(f"{title} - {best_bullet}")
        else:
            entries.append(title + ".")

    return unique_clean_values(entries, limit)


def has_explicit_experience_section(resume_text: str) -> bool:
    experience_headings = {
        "employment",
        "employment history",
        "experience",
        "internship",
        "professional experience",
        "work experience",
        "work history",
    }
    for raw_line in clean_resume_source_text(resume_text).splitlines():
        normalized = normalize_match_text(raw_line)
        if normalized in experience_headings:
            return True
    return False


def has_achievement_signal(sentence: str) -> bool:
    normalized = normalize_match_text(sentence)
    if has_measurable_impact(sentence):
        return True
    return any(
        normalized_phrase_in_text(term, normalized)
        for term in ("award", "achievement", "achieved", "honor", "dean s list", "scholarship")
    )


def split_project_and_experience_bullets(evidence_bullets: list[str]) -> tuple[list[str], list[str]]:
    projects: list[str] = []
    experience: list[str] = []
    for bullet in evidence_bullets:
        if is_project_like_sentence(bullet) and len(projects) < 6:
            projects.append(bullet)
        elif len(experience) < 4:
            experience.append(bullet)

    return unique_clean_values(experience, 4), unique_clean_values(projects, 6)


def extract_certification_lines(resume_text: str, limit: int = 3) -> list[str]:
    known_cert_terms = (
        "aws certified",
        "azure certified",
        "certificate",
        "certification",
        "certified",
        "cisco",
        "ccna",
        "comptia",
        "google professional",
        "microsoft certified",
        "oracle certified",
        "ocp",
        "security+",
    )
    certifications: list[str] = []
    for raw_part in split_resume_statement_parts(resume_text):
        sentence = clean_resume_sentence(raw_part)
        normalized = normalize_match_text(sentence)
        if len(sentence) < 12 or has_pdf_text_artifacts(sentence):
            continue
        if re.search(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+|(?:\+?\d[\d\s().-]{7,}\d)", sentence):
            continue
        if any(term in normalized for term in known_cert_terms):
            certifications.append(sentence)
    return unique_clean_values(certifications, limit)


def extract_achievement_lines(resume_text: str, evidence_bullets: list[str], limit: int = 3) -> list[str]:
    achievements = [bullet for bullet in evidence_bullets if has_achievement_signal(bullet)]
    for raw_part in split_resume_statement_parts(resume_text):
        sentence = clean_resume_sentence(raw_part)
        normalized = normalize_match_text(sentence)
        if len(sentence) < 16 or has_pdf_text_artifacts(sentence):
            continue
        if re.search(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+|(?:\+?\d[\d\s().-]{7,}\d)", sentence):
            continue
        if any(normalized_phrase_in_text(term, normalized) for term in ("award", "achievement", "honor", "dean s list", "scholarship")):
            achievements.append(sentence)
    return unique_clean_values(achievements, limit)


def build_section_completeness(sections: dict[str, Any]) -> dict[str, Any]:
    section_labels = {
        "experience_bullets": "professional experience",
        "projects": "projects",
        "certifications": "certifications",
        "achievements": "achievements",
        "education": "education",
    }
    missing_sections: list[str] = []
    placeholder_counts: dict[str, int] = {}
    for key, label in section_labels.items():
        values = list(sections.get(key) or [])
        count = sum(1 for value in values if is_generated_placeholder(str(value)))
        if count:
            placeholder_counts[key] = count
        if not values or count == len(values):
            missing_sections.append(label)

    return {
        "missing_sections": missing_sections,
        "placeholder_counts": placeholder_counts,
        "has_limited_source_info": bool(missing_sections or placeholder_counts),
    }


def build_limited_info_notes(completeness: dict[str, Any]) -> list[str]:
    missing_sections = completeness.get("missing_sections") or []
    if not missing_sections:
        return []
    readable_missing = ", ".join(missing_sections)
    return [
        "Limited source information: the uploaded resume did not clearly show " + readable_missing + ".",
        "Add those details only if they are true; otherwise leave the section out of the final resume.",
    ]


def build_generated_summary(
    target_role: str,
    skills: list[str],
    matched_signals: list[str],
    project_count: int = 0,
    has_professional_experience: bool = False,
) -> str:
    top_skills = unique_clean_values(skills + matched_signals, 6)
    skill_text = ", ".join(top_skills[:5]) if top_skills else "computer science fundamentals"
    evidence_parts: list[str] = []
    if has_professional_experience:
        evidence_parts.append("professional experience")
    if project_count:
        project_label = "documented project" if project_count == 1 else "documented projects"
        evidence_parts.append(f"{project_count} {project_label}")
    evidence_text = " and ".join(evidence_parts) or "academic and project work"
    return (
        f"Computer Science candidate targeting {target_role} roles with resume evidence in {skill_text}. "
        f"Background includes {evidence_text}, with focus on clear documentation, practical implementation, "
        "and measurable project outcomes."
    )


def format_resume_keyword(value: str) -> str:
    keyword = normalize_text(str(value or ""))
    normalized = normalize_match_text(keyword)
    compact = re.sub(r"[^a-z0-9+#]+", "", keyword.lower())
    direct_labels = {
        ".net": ".NET",
        ".net maui": ".NET MAUI",
        "netmaui": ".NET MAUI",
        "c#": "C#",
        "c++": "C++",
        "gemini api": "Google Gemini API",
        "geminiapi": "Google Gemini API",
        "google gemini api": "Google Gemini API",
        "googlegeminiapi": "Google Gemini API",
        "google maps api": "Google Maps API",
        "googlemapsapi": "Google Maps API",
        "node.js": "Node.js",
        "express.js": "Express.js",
        "restapi": "REST APIs",
        "restapis": "REST APIs",
        "vscode": "Visual Studio Code",
        "vs code": "Visual Studio Code",
    }
    if keyword.lower() in direct_labels:
        return direct_labels[keyword.lower()]
    if compact in direct_labels:
        return direct_labels[compact]
    labels = {
        "android studio": "Android Studio",
        "api": "API Integration",
        "api integration": "API Integration",
        "aws": "AWS",
        "azure": "Azure",
        "c": "C",
        "ci cd": "CI/CD",
        "css": "CSS",
        "debug": "Troubleshooting",
        "debugging": "Troubleshooting",
        "develop": "Software Development",
        "docker": "Docker",
        "express": "Express.js",
        "express js": "Express.js",
        "fastapi": "FastAPI",
        "firebase": "Firebase",
        "flask": "Flask",
        "full stack": "Full-Stack Development",
        "full stack development": "Full-Stack Development",
        "fullstack": "Full-Stack Development",
        "gcp": "Google Cloud",
        "git": "Git",
        "github": "GitHub",
        "html": "HTML",
        "javascript": "JavaScript",
        "jquery": "jQuery",
        "json": "JSON",
        "kubernetes": "Kubernetes",
        "linux": "Linux",
        "machine learning": "Machine Learning",
        "mongodb": "MongoDB",
        "mysql": "MySQL",
        "node": "Node.js",
        "node js": "Node.js",
        "nlp": "NLP",
        "object oriented programming": "OOP",
        "oop": "OOP",
        "oracle": "Oracle",
        "postman": "Postman",
        "postgresql": "PostgreSQL",
        "prompt engineering": "Prompt Engineering",
        "problem solving": "Problem Solving",
        "python": "Python",
        "qa": "QA Testing",
        "qa testing": "QA Testing",
        "react": "React",
        "rest": "REST APIs",
        "rest api": "REST APIs",
        "rest apis": "REST APIs",
        "sdlc": "SDLC",
        "software test": "Software Testing",
        "sql": "SQL",
        "sqlite": "SQLite",
        "spring": "Spring Boot",
        "spring boot": "Spring Boot",
        "supabase": "Supabase",
        "tailwind": "Tailwind CSS",
        "test": "Software Testing",
        "testing": "Software Testing",
        "troubleshoot": "Troubleshooting",
        "troubleshooting": "Troubleshooting",
        "typescript": "TypeScript",
        "ui": "UI",
        "ux": "UX",
        "visual studio code": "Visual Studio Code",
    }
    if normalized in labels:
        return labels[normalized]
    if not keyword:
        return ""
    if keyword.isupper() or any(char in keyword for char in "#+/"):
        return keyword
    return " ".join(word.upper() if word in {"api", "sql", "ui", "ux"} else word.capitalize() for word in keyword.split())


def categorize_resume_skills(skills: list[str]) -> list[dict[str, Any]]:
    group_rules: list[tuple[str, set[str]]] = [
        ("Languages", {"java", "python", "javascript", "typescript", "sql", "c", "c#", "c++", "php", "ruby", "go", "kotlin", "swift", "dart"}),
        ("Frontend / Mobile", {"net maui", "html", "css", "javascript", "typescript", "react", "angular", "vue", "tailwind css", "bootstrap", "ui", "ux"}),
        ("Backend / APIs", {"node js", "express js", "spring boot", "rest apis", "rest api", "api", "api integration", "google gemini api", "flask", "django", "fastapi", "net", "asp net", "laravel", "php"}),
        ("Databases", {"mysql", "supabase", "firebase", "postgresql", "mongodb", "oracle", "sqlite", "sql server", "database administration"}),
        ("AI / Data", {"machine learning", "nlp", "data analysis", "prompt engineering"}),
        ("Tools", {"git", "github", "google maps api", "postman", "android studio", "visual studio code", "docker", "kubernetes", "jenkins", "linux", "aws", "azure", "google cloud", "gcp", "excel", "tableau", "power bi"}),
        ("Concepts", {"oop", "object oriented programming", "full stack development", "fullstack", "software development", "software testing", "qa testing", "troubleshooting", "sdlc", "agile", "scrum", "ci cd", "system design", "problem solving"}),
    ]
    low_value_terms = {
        "analysis",
        "application",
        "critical",
        "design",
        "direct",
        "engineer",
        "information",
        "procedure",
        "process",
        "related",
        "research",
        "software",
        "solving",
        "system",
        "systems",
        "thinking",
    }
    groups = [{"label": label, "items": []} for label, _ in group_rules]
    preferred_order = {
        "Languages": ["Java", "Python", "JavaScript", "SQL", "TypeScript", "C#", "C++", "PHP", "Go", "Ruby", "Kotlin", "Swift", "Dart"],
        "Frontend / Mobile": [".NET MAUI", "HTML", "CSS", "JavaScript", "TypeScript", "React", "Angular", "Vue", "Tailwind CSS", "Bootstrap", "UI", "UX"],
        "Backend / APIs": ["Node.js", "Express.js", "Spring Boot", "API Integration", "Google Gemini API", "REST APIs", "Flask", "Django", "FastAPI", ".NET", "ASP.NET", "Laravel"],
        "Databases": ["MySQL", "Supabase", "SQLite", "Firebase", "PostgreSQL", "MongoDB", "Oracle", "SQL Server"],
        "AI / Data": ["Machine Learning", "NLP", "Data Analysis", "Prompt Engineering"],
        "Tools": ["Git", "GitHub", "Google Maps API", "Postman", "Android Studio", "Visual Studio Code", "Docker", "Kubernetes", "Jenkins", "Linux", "AWS", "Azure", "Google Cloud", "Excel", "Tableau", "Power BI"],
        "Concepts": ["OOP", "Full-Stack Development", "Software Testing", "Troubleshooting", "SDLC", "Agile", "Scrum", "CI/CD", "System Design", "Problem Solving"],
    }

    for skill in unique_clean_values(skills, 36):
        display = format_resume_keyword(skill)
        normalized_display = normalize_match_text(display)
        normalized_skill = normalize_match_text(skill)
        if normalized_skill in low_value_terms and normalized_display in low_value_terms:
            continue
        for index, (_, terms) in enumerate(group_rules):
            if normalized_skill in terms or normalized_display in terms or any(term in normalized_skill for term in terms if len(term) > 4):
                groups[index]["items"].append(display)

    clean_groups = []
    for group in groups:
        items = unique_clean_values(group["items"], 8)
        order_map = {
            normalize_match_text(item): index
            for index, item in enumerate(preferred_order.get(group["label"], []))
        }
        items = [
            item
            for _, item in sorted(
                enumerate(items),
                key=lambda pair: (order_map.get(normalize_match_text(pair[1]), 1000 + pair[0]), pair[0]),
            )
        ]
        if items:
            clean_groups.append({"label": group["label"], "items": items})
    return clean_groups


def extract_source_resume_skills(resume_text: str) -> list[str]:
    """Preserve explicit technologies that the broad analysis vocabulary can miss."""

    text = clean_resume_source_text(resume_text)
    skill_patterns: list[tuple[str, str]] = [
        (".NET MAUI", r"(?<!\w)\.\s*net\s+maui\b"),
        ("C++", r"(?<!\w)c\s*\+\s*\+(?!\w)"),
        ("GitHub", r"\bgithub\b"),
        ("Google Gemini API", r"\bgoogle\s+gemini\s+api\b"),
        ("Google Maps API", r"\bgoogle\s+maps\s+api\b"),
        ("SQLite", r"\bsqlite\b"),
        ("Prompt Engineering", r"\bprompt\s+engineering\b"),
        ("Machine Learning", r"\bmachine\s+learning\b"),
        ("NLP", r"\bnlp\b"),
        ("Data Analysis", r"\bdata\s+analysis\b"),
    ]
    return [label for label, pattern in skill_patterns if re.search(pattern, text, re.I)]


def split_generated_education_item(value: str) -> tuple[str, str, str]:
    """Split a stored education line into ATS-safe display lines."""

    text = normalize_text(value).rstrip(".")
    date_match = re.search(
        r"\s*\(((?:19|20)\d{2}\s*-\s*(?:(?:19|20)\d{2}|Present))\)$",
        text,
        re.I,
    )
    dates = date_match.group(1) if date_match else ""
    if date_match:
        text = text[: date_match.start()].rstrip()
    credential, separator, institution = text.partition(" - ")
    if not separator:
        return text, "", dates
    return credential.strip(), institution.strip(), dates


def build_generated_resume_plain_text(draft: dict[str, Any]) -> str:
    candidate = draft["candidate"]
    sections = draft["sections"]
    template = draft.get("template") or {}
    contact_items = unique_clean_values(
        [candidate.get("email"), candidate.get("phone")] + list(candidate.get("link_labels") or candidate.get("links") or []),
        6,
    )
    lines: list[str] = []
    if draft.get("is_sample_resume") or draft.get("sample_only"):
        sample_warning = normalize_text(str((draft.get("ats_notes") or [""])[0]))
        lines.extend(
            [
                "FICTIONAL EXAMPLE - NOT THE USER'S RESUME - NOT FOR SUBMISSION",
                f"Target role: {draft.get('sample_target_role') or draft.get('target_role') or 'Target Role'}",
                sample_warning,
                "",
            ]
        )
    lines.extend([
        (candidate.get("name") or "Candidate Name").upper(),
        template.get("headline") or draft.get("target_role") or "Target Role",
        " | ".join(contact_items),
        "",
        "PROFESSIONAL SUMMARY",
        sections["professional_summary"],
        "",
        str(template.get("skills_heading") or "Core Skills").upper(),
    ])
    for group in sections.get("skill_groups") or []:
        lines.append(f"{group['label']}: " + ", ".join(group["items"]))

    section_order = [
        ("experience_bullets", str(template.get("experience_heading") or "Project / Experience Highlights")),
        ("projects", str(template.get("project_heading") or "Projects")),
        ("certifications", str(template.get("certification_heading") or "Certifications")),
        ("achievements", str(template.get("achievement_heading") or "Achievements")),
        ("education", str(template.get("education_heading") or "Education")),
    ]
    for section_key, section_heading in section_order:
        items = real_resume_items(list(sections.get(section_key) or []))
        if not items:
            continue
        lines.extend(["", section_heading.upper()])
        if section_key == "education":
            for item in items:
                credential, institution, dates = split_generated_education_item(str(item))
                lines.append(f"- {credential}")
                detail = " | ".join(value for value in (institution, dates) if value)
                if detail:
                    lines.append(f"  {detail}")
        else:
            lines.extend(f"- {line}" for line in items)
    return "\n".join(line for line in lines if line is not None).strip() + "\n"


def build_generated_resume_html(draft: dict[str, Any]) -> str:
    candidate = draft["candidate"]
    sections = draft["sections"]
    template = draft.get("template") or {}
    contact_items = unique_clean_values(
        [candidate.get("email"), candidate.get("phone")] + list(candidate.get("link_labels") or candidate.get("links") or []),
        6,
    )

    def section_html(section_key: str, heading: str) -> str:
        items = real_resume_items(list(sections.get(section_key) or []))
        if not items:
            return ""
        if section_key == "education":
            rendered_items = ""
            for item in items:
                credential, institution, dates = split_generated_education_item(str(item))
                detail = " | ".join(value for value in (institution, dates) if value)
                detail_html = f"<span>{html_escape(detail)}</span>" if detail else ""
                rendered_items += (
                    "<li class=\"education-item\">"
                    f"<strong>{html_escape(credential)}</strong>{detail_html}</li>"
                )
        else:
            rendered_items = "".join(f"<li>{html_escape(str(item))}</li>" for item in items)
        return f"<h2>{html_escape(heading)}</h2><ul>{rendered_items}</ul>"

    skill_group_html = "".join(
        "<div class=\"skill-row\">"
        f"<strong>{html_escape(group['label'])}</strong>"
        f"<span>{html_escape(', '.join(group['items']))}</span>"
        "</div>"
        for group in sections.get("skill_groups") or []
    )
    sample_banner_html = ""
    if draft.get("is_sample_resume") or draft.get("sample_only"):
        sample_target = draft.get("sample_target_role") or draft.get("target_role") or "Target Role"
        sample_banner_html = (
            '<aside class="example-banner" role="note">'
            '<strong>FICTIONAL EXAMPLE - NOT FOR SUBMISSION</strong>'
            f'<span>Target role: {html_escape(str(sample_target))}. This is not the user\'s resume and was not generated from the user\'s experience.</span>'
            "</aside>"
        )

    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        "<title>ATS Resume Draft</title>"
        "<style>"
        "@page{size:letter;margin:.55in}"
        "*{box-sizing:border-box}"
        "body{font-family:Arial,Helvetica,sans-serif;margin:0;background:#fff;color:#111;line-height:1.35;font-size:11pt}"
        ".page{max-width:7.4in;margin:0 auto;background:#fff;padding:.42in .2in}"
        ".header{text-align:left;border-bottom:1px solid #111;padding-bottom:6px;margin-bottom:12px}"
        "h1{margin:0 0 2px;font-size:22pt;color:#111;text-transform:uppercase;font-weight:700;letter-spacing:0}"
        ".role{margin:0 0 3px;font-weight:700;color:#111;font-size:11pt;text-transform:none;letter-spacing:0}"
        ".contact{margin:0;color:#111;font-size:10pt;letter-spacing:0}"
        "h2{font-size:11pt;letter-spacing:0;text-transform:uppercase;color:#111;border-bottom:1px solid #555;padding-bottom:2px;margin:14px 0 7px;font-weight:700}"
        "p{margin:0 0 7px}.skill-row{display:grid;grid-template-columns:1.25in 1fr;gap:10px;margin:3px 0;font-size:10.5pt}"
        ".skill-row strong{color:#111}.skill-row span{color:#111}"
        "ul{margin:0 0 0 18px;padding:0;list-style-position:outside}li{margin:4px 0;font-size:10.5pt;break-inside:avoid}"
        ".education-item strong,.education-item span{display:block}.education-item span{margin-top:1px}"
        ".summary{font-size:10.8pt;color:#111}"
        ".example-banner{border:2px solid #9b1c1c;background:#fff2f2;color:#7f1d1d;padding:9px 11px;margin:0 0 14px;text-align:center}"
        ".example-banner strong,.example-banner span{display:block}.example-banner strong{font-size:12pt;letter-spacing:.04em}.example-banner span{font-size:9.5pt;margin-top:3px}"
        "@media print{.page{max-width:none;margin:0;padding:0}.header{margin-bottom:10px}}"
        "</style></head><body><main class=\"page\">"
        f"{sample_banner_html}"
        "<header class=\"header\">"
        f"<h1>{html_escape(candidate.get('name') or 'Candidate Name')}</h1>"
        f"<p class=\"role\">{html_escape(template.get('headline') or draft.get('target_role') or 'Target Role')}</p>"
        f"<p class=\"contact\">{html_escape(' | '.join(contact_items))}</p>"
        "</header>"
        "<h2>Professional Summary</h2>"
        f"<p class=\"summary\">{html_escape(sections['professional_summary'])}</p>"
        f"<h2>{html_escape(template.get('skills_heading') or 'Core Skills')}</h2>"
        f"{skill_group_html}"
        f"{section_html('experience_bullets', str(template.get('experience_heading') or 'Project / Experience Highlights'))}"
        f"{section_html('projects', str(template.get('project_heading') or 'Projects'))}"
        f"{section_html('certifications', str(template.get('certification_heading') or 'Certifications'))}"
        f"{section_html('achievements', str(template.get('achievement_heading') or 'Achievements'))}"
        f"{section_html('education', str(template.get('education_heading') or 'Education'))}"
        "</main></body></html>"
    )


def build_generated_resume_pdf_buffer(draft: dict[str, Any]) -> BytesIO:
    try:
        from reportlab.lib import colors  # type: ignore
        from reportlab.lib.enums import TA_CENTER, TA_LEFT  # type: ignore
        from reportlab.lib.pagesizes import letter  # type: ignore
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet  # type: ignore
        from reportlab.lib.units import inch  # type: ignore
        from reportlab.platypus import (  # type: ignore
            HRFlowable,
            Paragraph,
            SimpleDocTemplate,
            Table,
            TableStyle,
        )
    except ImportError as error:
        raise RuntimeError("Install reportlab to enable PDF resume export.") from error

    candidate = draft.get("candidate") or {}
    sections = draft.get("sections") or {}
    template = draft.get("template") or {}
    contact_items = unique_clean_values(
        [candidate.get("email"), candidate.get("phone")] + list(candidate.get("link_labels") or candidate.get("links") or []),
        6,
    )

    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=0.62 * inch,
        rightMargin=0.62 * inch,
        topMargin=0.5 * inch,
        bottomMargin=0.5 * inch,
        title="ATS Resume Draft",
    )
    base_styles = getSampleStyleSheet()
    styles = {
        "name": ParagraphStyle(
            "ResumeName",
            parent=base_styles["Title"],
            alignment=TA_LEFT,
            fontName="Helvetica-Bold",
            fontSize=20,
            leading=23,
            textColor=colors.black,
            spaceAfter=2,
        ),
        "role": ParagraphStyle(
            "ResumeRole",
            parent=base_styles["Normal"],
            alignment=TA_LEFT,
            fontName="Helvetica-Bold",
            fontSize=10.5,
            leading=12.5,
            textColor=colors.black,
            spaceAfter=3,
        ),
        "contact": ParagraphStyle(
            "ResumeContact",
            parent=base_styles["Normal"],
            alignment=TA_LEFT,
            fontSize=9,
            leading=11,
            textColor=colors.black,
            spaceAfter=6,
        ),
        "heading": ParagraphStyle(
            "ResumeHeading",
            parent=base_styles["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=10.2,
            leading=12.2,
            textColor=colors.black,
            spaceBefore=8,
            spaceAfter=3,
        ),
        "body": ParagraphStyle(
            "ResumeBody",
            parent=base_styles["Normal"],
            fontSize=9.7,
            leading=12,
            textColor=colors.black,
            spaceAfter=4,
        ),
        "skill_label": ParagraphStyle(
            "SkillLabel",
            parent=base_styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=9,
            leading=11,
            textColor=colors.black,
        ),
        "skill_value": ParagraphStyle(
            "SkillValue",
            parent=base_styles["Normal"],
            fontSize=9,
            leading=11,
            textColor=colors.black,
        ),
        "bullet": ParagraphStyle(
            "ResumeBullet",
            parent=base_styles["Normal"],
            fontSize=9.4,
            leading=11.8,
            leftIndent=0,
            textColor=colors.black,
            spaceAfter=2,
        ),
        "example_banner": ParagraphStyle(
            "ExampleOnlyBanner",
            parent=base_styles["Normal"],
            alignment=TA_CENTER,
            fontName="Helvetica-Bold",
            fontSize=10.5,
            leading=13,
            textColor=colors.HexColor("#7f1d1d"),
            backColor=colors.HexColor("#fff2f2"),
            borderColor=colors.HexColor("#9b1c1c"),
            borderWidth=1.2,
            borderPadding=7,
            spaceAfter=10,
        ),
    }

    def heading(text: str):
        return [
            Paragraph(html_escape(text.upper()), styles["heading"]),
            HRFlowable(width="100%", thickness=0.6, color=colors.HexColor("#555555"), spaceAfter=4),
        ]

    story = []
    if draft.get("is_sample_resume") or draft.get("sample_only"):
        sample_target = draft.get("sample_target_role") or draft.get("target_role") or "Target Role"
        story.append(
            Paragraph(
                "FICTIONAL EXAMPLE - NOT FOR SUBMISSION<br/>"
                + html_escape(str(sample_target))
                + "; this is not the user's resume and was not generated from the user's experience.",
                styles["example_banner"],
            )
        )
    story.extend([
        Paragraph(html_escape(str(candidate.get("name") or "Candidate Name").upper()), styles["name"]),
        Paragraph(html_escape(str(template.get("headline") or draft.get("target_role") or "Target Role")), styles["role"]),
        Paragraph(html_escape(" | ".join(contact_items)), styles["contact"]),
        HRFlowable(width="100%", thickness=0.8, color=colors.black, spaceAfter=7),
    ])

    story.extend(heading("Professional Summary"))
    story.append(Paragraph(html_escape(str(sections.get("professional_summary") or "")), styles["body"]))

    story.extend(heading(str(template.get("skills_heading") or "Technical Skills")))
    skill_rows = []
    for group in sections.get("skill_groups") or []:
        skill_rows.append([
            Paragraph(html_escape(str(group.get("label") or "Skills")), styles["skill_label"]),
            Paragraph(html_escape(", ".join(group.get("items") or [])), styles["skill_value"]),
        ])
    if skill_rows:
        skill_table = Table(skill_rows, colWidths=[1.35 * inch, 5.65 * inch], hAlign="LEFT")
        skill_table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]))
        story.append(skill_table)

    section_order = [
        ("experience_bullets", str(template.get("experience_heading") or "Professional Experience And Projects")),
        ("projects", str(template.get("project_heading") or "Projects")),
        ("certifications", str(template.get("certification_heading") or "Certifications")),
        ("achievements", str(template.get("achievement_heading") or "Achievements")),
        ("education", str(template.get("education_heading") or "Education")),
    ]
    for section_key, section_heading in section_order:
        items = real_resume_items(list(sections.get(section_key) or []))
        if not items:
            continue
        story.extend(heading(section_heading))
        for item in items:
            if section_key == "education":
                credential, institution, dates = split_generated_education_item(str(item))
                detail = " | ".join(value for value in (institution, dates) if value)
                content = "- <b>" + html_escape(credential) + "</b>"
                if detail:
                    content += "<br/><font size=\"8.8\">" + html_escape(detail) + "</font>"
                story.append(Paragraph(content, styles["bullet"]))
            else:
                story.append(Paragraph("- " + html_escape(str(item)), styles["bullet"]))

    document.build(story)
    buffer.seek(0)
    return buffer


def build_feedback_report_pdf_buffer(report: dict[str, Any]) -> BytesIO:
    try:
        from reportlab.lib import colors  # type: ignore
        from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT  # type: ignore
        from reportlab.lib.pagesizes import letter  # type: ignore
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet  # type: ignore
        from reportlab.lib.units import inch  # type: ignore
        from reportlab.platypus import (  # type: ignore
            HRFlowable,
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
        from reportlab.pdfgen import canvas  # type: ignore
    except ImportError as error:
        raise RuntimeError("Install reportlab to enable PDF feedback report export.") from error

    def clean_report_text(value: Any, fallback: str = "", max_length: int = 700) -> str:
        text = normalize_text(str(value or ""))
        if not text:
            text = fallback
        if max_length and len(text) > max_length:
            return text[: max_length - 3].rstrip() + "..."
        return text

    def clean_report_items(value: Any, fallback: str = "", limit: int = 8, max_length: int = 180) -> list[str]:
        if not isinstance(value, list):
            return [fallback] if fallback else []
        cleaned = []
        for item in value:
            text = clean_report_text(item, "", max_length)
            if text:
                cleaned.append(text)
            if len(cleaned) >= limit:
                break
        return cleaned or ([fallback] if fallback else [])

    def metric_status(value: Any) -> str:
        try:
            safe_percent = max(0.0, min(100.0, float(value or 0)))
        except (TypeError, ValueError):
            safe_percent = 0.0
        if safe_percent >= 75:
            return "Strong"
        if safe_percent >= 55:
            return "Fair"
        return "Needs work"

    def score_label(value: Any) -> str:
        try:
            score = round(max(0.0, min(100.0, float(value or 0))))
        except (TypeError, ValueError):
            score = 0
        return f"{score}/100"

    def criteria_items() -> list[dict[str, Any]]:
        raw_criteria = report.get("qualityCriteria")
        if not isinstance(raw_criteria, list):
            return []
        return [item for item in raw_criteria if isinstance(item, dict)]

    def top_role_items() -> list[dict[str, Any]]:
        raw_matches = report.get("topRoleMatches")
        if not isinstance(raw_matches, list):
            return []
        return [item for item in raw_matches[:3] if isinstance(item, dict)]

    def recommendation_action_items() -> list[dict[str, Any]]:
        raw_actions = report.get("recommendationPlan") or report.get("priorityActions")
        if not isinstance(raw_actions, list):
            return []
        return [item for item in raw_actions[:5] if isinstance(item, dict)]

    def issue_kind(item: dict[str, Any]) -> str:
        combined = normalize_match_text(
            f"{item.get('category', '')} {item.get('title', '')} {item.get('detail', '')}"
        )
        if re.search(r"\b(embedded|photo|passport|decorative image|image detected)\b", combined):
            return "image"
        if re.search(r"\b(contact|email|phone|linkedin|github|portfolio)\b", combined):
            return "contact"
        if re.search(r"\b(skill|technical|tool|framework|language)\b", combined):
            return "skills"
        if re.search(r"\b(section|heading|education|project)\b", combined):
            return "sections"
        if re.search(r"\b(impact|measure|quant|number|outcome)\b", combined):
            return "impact"
        if "length" in combined:
            return "length"
        if re.search(r"\b(image|format|layout|spacing|column|font|parser)\b", combined):
            return "format"
        return "general"

    def marker_position(item: dict[str, Any], index: int) -> dict[str, float]:
        positions = {
            "image": {"top": 12.0, "left": 58.0, "width": 22.0, "height": 20.0},
            "contact": {"top": 21.0, "left": 8.0, "width": 31.0, "height": 9.0},
            "format": {"top": 36.0, "left": 8.0, "width": 34.0, "height": 15.0},
            "sections": {"top": 53.0, "left": 8.0, "width": 34.0, "height": 17.0},
            "impact": {"top": 39.0, "left": 51.0, "width": 37.0, "height": 23.0},
            "length": {"top": 70.0, "left": 11.0, "width": 75.0, "height": 10.0},
            "skills": {"top": 31.0, "left": 8.0, "width": 35.0, "height": 10.0},
            "general": {
                "top": 29.0 + (index * 8.0),
                "left": 12.0 + (index * 7.0),
                "width": 34.0,
                "height": 10.0,
            },
        }
        return positions.get(issue_kind(item), positions["general"])

    def decode_data_url(value: Any) -> tuple[str, bytes] | None:
        data_url = str(value or "")
        match = re.match(r"^data:([^;,]+);base64,(.+)$", data_url, re.S)
        if not match:
            return None
        try:
            return match.group(1).lower(), base64.b64decode(match.group(2), validate=False)
        except Exception:
            return None

    def build_annotated_resume_pdf_preview() -> BytesIO | None:
        preview = report.get("resumeVisualPreview") if isinstance(report.get("resumeVisualPreview"), dict) else {}
        decoded = decode_data_url(preview.get("dataUrl"))
        if not decoded:
            return None

        mime_type, raw_file = decoded
        if mime_type != "application/pdf":
            return None

        try:
            resume_reader = PdfReader(BytesIO(raw_file))
            if not resume_reader.pages:
                return None
            page = resume_reader.pages[0]
            page_width = float(page.mediabox.width)
            page_height = float(page.mediabox.height)

            overlay_buffer = BytesIO()
            overlay_canvas = canvas.Canvas(overlay_buffer, pagesize=(page_width, page_height))
            overlay_canvas.setTitle("Annotated Resume Preview")
            overlay_canvas.setStrokeColor(colors.HexColor("#d01822"))
            overlay_canvas.setFillColor(colors.HexColor("#d01822"))
            overlay_canvas.setLineWidth(1.4)
            overlay_canvas.setFont("Helvetica-Bold", 9)

            for index, item in enumerate(quality_issues[:5]):
                position = marker_position(item, index)
                x = page_width * (position["left"] / 100.0)
                rect_width = page_width * (position["width"] / 100.0)
                rect_height = page_height * (position["height"] / 100.0)
                y = page_height - (page_height * (position["top"] / 100.0)) - rect_height
                overlay_canvas.roundRect(x, y, rect_width, rect_height, 4, stroke=1, fill=0)
                badge_x = min(page_width - 11, x + rect_width)
                badge_y = min(page_height - 11, y + rect_height)
                overlay_canvas.circle(badge_x, badge_y, 9, stroke=0, fill=1)
                overlay_canvas.setFillColor(colors.white)
                overlay_canvas.drawCentredString(badge_x, badge_y - 3, str(index + 1))
                overlay_canvas.setFillColor(colors.HexColor("#d01822"))

            overlay_canvas.showPage()
            overlay_canvas.save()
            overlay_buffer.seek(0)
            page.merge_page(PdfReader(overlay_buffer).pages[0])

            preview_writer = PdfWriter()
            preview_writer.add_page(page)
            preview_buffer = BytesIO()
            preview_writer.write(preview_buffer)
            preview_buffer.seek(0)
            return preview_buffer
        except Exception:
            return None

    resume_quality = report.get("resumeQuality") if isinstance(report.get("resumeQuality"), dict) else {}
    compatibility = report.get("compatibility") if isinstance(report.get("compatibility"), dict) else {}
    score_explanation = report.get("scoreExplanation") if isinstance(report.get("scoreExplanation"), dict) else {}
    quality_criteria = criteria_items()
    quality_issues = [item for item in quality_criteria if item.get("status") != "pass"]
    final_score = score_label(report.get("scoreOutOf100"))
    generated_at = clean_report_text(report.get("generatedAt"), datetime.now().strftime("%Y-%m-%d %H:%M"))
    annotated_resume_buffer = build_annotated_resume_pdf_preview()

    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=0.55 * inch,
        rightMargin=0.55 * inch,
        topMargin=0.55 * inch,
        bottomMargin=0.55 * inch,
        title="UTS CS Career Navigator Feedback Report",
    )
    base_styles = getSampleStyleSheet()
    styles = {
        "title": ParagraphStyle(
            "FeedbackReportTitle",
            parent=base_styles["Title"],
            alignment=TA_LEFT,
            fontName="Helvetica-Bold",
            fontSize=20,
            leading=24,
            textColor=colors.HexColor("#002555"),
            spaceAfter=3,
        ),
        "score": ParagraphStyle(
            "FeedbackReportScore",
            parent=base_styles["Title"],
            alignment=TA_RIGHT,
            fontName="Helvetica-Bold",
            fontSize=26,
            leading=30,
            textColor=colors.HexColor("#0060ac"),
        ),
        "subtitle": ParagraphStyle(
            "FeedbackReportSubtitle",
            parent=base_styles["Normal"],
            fontSize=9.5,
            leading=12,
            textColor=colors.HexColor("#43474f"),
            spaceAfter=10,
        ),
        "section": ParagraphStyle(
            "FeedbackReportSection",
            parent=base_styles["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=11,
            leading=13,
            textColor=colors.HexColor("#0060ac"),
            spaceBefore=12,
            spaceAfter=4,
        ),
        "heading": ParagraphStyle(
            "FeedbackReportHeading",
            parent=base_styles["Heading3"],
            fontName="Helvetica-Bold",
            fontSize=12.5,
            leading=15,
            textColor=colors.HexColor("#002555"),
            spaceBefore=5,
            spaceAfter=5,
        ),
        "body": ParagraphStyle(
            "FeedbackReportBody",
            parent=base_styles["Normal"],
            fontSize=9,
            leading=12,
            textColor=colors.HexColor("#191c1d"),
            spaceAfter=4,
        ),
        "muted": ParagraphStyle(
            "FeedbackReportMuted",
            parent=base_styles["Normal"],
            fontSize=8,
            leading=10,
            textColor=colors.HexColor("#64748b"),
        ),
        "table_label": ParagraphStyle(
            "FeedbackReportTableLabel",
            parent=base_styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=8.5,
            leading=11,
            textColor=colors.HexColor("#17324f"),
        ),
        "table_cell": ParagraphStyle(
            "FeedbackReportTableCell",
            parent=base_styles["Normal"],
            fontSize=8.4,
            leading=10.8,
            textColor=colors.HexColor("#26384d"),
        ),
        "table_header": ParagraphStyle(
            "FeedbackReportTableHeader",
            parent=base_styles["Normal"],
            alignment=TA_CENTER,
            fontName="Helvetica-Bold",
            fontSize=8.3,
            leading=10.5,
            textColor=colors.white,
        ),
        "note": ParagraphStyle(
            "FeedbackReportNote",
            parent=base_styles["Normal"],
            fontSize=8.7,
            leading=11.2,
            textColor=colors.HexColor("#17324f"),
            backColor=colors.HexColor("#edf5ff"),
            borderColor=colors.HexColor("#bfd4f4"),
            borderWidth=0.6,
            borderPadding=6,
            spaceAfter=6,
        ),
        "excerpt": ParagraphStyle(
            "FeedbackReportExcerpt",
            parent=base_styles["Code"],
            fontName="Courier",
            fontSize=7.5,
            leading=9.4,
            textColor=colors.HexColor("#334155"),
            backColor=colors.HexColor("#f8fafc"),
            borderColor=colors.HexColor("#d9e1ec"),
            borderWidth=0.4,
            borderPadding=4,
            spaceAfter=2,
        ),
    }

    def paragraph(value: Any, style_name: str = "body", fallback: str = "", max_length: int = 700):
        return Paragraph(html_escape(clean_report_text(value, fallback, max_length)), styles[style_name])

    def report_table(rows: list[list[Any]], col_widths: list[float], has_header: bool = False) -> Table:
        styled_rows = []
        for row_index, row in enumerate(rows):
            style_name = "table_header" if has_header and row_index == 0 else "table_cell"
            styled_rows.append([paragraph(cell, style_name, max_length=420) for cell in row])
        table = Table(styled_rows, colWidths=col_widths, hAlign="LEFT", repeatRows=1 if has_header else 0)
        table_style = [
            ("GRID", (0, 0), (-1, -1), 0.45, colors.HexColor("#d9e1ec")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]
        if has_header:
            table_style.extend(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#002555")),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
                ]
            )
        else:
            table_style.extend(
                [
                    ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f1f5f9")),
                    ("ROWBACKGROUNDS", (1, 0), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
                ]
            )
        table.setStyle(TableStyle(table_style))
        return table

    def add_section(story: list[Any], title: str) -> None:
        story.append(Paragraph(html_escape(title.upper()), styles["section"]))
        story.append(HRFlowable(width="100%", thickness=0.45, color=colors.HexColor("#d9e1ec"), spaceAfter=6))

    summary_rows = [
        ["Uploaded file", clean_report_text(report.get("uploadedResumeName"), "Not available", 180)],
        ["File type", clean_report_text(report.get("fileType"), "Not available", 80)],
        ["Selected role", clean_report_text(report.get("selectedRole"), "Not selected", 160)],
        ["Database role", clean_report_text(report.get("selectedDatabaseRole"), "Not mapped", 160)],
        ["Recommended role", clean_report_text(report.get("recommendedRole"), "Not available", 160)],
        ["Final readiness score", final_score],
        ["ATS quality risk", clean_report_text(resume_quality.get("risk_level"), "Not available", 80)],
        ["Submitted at", clean_report_text(report.get("submittedAt"), generated_at, 140)],
    ]
    raw_components = score_explanation.get("components")
    score_components = [item for item in raw_components[:8] if isinstance(item, dict)] if isinstance(raw_components, list) else []
    if score_components:
        component_rows = [
            [
                clean_report_text(item.get("label"), "Component", 80),
                clean_report_text(item.get("value"), "Not available", 80),
            ]
            for item in score_components
        ]
    else:
        component_rows = [
            ["Keyword match", metric_status(compatibility.get("keyword_match"))],
            ["Experience alignment", metric_status(compatibility.get("experience_alignment"))],
            ["Education / skills evidence", metric_status(compatibility.get("academic_rigor"))],
            ["ATS format quality", clean_report_text(resume_quality.get("risk_level"), "Not available", 80) + " risk"],
        ]

    def count_value(value: Any) -> int:
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    resume_parsing = report.get("resumeParsing") if isinstance(report.get("resumeParsing"), dict) else {}
    evidence_rows = [
        [
            "Parsed from resume",
            f"{count_value(resume_parsing.get('skills'))} skills, "
            f"{count_value(resume_parsing.get('educationMarkers'))} education markers, "
            f"{count_value(resume_parsing.get('experienceMarkers'))} experience markers",
        ],
        [
            "Role keywords",
            f"{count_value(resume_parsing.get('importantKeywords'))} important, "
            f"{count_value(resume_parsing.get('missingKeywords'))} missing",
        ],
    ]

    top_rows = [["Rank", "Database role", "Evidence score", "Status", "Evidence"]]
    for index, item in enumerate(top_role_items(), start=1):
        raw_score = item.get("score", item.get("confidence_score"))
        role_score = score_label(raw_score)
        labels = ", ".join(
            [
                label
                for label in [
                    "Used for ATS score" if item.get("is_final_recommended") else "",
                    "Selected" if item.get("is_selected_database_role") else "",
                ]
                if label
            ]
        ) or "Compared"
        matched = ", ".join(clean_report_items(item.get("matched_keywords"), "No strong matched keywords", 6, 50))
        missing = ", ".join(clean_report_items(item.get("missing_keywords"), "No major missing gaps", 5, 50))
        top_rows.append(
            [
                str(item.get("rank") or index),
                clean_report_text(item.get("role") or item.get("database_role"), "Role match", 140),
                f"{role_score} - {clean_report_text(item.get('confidence_label'), metric_status(raw_score), 60)}",
                labels,
                f"Matched: {matched} Improve: {missing}",
            ]
        )
    if len(top_rows) == 1:
        top_rows.append(["-", "No top role matches returned", "-", "-", "Run an analysis to compare role evidence."])

    # Fix rows are numbered in the same order as the red markers on the annotated resume page.
    quality_rows = [["Criteria", "Status", "Finding", "Suggested fix"]]
    fix_number = 0
    for item in quality_criteria:
        if item.get("status") != "pass":
            fix_number += 1
        quality_rows.append(
            [
                clean_report_text(item.get("category"), "Criteria", 80),
                "Pass" if item.get("status") == "pass" else f"Fix {fix_number}",
                clean_report_text(item.get("title") or item.get("detail"), "Finding not available", 190),
                clean_report_text(item.get("suggestion") or item.get("detail"), "Suggested fix not available", 260),
            ]
        )
    if len(quality_rows) == 1:
        quality_rows.append(["-", "Pending", "No ATS quality criteria returned", "Run an analysis to generate ATS quality checks."])

    story: list[Any] = []
    header = Table(
        [
            [
                Paragraph("UTS CS Career Navigator<br/>Feedback Report", styles["title"]),
                Paragraph(final_score, styles["score"]),
            ]
        ],
        colWidths=[5.35 * inch, 1.65 * inch],
        hAlign="LEFT",
    )
    header.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
                ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#d9e1ec")),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                ("TOPPADDING", (0, 0), (-1, -1), 12),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
            ]
        )
    )
    story.append(header)
    story.append(Paragraph("Structured ATS issues, resume evidence, and next-edit actions. Generated " + html_escape(generated_at) + ".", styles["subtitle"]))

    add_section(story, "Summary")
    story.append(report_table(summary_rows, [1.9 * inch, 5.1 * inch]))

    add_section(story, "Score Explanation")
    story.append(paragraph(score_explanation.get("label"), "heading", "Score meaning", 120))
    story.append(paragraph(score_explanation.get("text"), "note", "The final readiness score combines role match, resume evidence, and ATS format quality.", 650))
    if not score_components:
        story.append(paragraph(score_explanation.get("detail") or score_explanation.get("formula"), "body", "Formula details were not available.", 650))

    add_section(story, "Role Fit Explanation")
    story.append(paragraph(report.get("roleReason"), "note", "Selected role and recommendation are aligned.", 650))

    recommendation_actions = recommendation_action_items()
    if recommendation_actions:
        add_section(story, "Priority Improvement Plan")
        for index, item in enumerate(recommendation_actions, start=1):
            keywords = ", ".join(clean_report_items(item.get("keywords"), "", 5, 45))
            keyword_text = f" Keywords: {keywords}." if keywords else ""
            action_text = (
                f"{item.get('priority') or index}. {clean_report_text(item.get('title'), 'Recommendation action', 90)}: "
                f"{clean_report_text(item.get('action') or item.get('detail'), 'Apply this improvement before exporting the resume.', 260)}"
                f"{keyword_text}"
            )
            story.append(paragraph("- " + action_text, "body", max_length=390))

    add_section(story, "Top 3 Role Matches")
    story.append(
        paragraph(
            "Evidence-match scores compare resume text with database requirements; they are not hiring probabilities or validated model accuracy.",
            "muted",
            max_length=220,
        )
    )
    story.append(report_table(top_rows, [0.45 * inch, 1.55 * inch, 1.15 * inch, 0.9 * inch, 3.0 * inch], has_header=True))

    add_section(story, "Score Components")
    story.append(report_table(component_rows, [1.9 * inch, 5.1 * inch]))
    if score_components:
        story.append(paragraph(score_explanation.get("method"), "muted", "Formula details were not available.", 650))
    else:
        story.append(paragraph("Only the final readiness score is numeric. Component results are shown as status labels to keep the report easy to read.", "muted", max_length=220))

    add_section(story, "ATS Quality Criteria")
    story.append(report_table(quality_rows, [1.05 * inch, 0.75 * inch, 2.05 * inch, 3.15 * inch], has_header=True))
    if annotated_resume_buffer:
        story.append(paragraph("The first page of this PDF is your uploaded resume. Its numbered red markers match the numbered Fix rows above.", "note", max_length=260))
    else:
        story.append(paragraph("The report preview on the results page shows the uploaded resume when the original file is available.", "note", max_length=260))

    add_section(story, "Resume Evidence")
    story.append(report_table(evidence_rows, [1.9 * inch, 5.1 * inch]))
    matched_signals = ", ".join(clean_report_items(report.get("matchedSignals"), "No matched signals", 10, 55))
    missing_keywords = ", ".join(clean_report_items(report.get("missingKeywords"), "No missing keywords", 10, 55))
    story.append(paragraph("Matched signals: " + matched_signals, "body", max_length=520))
    story.append(paragraph("Missing keywords: " + missing_keywords, "body", max_length=520))
    rewrite_hint = (
        "Rewrite project bullets to include missing keywords naturally, only where you have real evidence. "
        if clean_report_items(report.get("missingKeywords"), "", 1)
        else ""
    )
    story.append(paragraph(rewrite_hint + "Similar terms such as developer/development and test/testing are normalized before scoring.", "muted", max_length=260))

    add_section(story, "Next Actions")
    for item in clean_report_items(report.get("structuredFeedback"), clean_report_text(report.get("feedback"), "No feedback available.", 280), 8, 240):
        story.append(paragraph("- " + item, "body", max_length=300))

    story.append(Spacer(1, 0.12 * inch))
    story.append(Paragraph("Generated locally by UTS CS Career Navigator.", styles["muted"]))

    document.build(story)
    buffer.seek(0)

    if annotated_resume_buffer:
        combined_writer = PdfWriter()
        for source_buffer in (annotated_resume_buffer, buffer):
            source_buffer.seek(0)
            reader = PdfReader(source_buffer)
            for page in reader.pages:
                combined_writer.add_page(page)
        combined_buffer = BytesIO()
        combined_writer.write(combined_buffer)
        combined_buffer.seek(0)
        return combined_buffer

    return buffer


def get_top_role_generation_scores(analysis: dict[str, Any]) -> list[float]:
    role_recommendation = analysis.get("role_recommendation") if isinstance(analysis.get("role_recommendation"), dict) else {}
    raw_matches = analysis.get("top_role_matches") or role_recommendation.get("top_role_matches") or []
    if not isinstance(raw_matches, list):
        return []

    scores: list[float] = []
    for match in raw_matches[:3]:
        if not isinstance(match, dict):
            continue
        raw_score = match.get("score")
        if raw_score is None:
            raw_score = match.get("confidence_score")
        try:
            scores.append(float(raw_score))
        except (TypeError, ValueError):
            continue
    return scores


def get_top_role_generation_name(analysis: dict[str, Any]) -> str:
    role_recommendation = analysis.get("role_recommendation") if isinstance(analysis.get("role_recommendation"), dict) else {}
    raw_matches = analysis.get("top_role_matches") or role_recommendation.get("top_role_matches") or []
    if isinstance(raw_matches, list) and raw_matches and isinstance(raw_matches[0], dict):
        top_role = normalize_text(str(raw_matches[0].get("role") or raw_matches[0].get("database_role") or ""))
        if top_role:
            return top_role
    return ""


def is_explicit_resume_generation_role(value: Any) -> bool:
    normalized = normalize_match_text(str(value or ""))
    if not normalized:
        return False
    return not any(
        marker in normalized
        for marker in ("auto detect", "selected role", "sample role")
    )


def choose_fallback_sample_role(analysis: dict[str, Any]) -> str:
    role_recommendation = (
        analysis.get("role_recommendation")
        if isinstance(analysis.get("role_recommendation"), dict)
        else {}
    )
    stored_role = normalize_text(str(role_recommendation.get("fallback_sample_role") or ""))
    if stored_role in AUTO_NO_MATCH_SAMPLE_ROLES:
        return stored_role

    top_role_names = [
        normalize_text(str(item.get("role") or item.get("database_role") or ""))
        for item in list(analysis.get("top_role_matches") or role_recommendation.get("top_role_matches") or [])[:3]
        if isinstance(item, dict)
    ]
    seed = "|".join(
        [
            normalize_text(str(analysis.get("uploaded_resume_name") or "")),
            normalize_text(str(analysis.get("resume_excerpt") or ""))[:500],
            *top_role_names,
        ]
    ) or "career-navigator-fallback-example"
    digest = hashlib.sha256(seed.encode("utf-8", errors="replace")).digest()
    return AUTO_NO_MATCH_SAMPLE_ROLES[int.from_bytes(digest[:8], "big") % len(AUTO_NO_MATCH_SAMPLE_ROLES)]


def analysis_has_explicit_non_it_headline(analysis: dict[str, Any]) -> bool:
    role_recommendation = (
        analysis.get("role_recommendation")
        if isinstance(analysis.get("role_recommendation"), dict)
        else {}
    )
    if role_recommendation.get("explicit_non_it_headline"):
        return True
    matches = analysis.get("top_role_matches") or role_recommendation.get("top_role_matches") or []
    return any(
        isinstance(item, dict)
        and (
            item.get("explicit_non_it_headline")
            or item.get("title_match_source") == "non_it_headline"
        )
        for item in list(matches)[:3]
    )


def role_score_needs_human_review(score: float | None, explicit_non_it_headline: bool) -> bool:
    if score is None:
        return False
    return bool(
        score < CS_SAMPLE_ONLY_TOP_MATCH_THRESHOLD
        or (
            explicit_non_it_headline
            and score < NON_IT_HEADLINE_REVIEW_THRESHOLD
        )
    )


def get_resume_sample_decision(analysis: dict[str, Any]) -> dict[str, Any]:
    """Choose between a truthful rewrite and a clearly labelled sample resume.

    An explicitly selected role is evaluated using its own selected-role score,
    not the score of the best detected role. Automatic analysis uses the same
    conservative no-suitable-role disposition as the result page.
    """

    if not analysis:
        return {"should_sample": False}

    role_recommendation = (
        analysis.get("role_recommendation")
        if isinstance(analysis.get("role_recommendation"), dict)
        else {}
    )
    selected_label = normalize_text(
        str(role_recommendation.get("selected_role") or analysis.get("selected_role") or "")
    )
    match_status = normalize_match_text(str(role_recommendation.get("match_status") or ""))
    explicit_non_it_headline = analysis_has_explicit_non_it_headline(analysis)
    review_threshold = (
        NON_IT_HEADLINE_REVIEW_THRESHOLD
        if explicit_non_it_headline
        else CS_SAMPLE_ONLY_TOP_MATCH_THRESHOLD
    )
    if is_explicit_resume_generation_role(selected_label):
        selected_target = selected_label
        selected_database_target = normalize_text(
            str(role_recommendation.get("selected_database_role") or "")
        )
        try:
            selected_score = float(role_recommendation.get("selected_score"))
        except (TypeError, ValueError):
            selected_score = None

        if match_status == "review required" or role_score_needs_human_review(
            selected_score,
            explicit_non_it_headline,
        ):
            top_scores = get_top_role_generation_scores(analysis)
            return {
                "should_sample": True,
                "reason_code": "selected_role_review_required",
                "target_role": selected_target,
                "mapped_database_role": selected_database_target,
                "evidence_score": selected_score,
                "threshold": review_threshold,
                "best_top_role_score": max(top_scores) if top_scores else None,
                "selection_mode": "selected_role",
            }
        return {
            "should_sample": False,
            "reason_code": "selected_role_has_evidence" if selected_score is not None else "selected_role_score_unavailable",
            "target_role": selected_target,
            "mapped_database_role": selected_database_target,
            "evidence_score": selected_score,
            "threshold": review_threshold,
        }

    top_scores = get_top_role_generation_scores(analysis)
    if top_scores:
        best_score = max(top_scores)
        no_suitable_role = match_status == "no suitable role" or role_score_needs_human_review(
            best_score,
            explicit_non_it_headline,
        )
        return {
            "should_sample": no_suitable_role,
            "reason_code": "auto_detected_no_suitable_role" if no_suitable_role else "auto_detected_role_has_evidence",
            "target_role": choose_fallback_sample_role(analysis) if no_suitable_role else get_top_role_generation_name(analysis),
            "evidence_score": best_score,
            "threshold": review_threshold,
            "best_top_role_score": best_score,
            "closest_role": get_top_role_generation_name(analysis),
            "selection_mode": "deterministic_fallback" if no_suitable_role else "detected_role",
        }

    if "matching_percentage" not in analysis:
        return {"should_sample": False}
    try:
        score = float(analysis.get("matching_percentage") or 0)
    except (TypeError, ValueError):
        return {"should_sample": False}
    no_suitable_role = match_status == "no suitable role" or role_score_needs_human_review(
        score,
        explicit_non_it_headline,
    )
    return {
        "should_sample": no_suitable_role,
        "reason_code": "auto_detected_no_suitable_role" if no_suitable_role else "auto_detected_role_has_evidence",
        "target_role": choose_fallback_sample_role(analysis) if no_suitable_role else get_top_role_generation_name(analysis),
        "evidence_score": score,
        "threshold": review_threshold,
        "best_top_role_score": score,
        "closest_role": get_top_role_generation_name(analysis),
        "selection_mode": "deterministic_fallback" if no_suitable_role else "detected_role",
    }


def build_sample_resume_role_profile(target_role: str) -> dict[str, Any]:
    """Return a polished, deterministic fictional resume for a role family.

    The surrounding resume renderer carries the fictional/not-for-submission
    disclaimer.  The resume sections themselves intentionally read like a
    finished professional example: instructions and fill-in tokens inside the
    document made the low-match preview look like a worksheet rather than a
    useful resume reference.
    """

    role = normalize_text(target_role) or "Technology Professional"
    normalized_role = normalize_match_text(role)

    if any(term in normalized_role for term in ("ui ux", "ux", "user experience", "interface designer", "product designer")):
        family = "UI / UX"
        skills = ["Figma", "Wireframing", "Prototyping", "User Research", "Usability Testing", "Accessibility", "Design Systems", "HTML", "CSS"]
        focus = "user research, accessible interaction design, prototypes, and evidence-based design decisions"
        experience = [
            "Interviewed 18 users across three cohorts, mapped their highest-friction tasks, and translated the findings into prioritised user stories and journey maps.",
            "Designed and tested a 14-screen Figma prototype; two design iterations improved task completion from 68% to 91%.",
            "Specified responsive states, WCAG accessibility checks, and 24 reusable components for developer handoff, reducing clarification requests by 35%.",
        ]
        projects = [
            "Student Services Redesign - recruited 12 test users, mapped two key journeys, validated a clickable Figma prototype, and reported before-and-after task metrics.",
            "Accessible Design System - documented 24 reusable components, keyboard and error states, contrast evidence, and implementation guidance for an engineering team.",
            "Mobile Checkout Usability Study - planned 10 moderated sessions, coded recurring usability problems, and prioritised design changes by severity and user impact.",
        ]
        certification = "Google UX Design Professional Certificate (2025)"
        education_focus = "User Experience Design"
        achievement = "Raised prototype task completion from 68% to 91% through two evidence-led design iterations."
    elif any(term in normalized_role for term in ("cyber", "security analyst", "security engineer", "information security", "soc analyst")):
        family = "Cybersecurity"
        skills = ["Network Security", "Linux", "Python", "SIEM", "Wireshark", "OWASP", "Vulnerability Assessment", "Incident Response", "Identity and Access Management"]
        focus = "security monitoring, vulnerability assessment, incident documentation, and defensive controls"
        experience = [
            "Triaged 240 SIEM security alerts in an authorised lab, correlated host and network indicators, and documented severity, evidence, and escalation decisions.",
            "Assessed a deliberately vulnerable application against OWASP risks, validated 11 findings, and prioritised remediation by likelihood and business impact.",
            "Refined a detection rule and incident playbook, reducing lab false positives from 34% to 12% while retaining documented test coverage.",
        ]
        projects = [
            "Security Monitoring Lab - collected Linux, authentication, and network logs; created 16 SIEM queries; and verified alert escalation with repeatable test cases.",
            "Web Security Assessment - tested an authorised lab application, recorded reproducible evidence for 11 findings, and produced a risk-ranked remediation report.",
            "Incident Response Tabletop - built a ransomware scenario, assigned containment and recovery actions, and documented 8 lessons from the post-incident review.",
        ]
        certification = "CompTIA Security+ (2025)"
        education_focus = "Cybersecurity"
        achievement = "Reduced lab false-positive alerts from 34% to 12% by refining a documented detection rule."
    elif any(term in normalized_role for term in ("cloud", "devops", "site reliability", "platform engineer", "release engineer")):
        family = "Cloud / DevOps"
        skills = ["Linux", "Git", "Docker", "Kubernetes", "CI/CD", "GitHub Actions", "Terraform", "AWS", "Monitoring", "Scripting"]
        focus = "automated delivery, reproducible infrastructure, containerised services, and operational monitoring"
        experience = [
            "Containerised a web service and built a CI/CD pipeline for linting, 86 automated tests, image scanning, and repeatable deployment.",
            "Provisioned 14 cloud resources with Terraform, applying versioned configuration, least-privilege access, and a tested rollback procedure.",
            "Defined availability and error-rate indicators, created actionable alerts, and reduced recovery time from 28 minutes to 9 minutes during drills.",
        ]
        projects = [
            "Container Deployment Pipeline - packaged an API with Docker, automated six quality gates in GitHub Actions, and reduced deployment lead time from 22 minutes to 8 minutes.",
            "Infrastructure Monitoring Lab - provisioned cloud resources, instrumented latency and error metrics, and tested 12 alert and recovery scenarios.",
            "Highly Available Web Service - designed a multi-zone deployment, documented scaling and backup choices, and load-tested it to 1,200 requests per second.",
        ]
        certification = "AWS Certified Cloud Practitioner (2025)"
        education_focus = "Cloud Computing"
        achievement = "Cut deployment lead time by 64% through automated quality gates and repeatable release steps."
    elif any(term in normalized_role for term in ("network", "infrastructure engineer", "systems administrator", "system administrator")):
        family = "Network / Infrastructure"
        skills = ["TCP/IP", "Routing", "Switching", "VLAN", "DNS", "DHCP", "Linux", "Windows Server", "Wireshark", "Network Troubleshooting"]
        focus = "network configuration, infrastructure monitoring, fault isolation, and technical documentation"
        experience = [
            "Configured a 36-device segmented network with VLANs, addressing, DHCP, and access controls, then recorded successful connectivity tests.",
            "Analysed packet captures and service logs to isolate 12 simulated DNS, routing, and access-control faults and document their root causes.",
            "Created a monitoring and escalation checklist that reduced fault-isolation time from 42 minutes to 16 minutes during lab exercises.",
        ]
        projects = [
            "Campus Network Lab - designed a topology for 6 departments with VLAN segmentation, routing, addressing tables, and verification evidence.",
            "Infrastructure Health Dashboard - tracked availability, latency, and packet loss for 18 services and linked alerts to incident notes.",
            "Branch Connectivity Simulation - configured redundant routes and failover checks, then documented recovery results across 9 controlled outage scenarios.",
        ]
        certification = "Cisco Certified Network Associate (CCNA) (2025)"
        education_focus = "Network Engineering"
        achievement = "Reduced incident diagnosis time by 62% with a documented troubleshooting and escalation checklist."
    elif any(term in normalized_role for term in ("database", "dba", "sql developer", "data engineer")):
        family = "Database"
        skills = ["SQL", "MySQL", "PostgreSQL", "Data Modelling", "Schema Design", "Indexing", "Query Optimisation", "Backup and Recovery", "Stored Procedures"]
        focus = "relational data modelling, reliable database operations, query performance, and data integrity"
        experience = [
            "Designed a 14-table relational schema, normalised key entities, and added constraints, roles, and validation rules to protect data integrity.",
            "Diagnosed 8 slow queries with execution plans and indexing, reducing median latency from 840 ms to 190 ms.",
            "Automated a backup-and-restore verification routine and documented recovery-point and recovery-time results across 6 controlled tests.",
        ]
        projects = [
            "Inventory Database - modelled products, suppliers, transactions, permissions, and audit history across 14 related tables and documented representative SQL queries.",
            "Database Recovery Lab - scripted backup, restore, integrity checks, and recovery verification for 6 failure scenarios.",
            "Query Performance Study - generated a 250,000-row workload, compared execution plans, and reported evidence-based indexing trade-offs.",
        ]
        certification = "Microsoft Certified: Azure Data Fundamentals (2025)"
        education_focus = "Database Systems"
        achievement = "Improved median query response time by 77% through indexing and execution-plan review."
    elif any(term in normalized_role for term in ("quality assurance", "qa", "tester", "test engineer", "software test")):
        family = "Quality Assurance / Testing"
        skills = ["Test Planning", "Manual Testing", "Test Automation", "Selenium", "Postman", "API Testing", "Pytest", "Regression Testing", "Defect Tracking", "Git"]
        focus = "risk-based test design, reproducible defect reports, API validation, and regression automation"
        experience = [
            "Derived 64 positive, negative, and boundary test cases from acceptance criteria and linked each result to traceable evidence.",
            "Reported 23 reproducible defects with severity, environment, steps, expected and actual results, and verification status.",
            "Automated a UI regression flow and API checks, increasing repeatable coverage from 38% to 76% while documenting residual risks.",
        ]
        projects = [
            "Web Application Test Suite - created 64 UI, API, boundary, and regression tests with versioned data and a pass/fail results report.",
            "Defect Quality Dashboard - summarised 23 defects by severity, ageing, retest status, escape rate, and release risk.",
            "API Contract Test Project - validated authentication, schemas, error responses, and rate limits across 15 endpoints using Postman and Pytest.",
        ]
        certification = "ISTQB Certified Tester Foundation Level (2025)"
        education_focus = "Software Quality Assurance"
        achievement = "Doubled repeatable regression coverage by converting recurring UI and API checks into automated tests."
    elif any(term in normalized_role for term in ("help desk", "service desk", "it support", "support specialist", "computer technician", "desktop support")):
        family = "IT Support"
        skills = ["Windows", "Linux", "Microsoft 365", "Active Directory", "Hardware Troubleshooting", "Networking", "Ticketing Systems", "Remote Support", "Technical Documentation"]
        focus = "user support, systematic troubleshooting, ticket ownership, account administration, and clear documentation"
        experience = [
            "Resolved 120 hardware, software, account, and connectivity tickets using documented diagnostics, ownership, and user confirmation.",
            "Administered Microsoft 365 and Active Directory accounts under least-privilege procedures and recorded 45 auditable changes.",
            "Published 18 knowledge-base articles that reduced repeat requests by 24% and improved first-response consistency.",
        ]
        projects = [
            "Support Knowledge Base - organised 18 solutions for account, device, printer, and network issues with symptoms and verification steps.",
            "Asset and Ticket Tracker - recorded 160 devices, ownership, warranty, ticket priority, status, and resolution history.",
            "Service Desk Improvement Study - categorised 300 tickets, identified the top recurring cause, and measured the effect of a revised triage checklist.",
        ]
        certification = "CompTIA A+ (2025)"
        education_focus = "Information Technology"
        achievement = "Reduced repeat support requests by 24% through concise knowledge-base articles and consistent triage."
    elif any(term in normalized_role for term in ("data analyst", "data scientist", "machine learning", "artificial intelligence", " ai ", "ai engineer", "business intelligence", "research scientist")):
        family = "Data / AI"
        skills = ["Python", "SQL", "Pandas", "NumPy", "Data Cleaning", "Statistics", "Data Visualisation", "Power BI", "Machine Learning", "Model Evaluation"]
        focus = "data preparation, exploratory analysis, visual reporting, reproducible modelling, and measured evaluation"
        experience = [
            "Cleaned an 85,000-row dataset with Python and SQL, documented missing-value and outlier decisions, and validated 27 data-quality checks.",
            "Explored sales trends and built a Power BI dashboard with 12 decision-focused measures, filters, and written interpretation for stakeholders.",
            "Compared 3 baseline and machine-learning models using a held-out test set, cross-validation, precision, recall, F1, and error analysis.",
        ]
        projects = [
            "Sales Insight Dashboard - transformed 85,000 transactions with SQL, defined 12 business measures, and presented trends with data-quality and interpretation notes.",
            "Classification Evaluation Project - trained 3 baseline models, prevented train-test leakage, and reported precision, recall, F1, confusion-matrix errors, and limitations.",
            "Forecasting Investigation - compared 4 approaches against a naive baseline and documented validation design, error metrics, assumptions, and failure cases.",
        ]
        certification = "Microsoft Certified: Power BI Data Analyst Associate (2025)"
        education_focus = "Data Science"
        achievement = "Reduced monthly reporting preparation time by 58% with a reproducible data-cleaning pipeline."
    elif any(term in normalized_role for term in ("software", "developer", "programmer", "web", "frontend", "backend", "full stack", "mobile")):
        family = "Software Development"
        skills = ["Java", "Python", "HTML", "CSS", "JavaScript", "SQL", "Git", "REST API", "Software Testing", "Debugging", "System Design"]
        focus = "software development, database-backed applications, API integration, testing, and maintainable design"
        experience = [
            "Implemented 18 user stories for a database-backed web application, including validation, authentication, and documented REST API integration.",
            "Designed and optimised 9 API endpoints and SQL queries, reducing median response time from 420 ms to 160 ms under a repeatable test workload.",
            "Resolved 27 defects, added unit and integration tests, and increased automated coverage from 46% to 81% before handover.",
        ]
        projects = [
            "Issue Tracker Web App - implemented role-based authentication, CRUD and search workflows, validation, 9 REST endpoints, and automated tests.",
            "Library Management System - designed a relational schema and borrowing workflow, documented API contracts, and verified 32 business-rule test cases.",
            "Team Scheduling Service - built an API with conflict detection, pagination, structured errors, and 41 unit and integration tests in a CI workflow.",
        ]
        certification = "AWS Certified Cloud Practitioner (2025)"
        education_focus = "Software Engineering"
        achievement = "Raised automated test coverage from 46% to 81% while reducing median API response time by 62%."
    else:
        family = "Technology"
        skills = ["Programming Fundamentals", "SQL", "Git", "Testing", "Problem Solving", "Technical Documentation", "Requirements Analysis", "Data Literacy"]
        focus = "technical problem solving, documented requirements, testing, data handling, and collaborative delivery"
        experience = [
            "Translated 16 stakeholder requirements into prioritised acceptance criteria and a tested prototype, documenting assumptions, decisions, and limitations.",
            "Tracked 28 tasks and defects in Git, communicated blockers and progress, and prepared a reproducible technical handover.",
            "Measured a process baseline, implemented one controlled improvement, and reduced completion time from 18 minutes to 7 minutes.",
        ]
        projects = [
            "Technology Portfolio Project - built a solution with versioned source, 34 traceable tests, setup instructions, architecture notes, and documented limitations.",
            "Process Improvement Prototype - mapped an operational workflow, identified 7 pain points, implemented a focused digital improvement, and compared before-and-after measures.",
            "Requirements-to-Test Case Study - converted 16 stakeholder needs into user stories, acceptance tests, and a concise validation report.",
        ]
        certification = "Microsoft Certified: Azure Fundamentals (2025)"
        education_focus = "Information Technology"
        achievement = "Reduced a measured workflow from 18 minutes to 7 minutes after validating a focused process improvement."

    sections = {
        "professional_summary": (
            f"Early-career {role} with hands-on experience in {focus}. "
            "Delivers well-documented work, validates outcomes with measurable evidence, "
            "and collaborates effectively across technical and non-technical teams."
        ),
        "technical_skills": skills,
        "skill_groups": categorize_resume_skills(skills),
        "experience_bullets": experience,
        "projects": projects,
        "certifications": [certification],
        "achievements": [achievement],
        "education": [
            f"Bachelor of Computer Science ({education_focus}) - University of Technology Sarawak (2021 - 2024)."
        ],
        "keyword_targets": [],
    }
    return {"family": family, "sections": sections}


def build_sample_resume_payload(
    analysis: dict[str, Any],
    target_role: str = "",
    best_score: float | None = None,
    *,
    reason_code: str = "auto_detected_top_role_below_20",
    evidence_score: float | None = None,
    generation_threshold: float = CS_SAMPLE_ONLY_TOP_MATCH_THRESHOLD,
    selection_mode: str = "detected_role",
    closest_role: str = "",
) -> dict[str, Any]:
    role = get_resume_generation_role(analysis, target_role) or get_top_role_generation_name(analysis) or "Software Developer"
    template_profile = select_resume_template_profile(role)
    role_recommendation = (
        analysis.get("role_recommendation")
        if isinstance(analysis.get("role_recommendation"), dict)
        else {}
    )
    mapped_database_role = normalize_text(str(role_recommendation.get("selected_database_role") or ""))
    sample_profile = build_sample_resume_role_profile(role)
    sample_profile_basis = role
    if sample_profile["family"] == "Technology" and mapped_database_role:
        mapped_profile = build_sample_resume_role_profile(mapped_database_role)
        if mapped_profile["family"] != "Technology":
            sample_profile = mapped_profile
            sample_profile_basis = mapped_database_role
    sections = sample_profile["sections"]
    completeness = build_section_completeness(sections)
    sample_score = evidence_score if isinstance(evidence_score, (int, float)) else best_score
    score_text = f"{sample_score:.0f}/100" if isinstance(sample_score, (int, float)) else "not available"
    selected_role_sample = reason_code in {
        "selected_role_at_or_below_50",
        "selected_role_review_required",
    }
    no_suitable_role_sample = reason_code == "auto_detected_no_suitable_role"
    if selected_role_sample:
        reason_note = (
            f"EXAMPLE ONLY: the selected {role} role scored {score_text}, below the applicable "
            f"{generation_threshold:.0f}/100 review threshold. The uploaded resume was not rewritten "
            "or presented as belonging to this field."
        )
        sample_reason = (
            f"The selected {role} evidence score is {score_text}, below the applicable "
            f"{generation_threshold:.0f}/100 review threshold."
        )
    elif no_suitable_role_sample:
        closest_text = normalize_text(closest_role) or "the closest database role"
        reason_note = (
            f"EXAMPLE ONLY: no suitable CS role was confirmed. {closest_text} scored {score_text}, below the "
            f"applicable {generation_threshold:.0f}/100 review threshold. {role} was selected from the fallback "
            "learning-example list and is not a job-match recommendation."
        )
        sample_reason = (
            f"No suitable CS role was confirmed; {role} is a deterministic fallback learning example, "
            "not a recommendation for the uploaded resume."
        )
    else:
        reason_note = (
            f"EXAMPLE ONLY: the best automatically detected CS role match was {score_text}, below the "
            f"{generation_threshold:.0f}/100 evidence threshold. The uploaded resume was not rewritten as a CS resume."
        )
        sample_reason = (
            f"The best automatically detected CS role evidence score is {score_text}, below the "
            f"{generation_threshold:.0f}/100 sample threshold."
        )
    draft = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "target_role": role,
        "candidate": {
            "name": "Avery Tan",
            "email": "avery.tan@example.com",
            "phone": "+60 12 555 0148",
            "links": [],
            "link_labels": [],
        },
        "source": {
            "uploaded_resume_name": analysis.get("uploaded_resume_name"),
            "analysis_id": analysis.get("analysis_id"),
            "score": analysis.get("matching_percentage"),
        },
        "sections": sections,
        "completeness": completeness,
        "missing_keywords": [],
        "ats_notes": [
            reason_note,
            f"Use this as a {role} resume structure example, not as a final resume to submit.",
            "To generate a real CS ATS resume, upload a resume with true CS coursework, projects, internships, tools, or certifications.",
            "Replace every sample name, contact, education, project, and achievement with your own verified details.",
        ],
        "template": {
            **template_profile,
            "name": f"Fictional {sample_profile['family']} ATS Resume",
            "experience_heading": "Selected Technical Experience",
            "project_heading": "Projects",
            "certification_heading": "Certifications",
            "achievement_heading": "Achievements",
            "skills_heading": "Technical Skills",
            "education_heading": "Education",
            "inspired_by": [
                "single-column ATS resume structure",
                "compact technical skills grouping",
                "project-focused CS student resume layout",
                "clear education, certification, and achievement sections",
            ],
        },
        "sample_only": True,
        "is_sample_resume": True,
        "sample_reason": sample_reason,
        "sample_reason_code": reason_code,
        "sample_selection_mode": selection_mode,
        "sample_closest_role": normalize_text(closest_role) or None,
        "sample_target_role": role,
        "sample_mapped_database_role": mapped_database_role or None,
        "sample_target_score": sample_score,
        "sample_score_threshold": generation_threshold,
        "sample_role_family": sample_profile["family"],
        "sample_profile_basis_role": sample_profile_basis,
        "sample_metadata": {
            "reason_code": reason_code,
            "target_role": role,
            "mapped_database_role": mapped_database_role or None,
            "profile_basis_role": sample_profile_basis,
            "evidence_score": sample_score,
            "threshold": generation_threshold,
            "comparison": "at_or_below" if selected_role_sample else "below",
            "selection_mode": selection_mode,
            "closest_role": normalize_text(closest_role) or None,
        },
        "generation_threshold": generation_threshold,
        "best_top_role_score": best_score,
    }
    draft["plain_text"] = build_generated_resume_plain_text(draft)
    draft["html"] = build_generated_resume_html(draft)
    return draft


def build_generated_resume_payload(
    analysis: dict[str, Any],
    resume_text: str,
    target_role: str = "",
    candidate_name: str = "",
    contact_links: list[str] | None = None,
) -> dict[str, Any]:
    safe_analysis = analysis or {}
    role = get_resume_generation_role(safe_analysis, target_role)
    sample_decision = get_resume_sample_decision(safe_analysis)
    if sample_decision.get("should_sample"):
        sample_role = normalize_text(str(sample_decision.get("target_role") or role))
        return build_sample_resume_payload(
            safe_analysis,
            sample_role,
            sample_decision.get("best_top_role_score"),
            reason_code=str(sample_decision.get("reason_code") or "auto_detected_top_role_below_20"),
            evidence_score=sample_decision.get("evidence_score"),
            generation_threshold=float(sample_decision.get("threshold") or CS_SAMPLE_ONLY_TOP_MATCH_THRESHOLD),
            selection_mode=str(sample_decision.get("selection_mode") or "detected_role"),
            closest_role=str(sample_decision.get("closest_role") or ""),
        )

    template_profile = select_resume_template_profile(role)
    resume_source = clean_resume_source_text(resume_text or str(safe_analysis.get("resume_text_for_generation") or ""))
    if not resume_source:
        resume_source = normalize_text(str(safe_analysis.get("resume_excerpt") or ""))

    resume_summary = safe_analysis.get("resume_summary") or {}
    role_recommendation = safe_analysis.get("role_recommendation") or {}
    recommendation_module = safe_analysis.get("recommendation_module") if isinstance(safe_analysis.get("recommendation_module"), dict) else {}
    if not recommendation_module and safe_analysis:
        recommendation_module = build_part8_recommendation_module(safe_analysis)
    resume_generation_strategy = (
        recommendation_module.get("resume_generation_strategy")
        if isinstance(recommendation_module.get("resume_generation_strategy"), dict)
        else {}
    )
    job_analysis = safe_analysis.get("job_description_analysis") or {}
    resume_quality = safe_analysis.get("resume_quality") or {}

    source_skills = extract_source_resume_skills(resume_source)
    existing_skills = unique_clean_values(
        list(resume_summary.get("skills") or []) + source_skills,
        24,
    )
    matched_signals = unique_clean_values(
        list(role_recommendation.get("matched_resume_signals") or [])
        + list(job_analysis.get("matched_keywords") or []),
        14,
    )
    missing_keywords = unique_clean_values(
        list(job_analysis.get("missing_keywords") or [])
        + list(role_recommendation.get("recommended_missing_keywords") or []),
        10,
    )
    strategy_keyword_targets = unique_clean_values(list(resume_generation_strategy.get("keyword_targets") or []), 8)
    missing_keywords = unique_clean_values(missing_keywords + strategy_keyword_targets, 10)
    important_keywords = unique_clean_values(list(job_analysis.get("important_keywords") or []), 14)
    skills = unique_clean_values(existing_skills + matched_signals, 28)
    if not skills:
        skills = ["Programming fundamentals", "Problem solving", "Technical documentation"]
    display_skills = unique_clean_values([format_resume_keyword(skill) for skill in skills], 28)
    if ".NET MAUI" in display_skills:
        display_skills = [skill for skill in display_skills if skill != ".NET"]
    display_matched_signals = unique_clean_values([format_resume_keyword(signal) for signal in matched_signals], 12)
    display_missing_keywords = unique_clean_values([format_resume_keyword(keyword) for keyword in missing_keywords], 10)

    contact = extract_resume_contact(resume_source, candidate_name, contact_links)
    filename_name = infer_resume_name_from_filename(str(safe_analysis.get("uploaded_resume_name") or ""))
    current_contact_name = normalize_text(str(contact.get("name") or ""))
    if filename_name and (not current_contact_name or current_contact_name == "Candidate Name"):
        contact["name"] = filename_name
    education_lines = extract_education_lines(resume_source, list(resume_summary.get("education_markers") or []))
    evidence_bullets = extract_resume_evidence_sentences(
        resume_source,
        skills + matched_signals + important_keywords,
        24,
    )
    experience_bullets, fallback_project_bullets = split_project_and_experience_bullets(evidence_bullets)
    project_bullets = build_project_entries(
        resume_source,
        evidence_bullets or fallback_project_bullets,
        6,
    )
    has_professional_experience = has_explicit_experience_section(resume_source)
    if not has_professional_experience:
        experience_bullets = []
    certification_lines = extract_certification_lines(resume_source)
    achievement_lines = extract_achievement_lines(resume_source, evidence_bullets)
    project_keys = {normalize_match_text(project) for project in project_bullets}
    achievement_lines = unique_clean_values(
        [achievement for achievement in achievement_lines if normalize_match_text(achievement) not in project_keys],
        3,
    )

    guidance_notes: list[str] = []
    for keyword in display_missing_keywords[: max(0, 5 - len(experience_bullets))]:
        guidance_notes.append(
            f"Add evidence if you have it: describe a project, coursework item, or work example that truthfully demonstrates {keyword}."
        )
    if len(experience_bullets) < 4:
        guidance_notes.append(
            "If available, add more measurable experience bullets with an action verb, the technical work, and a result or metric."
        )

    if not project_bullets:
        guidance_notes.append("Add a Projects section only if you have a real project; include project name, technologies, your role, and a measurable result.")
    if not certification_lines:
        guidance_notes.append("Add certifications only if you have them; otherwise leave the Certifications section out.")
    if not achievement_lines:
        guidance_notes.append("Add awards or achievements only if they are real; otherwise leave the Achievements section out.")

    sections = {
        "professional_summary": build_generated_summary(
            role,
            display_skills,
            display_matched_signals,
            len(project_bullets),
            has_professional_experience,
        ),
        "technical_skills": display_skills,
        "skill_groups": categorize_resume_skills(display_skills),
        "experience_bullets": experience_bullets[:6],
        "projects": project_bullets[:6],
        "certifications": certification_lines[:3],
        "achievements": achievement_lines[:3],
        "education": education_lines,
        "keyword_targets": display_missing_keywords[:6],
    }
    completeness = build_section_completeness(sections)
    ats_notes = [
        "Part 8 recommendation target: generate the resume for " + role + ".",
        "Draft uses extracted resume evidence. Missing optional content is shown here as guidance instead of inside the resume body.",
        "Only keep a missing keyword if you can support it with a real project, coursework, or work example.",
    ]
    priority_actions = [
        item
        for item in list(recommendation_module.get("priority_actions") or [])
        if isinstance(item, dict)
    ]
    ats_notes.extend(
        unique_clean_values(
            [
                str(item.get("action") or item.get("detail") or "")
                for item in priority_actions[:3]
            ],
            3,
        )
    )
    ats_notes.extend(unique_clean_values(guidance_notes, 8))
    ats_notes.extend(build_limited_info_notes(completeness))
    if display_missing_keywords:
        ats_notes.append("Target keywords to add with evidence: " + ", ".join(display_missing_keywords[:8]) + ".")
    issues = list(resume_quality.get("issues") or [])
    if issues:
        ats_notes.extend(unique_clean_values([str(issue.get("suggestion") or "") for issue in issues], 3))

    draft = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "target_role": role,
        "candidate": contact,
        "source": {
            "uploaded_resume_name": safe_analysis.get("uploaded_resume_name"),
            "analysis_id": safe_analysis.get("analysis_id"),
            "score": safe_analysis.get("matching_percentage"),
        },
        "sections": sections,
        "completeness": completeness,
        "missing_keywords": display_missing_keywords,
        "ats_notes": ats_notes,
        "template": {
            **template_profile,
            "inspired_by": [
                "single-column ATS resume structure",
                "compact technical skills grouping",
                "project-focused CS student resume layout",
                "clear education, certification, and achievement sections",
            ],
        },
        "recommendation_plan": priority_actions[:5],
        "resume_generation_strategy": resume_generation_strategy,
    }
    draft["plain_text"] = build_generated_resume_plain_text(draft)
    draft["html"] = build_generated_resume_html(draft)
    return draft


def record_download_history(user_id: int, analysis_id: int | None, downloaded_file_name: str) -> str:
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        if analysis_id is not None:
            cursor.execute(
                "SELECT id, user_id FROM resume_analyses WHERE id = %s",
                (analysis_id,),
            )
            analysis = cursor.fetchone()
            if not analysis:
                return "not_found"

            owner_id = analysis.get("user_id")
            if owner_id is None or int(owner_id) != int(user_id):
                return "forbidden"

        cursor.execute(
            """
            INSERT INTO download_history (
                user_id,
                analysis_id,
                downloaded_file_name,
                report_type
            ) VALUES (%s, %s, %s, %s)
            """,
            (int(user_id), analysis_id, downloaded_file_name, "resume_comments"),
        )
        connection.commit()
        return "saved"
    except Exception:
        if connection is not None:
            connection.rollback()
        raise
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()


@app.post("/auth/pin/send")
def send_auth_pin():
    payload = request.get_json(silent=True) or {}
    email = normalize_email(payload.get("email", ""))
    purpose = normalize_pin_purpose(payload.get("purpose", "register"))

    if not email:
        return jsonify({"detail": "email is required"}), 400

    if not is_valid_email(email):
        return jsonify({"detail": "Please provide a valid email address"}), 400

    if purpose not in EMAIL_PIN_PURPOSES:
        return jsonify({"detail": "Unsupported PIN purpose"}), 400

    try:
        get_email_pin_delivery_mode()
    except EmailPinConfigurationError:
        return jsonify({"detail": "Email verification is temporarily unavailable"}), 503

    try:
        enforce_email_pin_rate_limits(
            "request",
            email,
            get_request_client_ip(),
            purpose,
        )
        user_already_exists = email_exists(email)
        if purpose == "register" and user_already_exists:
            return jsonify({"detail": "This email is already registered"}), 409
        if purpose == "password_reset" and not user_already_exists:
            return jsonify({"detail": "No account was found for this email address"}), 404

        pin, _expires_at, pin_id = create_email_pin(email, purpose)
        try:
            delivery = send_email_pin(email, pin, purpose)
        except (EmailPinConfigurationError, OSError, smtplib.SMTPException):
            invalidate_email_pin(pin_id)
            raise
        except Exception as error:
            invalidate_email_pin(pin_id)
            raise OSError("Email provider failed while sending a verification PIN") from error
    except EmailPinRateLimitError as error:
        return build_rate_limit_response(error)
    except EmailPinConfigurationError:
        return jsonify({"detail": "Email verification is temporarily unavailable"}), 503
    except DatabaseErrors:
        return jsonify({"detail": "Database error while creating verification PIN"}), 500
    except (OSError, smtplib.SMTPException) as error:
        return jsonify({"detail": get_email_delivery_error_message(error)}), 502

    response: dict[str, Any] = {
        "message": "Verification PIN sent",
        "purpose": purpose,
        "expires_in_minutes": EMAIL_PIN_TTL_MINUTES,
    }
    if should_return_dev_pin(delivery):
        response["dev_pin"] = pin

    return jsonify(response)


@app.post("/users/register")
def register_user():
    payload = request.get_json(silent=True) or {}
    full_name = normalize_text(payload.get("full_name", ""))
    email = normalize_email(payload.get("email", ""))
    password = payload.get("password", "")
    verification_pin = normalize_pin(payload.get("verification_pin", payload.get("pin", "")))

    if not full_name or not email or not password or not verification_pin:
        return jsonify({"detail": "full_name, email, password, and verification_pin are required"}), 400

    if not is_valid_email(email):
        return jsonify({"detail": "Please provide a valid email address"}), 400

    password_error = get_password_validation_error(password)
    if password_error:
        return jsonify({"detail": password_error}), 400

    try:
        if email_exists(email):
            return jsonify({"detail": "This email is already registered"}), 409
        enforce_email_pin_rate_limits(
            "attempt",
            email,
            get_request_client_ip(),
            "register",
        )
        pin_ok, pin_detail = consume_email_pin(email, "register", verification_pin)
        if not pin_ok:
            return jsonify({"detail": pin_detail}), 400
    except EmailPinRateLimitError as error:
        return build_rate_limit_response(error)
    except DatabaseErrors:
        return jsonify({"detail": "Database error while verifying email PIN"}), 500

    hashed_password = generate_password_hash(password)

    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            f"""
            INSERT INTO users (full_name, email, password_hash, email_verified_at, is_admin)
            VALUES (%s, %s, %s, {utc_now_sql()}, %s)
            """ + (" RETURNING id" if is_postgres_backend() else ""),
            (full_name, email, hashed_password, 1 if is_configured_admin_email(email) else 0),
        )
        user_id = int(cursor.fetchone()["id"] if is_postgres_backend() else cursor.lastrowid)
        cursor.execute("SELECT id, full_name, email, email_verified_at, is_admin, created_at FROM users WHERE id = %s", (user_id,))
        created_user = cursor.fetchone()
        if not created_user:
            raise DatabaseError("Created user could not be reloaded")
        connection.commit()
    except DatabaseErrors as error:
        if connection is not None:
            connection.rollback()
        if is_duplicate_entry_error(error):
            return jsonify({"detail": "This email is already registered"}), 409
        return jsonify({"detail": "Database error while registering user"}), 500
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()

    save_login_session(created_user)

    return (
        jsonify(
            {
                "message": "User registered successfully",
                "user": serialize_user(created_user),
            }
        ),
        201,
    )


@app.post("/users/password-reset")
def reset_user_password():
    payload = request.get_json(silent=True) or {}
    email = normalize_email(payload.get("email", ""))
    verification_pin = normalize_pin(payload.get("verification_pin", payload.get("pin", "")))
    new_password = payload.get("new_password", payload.get("password", ""))

    if not email or not verification_pin or not new_password:
        return jsonify({"detail": "email, verification_pin, and new_password are required"}), 400

    if not is_valid_email(email):
        return jsonify({"detail": "Please provide a valid email address"}), 400

    password_error = get_password_validation_error(new_password)
    if password_error:
        return jsonify({"detail": password_error}), 400

    connection = None
    cursor = None
    try:
        if not email_exists(email):
            return jsonify({"detail": "No account was found for this email address"}), 404
        enforce_email_pin_rate_limits(
            "attempt",
            email,
            get_request_client_ip(),
            "password_reset",
        )
        pin_ok, pin_detail = consume_email_pin(email, "password_reset", verification_pin)
        if not pin_ok:
            return jsonify({"detail": pin_detail}), 400

        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute(
            "UPDATE users SET password_hash = %s WHERE email = %s",
            (generate_password_hash(new_password), email),
        )
        connection.commit()
    except EmailPinRateLimitError as error:
        return build_rate_limit_response(error)
    except DatabaseErrors:
        if connection is not None:
            connection.rollback()
        return jsonify({"detail": "Database error while resetting password"}), 500
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()

    return jsonify({"message": "Password reset successfully. You can now log in."})


@app.post("/users/login")
def login_user():
    payload = request.get_json(silent=True) or {}
    email = normalize_email(payload.get("email", ""))
    password = payload.get("password", "")

    if should_enforce_extra_rate_limits():
        try:
            enforce_login_rate_limits(email, get_request_client_ip())
        except RequestRateLimitError as error:
            return build_rate_limit_response(error)

    if not email or not password:
        return jsonify({"detail": "email and password are required"}), 400

    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            "SELECT id, full_name, email, password_hash, email_verified_at, is_admin, created_at FROM users WHERE email = %s",
            (email,),
        )
        user = cursor.fetchone()

        if not user or not check_password_hash(user["password_hash"], password):
            return jsonify({"detail": "Invalid email or password"}), 401

        if is_configured_admin_email(user["email"]) and not user.get("is_admin"):
            cursor.execute("UPDATE users SET is_admin = 1 WHERE id = %s", (user["id"],))
            connection.commit()
            user["is_admin"] = 1
    except DatabaseErrors:
        if connection is not None:
            connection.rollback()
        return jsonify({"detail": "Database error while logging in"}), 500
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()

    save_login_session(user)

    return jsonify(
        {
            "message": "Login successful",
            "user": serialize_user(user),
        }
    )


@app.post("/users/logout")
def logout_user():
    clear_login_session()
    return jsonify({"message": "Logged out successfully"})


@app.get("/auth/session")
def get_current_auth_session():
    """Reconcile browser UI state with the signed server session."""
    try:
        current_user = get_authenticated_session_user()
    except DatabaseErrors:
        return jsonify({"detail": "Database error while validating session"}), 500

    if not current_user:
        response = jsonify({"authenticated": False, "user": None})
        response.headers["Cache-Control"] = "no-store"
        return response

    response = jsonify(
        {
            "authenticated": True,
            "user": serialize_user(current_user),
        }
    )
    response.headers["Cache-Control"] = "no-store"
    return response


def verify_current_user_password(user_id: int, password: str) -> bool:
    """Check the current password against the live account, not the browser session."""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT password_hash FROM users WHERE id = %s", (user_id,))
        row = cursor.fetchone()
        return bool(row and check_password_hash(row["password_hash"], password))
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()


def email_change_target_error(current_user: dict[str, Any], new_email: str) -> str:
    """Keep self-service email changes away from configured administrator identities."""
    current_email = normalize_email(str(current_user.get("email") or ""))
    if is_user_admin(current_user) or is_configured_admin_email(current_email):
        return "Administrator email changes must be handled by an administrator."
    if not is_valid_email(new_email) or len(new_email) > 255:
        return "Please provide a valid email address."
    if new_email == current_email:
        return "Enter a different email address."
    if is_configured_admin_email(new_email):
        return "This email address cannot be used for a self-service change."
    return ""


@app.patch("/users/me")
def update_own_profile():
    current_user, auth_error = authenticate_json_request()
    if auth_error:
        return auth_error

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or "full_name" not in payload or not isinstance(payload["full_name"], str):
        return jsonify({"detail": "Full name is required."}), 400
    full_name = normalize_text(payload["full_name"])
    if not 2 <= len(full_name) <= 100 or re.search(r"[\x00-\x1f\x7f]", full_name):
        return jsonify({"detail": "Full name must contain 2 to 100 valid characters."}), 400

    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute("UPDATE users SET full_name = %s WHERE id = %s", (full_name, current_user["id"]))
        cursor.execute(
            "SELECT id, full_name, email, email_verified_at, is_admin, created_at FROM users WHERE id = %s",
            (current_user["id"],),
        )
        updated_user = cursor.fetchone()
        if not updated_user:
            connection.rollback()
            return jsonify({"detail": "Account not found."}), 404
        connection.commit()
    except DatabaseErrors:
        if connection is not None:
            connection.rollback()
        return jsonify({"detail": "Database error while updating profile."}), 500
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()

    return jsonify({"message": "Name updated.", "user": serialize_user(updated_user)})


@app.post("/users/me/email-change/pin")
def send_own_email_change_pin():
    current_user, auth_error = authenticate_json_request()
    if auth_error:
        return auth_error

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"detail": "New email and current password are required."}), 400
    new_email = normalize_email(str(payload.get("new_email") or ""))
    current_password = payload.get("current_password")
    target_error = email_change_target_error(current_user, new_email)
    if target_error:
        return jsonify({"detail": target_error}), 403 if is_user_admin(current_user) or is_configured_admin_email(str(current_user.get("email") or "")) else 400
    if not isinstance(current_password, str) or not current_password or len(current_password) > 1024:
        return jsonify({"detail": "Current password is required."}), 400

    try:
        get_email_pin_delivery_mode()
        enforce_login_rate_limits(str(current_user["email"]), get_request_client_ip())
        if not verify_current_user_password(int(current_user["id"]), current_password):
            return jsonify({"detail": "Current password is incorrect."}), 401
        if email_exists(new_email):
            return jsonify({"detail": "This email is already registered."}), 409

        purpose = f"email_change_{int(current_user['id'])}"
        enforce_email_pin_rate_limits("request", new_email, get_request_client_ip(), purpose)
        pin, _expires_at, pin_id = create_email_pin(new_email, purpose)
        try:
            delivery = send_email_pin(new_email, pin, purpose)
        except (EmailPinConfigurationError, OSError, smtplib.SMTPException):
            invalidate_email_pin(pin_id)
            raise
        except Exception as error:
            invalidate_email_pin(pin_id)
            raise OSError("Email provider failed while sending a verification PIN") from error
    except (EmailPinRateLimitError, RequestRateLimitError) as error:
        return build_rate_limit_response(error)
    except EmailPinConfigurationError:
        return jsonify({"detail": "Email verification is temporarily unavailable."}), 503
    except DatabaseErrors:
        return jsonify({"detail": "Database error while preparing email change."}), 500
    except (OSError, smtplib.SMTPException) as error:
        return jsonify({"detail": get_email_delivery_error_message(error)}), 502

    response: dict[str, Any] = {
        "message": "Verification PIN sent to your new email address.",
        "expires_in_minutes": EMAIL_PIN_TTL_MINUTES,
    }
    if should_return_dev_pin(delivery):
        response["dev_pin"] = pin
    return jsonify(response)


@app.put("/users/me/email")
def update_own_email():
    current_user, auth_error = authenticate_json_request()
    if auth_error:
        return auth_error

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"detail": "New email, current password, and PIN are required."}), 400
    new_email = normalize_email(str(payload.get("new_email") or ""))
    current_password = payload.get("current_password")
    verification_pin = str(payload.get("verification_pin") or "")
    target_error = email_change_target_error(current_user, new_email)
    if target_error:
        return jsonify({"detail": target_error}), 403 if is_user_admin(current_user) or is_configured_admin_email(str(current_user.get("email") or "")) else 400
    if not isinstance(current_password, str) or not current_password or len(current_password) > 1024:
        return jsonify({"detail": "Current password is required."}), 400
    if not re.fullmatch(r"[0-9]{6}", verification_pin):
        return jsonify({"detail": "Enter the 6-digit verification PIN."}), 400

    try:
        enforce_login_rate_limits(str(current_user["email"]), get_request_client_ip())
        if not verify_current_user_password(int(current_user["id"]), current_password):
            return jsonify({"detail": "Current password is incorrect."}), 401
        if email_exists(new_email):
            return jsonify({"detail": "This email is already registered."}), 409
        enforce_email_pin_rate_limits("attempt", new_email, get_request_client_ip(), "email_change")
        purpose = f"email_change_{int(current_user['id'])}"
        pin_ok, pin_detail = consume_email_pin(new_email, purpose, verification_pin)
        if not pin_ok:
            return jsonify({"detail": pin_detail}), 400
    except (EmailPinRateLimitError, RequestRateLimitError) as error:
        return build_rate_limit_response(error)
    except DatabaseErrors:
        return jsonify({"detail": "Database error while verifying email change."}), 500

    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            f"UPDATE users SET email = %s, email_verified_at = {utc_now_sql()} WHERE id = %s",
            (new_email, current_user["id"]),
        )
        cursor.execute(
            "SELECT id, full_name, email, email_verified_at, is_admin, created_at FROM users WHERE id = %s",
            (current_user["id"],),
        )
        updated_user = cursor.fetchone()
        if not updated_user:
            connection.rollback()
            return jsonify({"detail": "Account not found."}), 404
        connection.commit()
    except DatabaseErrors as error:
        if connection is not None:
            connection.rollback()
        if is_duplicate_entry_error(error):
            return jsonify({"detail": "This email is already registered."}), 409
        return jsonify({"detail": "Database error while updating email."}), 500
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()

    # Preserve the current CSRF token while updating the email held in the session.
    session["email"] = new_email
    return jsonify({"message": "Email updated and verified.", "user": serialize_user(updated_user)})


def normalize_avatar_image(raw_image: bytes) -> bytes:
    """Decode and re-encode an avatar, removing metadata and active file content."""
    if Image is None or ImageOps is None:
        raise RuntimeError("Image processing is unavailable.")
    try:
        with Image.open(BytesIO(raw_image)) as source:
            if source.format not in {"JPEG", "PNG", "WEBP"}:
                raise ValueError("Choose a JPG, PNG, or WebP image.")
            if (
                source.width > MAX_AVATAR_EDGE
                or source.height > MAX_AVATAR_EDGE
                or source.width * source.height > MAX_AVATAR_PIXELS
            ):
                raise ValueError("Image dimensions are too large.")
            source.load()
            image = ImageOps.exif_transpose(source)
            image.thumbnail((AVATAR_OUTPUT_EDGE, AVATAR_OUTPUT_EDGE), Image.Resampling.LANCZOS)
            rgba = image.convert("RGBA")
            normalized = Image.new("RGB", rgba.size, (255, 255, 255))
            normalized.paste(rgba, mask=rgba.getchannel("A"))
            output = BytesIO()
            normalized.save(output, format="JPEG", quality=84, optimize=True)
            return output.getvalue()
    except (OSError, ValueError, Image.DecompressionBombError) as error:
        if isinstance(error, ValueError) and str(error) in {
            "Choose a JPG, PNG, or WebP image.",
            "Image dimensions are too large.",
        }:
            raise
        raise ValueError("Choose a readable JPG, PNG, or WebP image.") from error


@app.get("/users/me/avatar")
def get_own_avatar():
    current_user, auth_error = authenticate_json_request()
    if auth_error:
        return auth_error

    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            "SELECT mime_type, image_base64 FROM user_avatars WHERE user_id = %s",
            (current_user["id"],),
        )
        avatar = cursor.fetchone()
    except DatabaseErrors:
        return jsonify({"detail": "Database error while loading avatar."}), 500
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()

    if not avatar:
        response = jsonify({"detail": "No profile photo uploaded."})
        response.status_code = 404
        response.headers["Cache-Control"] = "no-store"
        return response
    try:
        image_bytes = base64.b64decode(avatar["image_base64"], validate=True)
    except (ValueError, base64.binascii.Error):
        return jsonify({"detail": "Profile photo could not be loaded."}), 500
    response = send_file(BytesIO(image_bytes), mimetype="image/jpeg", download_name="profile-photo.jpg")
    response.headers["Cache-Control"] = "no-store"
    return response


@app.put("/users/me/avatar")
def upload_own_avatar():
    current_user, auth_error = authenticate_json_request()
    if auth_error:
        return auth_error
    if request.content_length is not None and request.content_length > MAX_AVATAR_UPLOAD_BYTES + 65536:
        return jsonify({"detail": "Profile photo must be 2 MB or smaller."}), 413
    upload = request.files.get("avatar")
    if upload is None:
        return jsonify({"detail": "Choose a profile photo to upload."}), 400
    raw_image = upload.stream.read(MAX_AVATAR_UPLOAD_BYTES + 1)
    if not raw_image:
        return jsonify({"detail": "Choose a profile photo to upload."}), 400
    if len(raw_image) > MAX_AVATAR_UPLOAD_BYTES:
        return jsonify({"detail": "Profile photo must be 2 MB or smaller."}), 413
    try:
        image_bytes = normalize_avatar_image(raw_image)
    except RuntimeError:
        return jsonify({"detail": "Image processing is unavailable."}), 503
    except ValueError as error:
        return jsonify({"detail": str(error)}), 400

    encoded = base64.b64encode(image_bytes).decode("ascii")
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        if is_postgres_backend():
            cursor.execute(
                f"""
                INSERT INTO user_avatars (user_id, mime_type, image_base64, updated_at)
                VALUES (%s, %s, %s, {utc_now_sql()})
                ON CONFLICT (user_id) DO UPDATE SET
                    mime_type = EXCLUDED.mime_type,
                    image_base64 = EXCLUDED.image_base64,
                    updated_at = EXCLUDED.updated_at
                """,
                (current_user["id"], "image/jpeg", encoded),
            )
        else:
            cursor.execute(
                f"""
                INSERT INTO user_avatars (user_id, mime_type, image_base64, updated_at)
                VALUES (%s, %s, %s, {utc_now_sql()})
                ON DUPLICATE KEY UPDATE
                    mime_type = VALUES(mime_type),
                    image_base64 = VALUES(image_base64),
                    updated_at = VALUES(updated_at)
                """,
                (current_user["id"], "image/jpeg", encoded),
            )
        connection.commit()
    except DatabaseErrors:
        if connection is not None:
            connection.rollback()
        return jsonify({"detail": "Database error while saving avatar."}), 500
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()

    return jsonify({"message": "Profile photo updated."})


@app.delete("/users/me/avatar")
def delete_own_avatar():
    current_user, auth_error = authenticate_json_request()
    if auth_error:
        return auth_error

    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute("DELETE FROM user_avatars WHERE user_id = %s", (current_user["id"],))
        connection.commit()
    except DatabaseErrors:
        if connection is not None:
            connection.rollback()
        return jsonify({"detail": "Database error while removing avatar."}), 500
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()

    return jsonify({"message": "Profile photo removed."})


def build_admin_dashboard_payload() -> dict[str, Any]:
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT id, full_name, email, email_verified_at, is_admin, created_at
            FROM users
            ORDER BY created_at DESC
            """
        )
        user_rows = cursor.fetchall()
        cursor.execute(
            """
            SELECT
                ra.id,
                ra.user_id,
                ra.uploaded_resume_name,
                ra.target_role,
                ra.matching_percentage,
                ra.analysis_json,
                ra.created_at,
                u.full_name,
                u.email
            FROM resume_analyses ra
            LEFT JOIN users u ON u.id = ra.user_id
            ORDER BY ra.created_at DESC
            LIMIT 250
            """
        )
        analysis_rows = cursor.fetchall()
        cursor.execute("SELECT COUNT(*) AS count FROM download_history")
        download_row = cursor.fetchone() or {}
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()

    user_stats: dict[int, dict[str, Any]] = {}
    role_counter: Counter[str] = Counter()
    score_values: list[float] = []
    estimated_after_values: list[float] = []
    recent_evaluations: list[dict[str, Any]] = []

    for row in analysis_rows:
        before_score = clamp_admin_score(row.get("matching_percentage"))
        analysis = parse_stored_analysis_json(row.get("analysis_json"))
        estimated_after = estimate_after_recommendation_score(before_score, analysis)
        gain = round(max(0.0, estimated_after - before_score), 2)
        user_id = row.get("user_id")
        if user_id is not None:
            safe_user_id = int(user_id)
            stats = user_stats.setdefault(
                safe_user_id,
                {
                    "evaluation_count": 0,
                    "latest_score": None,
                    "latest_estimated_after_score": None,
                    "last_evaluation_at": None,
                },
            )
            stats["evaluation_count"] += 1
            if not stats["last_evaluation_at"]:
                stats["latest_score"] = before_score
                stats["latest_estimated_after_score"] = estimated_after
                stats["last_evaluation_at"] = isoformat_datetime(row.get("created_at"))

        target_role = normalize_text(str(row.get("target_role") or "Unknown role"))
        if target_role:
            role_counter[target_role] += 1
        score_values.append(before_score)
        estimated_after_values.append(estimated_after)
        if len(recent_evaluations) < 40:
            recent_evaluations.append(
                {
                    "id": row["id"],
                    "user_id": user_id,
                    "user_name": row.get("full_name") or "Deleted user",
                    "user_email": row.get("email") or "",
                    "uploaded_resume_name": row.get("uploaded_resume_name") or "resume.pdf",
                    "target_role": target_role,
                    "before_score": before_score,
                    "estimated_after_score": estimated_after,
                    "estimated_gain": gain,
                    "created_at": isoformat_datetime(row.get("created_at")),
                }
            )

    users: list[dict[str, Any]] = []
    for row in user_rows:
        user_id = int(row["id"])
        stats = user_stats.get(
            user_id,
            {
                "evaluation_count": 0,
                "latest_score": None,
                "latest_estimated_after_score": None,
                "last_evaluation_at": None,
            },
        )
        users.append(
            {
                **serialize_user(row),
                "evaluation_count": stats["evaluation_count"],
                "latest_score": stats["latest_score"],
                "latest_estimated_after_score": stats["latest_estimated_after_score"],
                "last_evaluation_at": stats["last_evaluation_at"],
            }
        )

    total_evaluations = len(score_values)
    average_before = round(sum(score_values) / total_evaluations, 2) if total_evaluations else 0.0
    average_after = round(sum(estimated_after_values) / total_evaluations, 2) if total_evaluations else 0.0
    estimated_improved = sum(1 for before, after in zip(score_values, estimated_after_values) if after > before)
    strong_scores = sum(1 for score in score_values if score >= 80)
    fair_scores = sum(1 for score in score_values if 60 <= score < 80)
    weak_scores = sum(1 for score in score_values if score < 60)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "registered_users": len(users),
            "resume_evaluations": total_evaluations,
            "evaluating_users": len(user_stats),
            "download_records": int(download_row.get("count") or 0),
            "average_before_score": average_before,
            "estimated_average_after_score": average_after,
            "average_estimated_gain": round(max(0.0, average_after - average_before), 2),
            "estimated_improved_evaluations": estimated_improved,
            "estimated_improvement_rate": round((estimated_improved / total_evaluations) * 100, 2) if total_evaluations else 0.0,
        },
        "score_distribution": [
            {"label": "Strong 80-100", "value": strong_scores},
            {"label": "Fair 60-79", "value": fair_scores},
            {"label": "Needs Work 0-59", "value": weak_scores},
        ],
        "top_evaluated_roles": [
            {"label": role, "value": count}
            for role, count in role_counter.most_common(12)
        ],
        "recent_evaluations": recent_evaluations,
        "users": users,
    }


@app.get("/users")
def list_users():
    admin_error = require_admin_json_response()
    if admin_error:
        return admin_error

    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT id, full_name, email, email_verified_at, is_admin, created_at FROM users ORDER BY created_at DESC")
        rows = cursor.fetchall()
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()

    users = [serialize_user(row) for row in rows]
    return jsonify({"count": len(users), "users": users})


@app.get("/admin/dashboard-insights")
def get_admin_dashboard_insights():
    admin_error = require_admin_json_response()
    if admin_error:
        return admin_error
    try:
        return jsonify(build_admin_dashboard_payload())
    except DatabaseErrors:
        return jsonify({"detail": "Database error while building admin dashboard"}), 500


@app.put("/admin/users/<int:user_id>")
def update_admin_user(user_id: int):
    admin_error = require_admin_json_response()
    if admin_error:
        return admin_error

    payload = request.get_json(silent=True) or {}
    updates: list[str] = []
    values: list[Any] = []
    new_full_name = normalize_text(str(payload.get("full_name", ""))) if "full_name" in payload else None
    new_email = normalize_email(str(payload.get("email", ""))) if "email" in payload else None
    new_password = str(payload.get("new_password", ""))
    has_admin_value = "is_admin" in payload
    requested_admin = bool(payload.get("is_admin")) if has_admin_value else None

    if new_full_name is not None:
        if not new_full_name:
            return jsonify({"detail": "Full name is required"}), 400
        updates.append("full_name = %s")
        values.append(new_full_name)

    if new_email is not None:
        if not is_valid_email(new_email):
            return jsonify({"detail": "Please provide a valid email address"}), 400
        updates.append("email = %s")
        values.append(new_email)

    if new_password:
        password_error = get_password_validation_error(new_password)
        if password_error:
            return jsonify({"detail": password_error}), 400
        updates.append("password_hash = %s")
        values.append(generate_password_hash(new_password))

    if new_email is not None and is_configured_admin_email(new_email):
        requested_admin = True
        has_admin_value = True

    if has_admin_value:
        if int(session.get("user_id") or 0) == user_id and not requested_admin:
            return jsonify({"detail": "You cannot remove admin access from your current session"}), 400
        updates.append("is_admin = %s")
        values.append(1 if requested_admin else 0)

    if not updates:
        return jsonify({"detail": "No user fields were provided"}), 400

    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT id FROM users WHERE id = %s", (user_id,))
        if not cursor.fetchone():
            return jsonify({"detail": "User not found"}), 404
        values.append(user_id)
        cursor.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = %s", tuple(values))
        cursor.execute("SELECT id, full_name, email, email_verified_at, is_admin, created_at FROM users WHERE id = %s", (user_id,))
        updated_user = cursor.fetchone()
        if not updated_user:
            raise DatabaseError("Updated user could not be reloaded")
        connection.commit()
    except DatabaseErrors as error:
        if connection is not None:
            connection.rollback()
        if is_duplicate_entry_error(error):
            return jsonify({"detail": "This email is already registered"}), 409
        return jsonify({"detail": "Database error while updating user"}), 500
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()

    return jsonify({"message": "User updated", "user": serialize_user(updated_user)})


@app.delete("/admin/users/<int:user_id>")
def delete_admin_user(user_id: int):
    admin_error = require_admin_json_response()
    if admin_error:
        return admin_error
    if int(session.get("user_id") or 0) == user_id:
        return jsonify({"detail": "You cannot delete the admin account you are currently using"}), 400

    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT id FROM users WHERE id = %s", (user_id,))
        if not cursor.fetchone():
            return jsonify({"detail": "User not found"}), 404
        cursor.execute(
            """
            DELETE FROM download_history
            WHERE user_id = %s
               OR analysis_id IN (
                    SELECT id FROM resume_analyses WHERE user_id = %s
               )
            """,
            (user_id, user_id),
        )
        cursor.execute("DELETE FROM resume_analyses WHERE user_id = %s", (user_id,))
        cursor.execute("DELETE FROM users WHERE id = %s", (user_id,))
        connection.commit()
    except DatabaseErrors:
        if connection is not None:
            connection.rollback()
        return jsonify({"detail": "Database error while deleting user"}), 500
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()

    return jsonify({"message": "User deleted"})


@app.get("/admin/analyses/<int:analysis_id>")
def get_admin_analysis_report(analysis_id: int):
    """Return any user's saved report for administrator review."""
    admin_error = require_admin_json_response()
    if admin_error:
        return admin_error

    connection = None
    cursor = None
    file_row = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT
                ra.id,
                ra.uploaded_resume_name,
                ra.target_role,
                ra.matching_percentage,
                ra.resume_excerpt,
                ra.analysis_json,
                ra.created_at,
                u.full_name,
                u.email
            FROM resume_analyses ra
            LEFT JOIN users u ON u.id = ra.user_id
            WHERE ra.id = %s
            """,
            (analysis_id,),
        )
        row = cursor.fetchone()
        if row and ensure_resume_file_table(connection):
            cursor.execute(
                "SELECT file_name, content_type, file_size FROM resume_files WHERE analysis_id = %s",
                (analysis_id,),
            )
            file_row = cursor.fetchone()
    except DatabaseErrors:
        return jsonify({"detail": "Database error while loading the saved analysis"}), 500
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()

    if not row:
        return jsonify({"detail": "Analysis not found"}), 404

    analysis = parse_stored_analysis_json(row.get("analysis_json"))
    if not analysis.get("resume_excerpt") and row.get("resume_excerpt"):
        analysis["resume_excerpt"] = str(row["resume_excerpt"])
    response = jsonify(
        {
            "analysis_id": int(row["id"]),
            "user_name": row.get("full_name") or "Deleted user",
            "user_email": row.get("email") or "",
            "uploaded_resume_name": row.get("uploaded_resume_name") or "resume.pdf",
            "target_role": normalize_text(str(row.get("target_role") or "")),
            "score": clamp_admin_score(row.get("matching_percentage")),
            "created_at": isoformat_datetime(row.get("created_at")),
            "resume_file": (
                {
                    "file_name": file_row["file_name"],
                    "content_type": file_row["content_type"],
                    "file_size": int(file_row["file_size"]),
                }
                if file_row
                else None
            ),
            "analysis": analysis,
        }
    )
    response.headers["Cache-Control"] = "private, no-store"
    return response


@app.get("/admin/analyses/<int:analysis_id>/resume-file")
def get_admin_resume_file(analysis_id: int):
    """Send a user's original upload to an administrator as a download."""
    admin_error = require_admin_json_response()
    if admin_error:
        return admin_error

    connection = None
    cursor = None
    row = None
    try:
        connection = get_db_connection()
        if ensure_resume_file_table(connection):
            cursor = connection.cursor(dictionary=True)
            cursor.execute(
                "SELECT file_name, content_type, file_data FROM resume_files WHERE analysis_id = %s",
                (analysis_id,),
            )
            row = cursor.fetchone()
    except DatabaseErrors:
        return jsonify({"detail": "Database error while loading the resume file"}), 500
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()

    if not row:
        return jsonify({"detail": "The original file was not stored for this evaluation."}), 404

    content_type = row["content_type"]
    if content_type not in RESUME_FILE_CONTENT_TYPES.values():
        content_type = "application/octet-stream"
    response = send_file(
        BytesIO(bytes(row["file_data"])),
        mimetype=content_type,
        as_attachment=True,
        download_name=row["file_name"] or "resume",
        max_age=0,
    )
    response.headers["Cache-Control"] = "private, no-store"
    return response


@app.get("/analyses")
def list_resume_analyses():
    try:
        current_user = get_authenticated_session_user()
    except DatabaseErrors:
        return jsonify({"detail": "Database error while loading analysis history"}), 500

    if not current_user:
        return jsonify({"detail": "Authentication required"}), 401

    requested_email = normalize_email(request.args.get("email", ""))
    current_email = normalize_email(str(current_user.get("email") or ""))
    live_is_admin = is_user_admin(current_user)
    target_user_id = int(current_user["id"])

    if requested_email and requested_email != current_email and not live_is_admin:
        return jsonify({"detail": "You can only view your own analysis history"}), 403

    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        if requested_email and requested_email != current_email:
            if not is_valid_email(requested_email):
                return jsonify({"detail": "Please provide a valid email address"}), 400

            cursor.execute("SELECT id FROM users WHERE email = %s", (requested_email,))
            target_user = cursor.fetchone()
            if not target_user:
                return jsonify({"detail": "User not found"}), 404
            target_user_id = int(target_user["id"])

        cursor.execute(
            """
            SELECT
                id,
                uploaded_resume_name,
                target_role,
                matching_percentage,
                feedback,
                resume_excerpt,
                created_at
            FROM resume_analyses
            WHERE user_id = %s
            ORDER BY created_at DESC
            """,
            (target_user_id,),
        )
        rows = cursor.fetchall()
    except DatabaseErrors:
        return jsonify({"detail": "Database error while loading analysis history"}), 500
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()

    analyses = []
    for row in rows:
        analyses.append(
            {
                "id": row["id"],
                "uploaded_resume_name": row["uploaded_resume_name"],
                "target_role": row["target_role"],
                "matching_percentage": float(row["matching_percentage"]),
                "feedback": row["feedback"],
                "resume_excerpt": row["resume_excerpt"],
                "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            }
        )

    response = jsonify({"count": len(analyses), "analyses": analyses})
    response.headers["Cache-Control"] = "private, no-store"
    return response


@app.get("/analyses/<int:analysis_id>")
def get_saved_resume_analysis(analysis_id: int):
    """Restore a saved report for its owner, including on a second device."""
    try:
        current_user = get_authenticated_session_user()
    except DatabaseErrors:
        return jsonify({"detail": "Database error while validating session"}), 500

    if not current_user:
        return jsonify({"detail": "Authentication required"}), 401

    try:
        ownership_status, analysis = load_owned_analysis_snapshot(int(current_user["id"]), analysis_id)
    except DatabaseErrors:
        return jsonify({"detail": "Database error while loading the saved analysis"}), 500

    # Return the same response for missing and foreign IDs so report IDs do not
    # reveal whether another account has an analysis.
    if ownership_status in {"not_found", "forbidden"}:
        return jsonify({"detail": "Analysis not found"}), 404
    if ownership_status != "owned" or not analysis:
        return jsonify({"detail": "The saved analysis is unavailable or invalid"}), 409

    analysis["history_saved"] = True
    response = jsonify({"analysis": analysis})
    response.headers["Cache-Control"] = "private, no-store"
    return response


@app.delete("/analyses/<int:analysis_id>")
def delete_saved_resume_analysis(analysis_id: int):
    """Remove one owned report and its download records."""
    try:
        current_user = get_authenticated_session_user()
    except DatabaseErrors:
        return jsonify({"detail": "Database error while validating session"}), 500
    if not current_user:
        return jsonify({"detail": "Authentication required"}), 401

    user_id = int(current_user["id"])
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute(
            "SELECT id FROM resume_analyses WHERE id = %s AND user_id = %s",
            (analysis_id, user_id),
        )
        if not cursor.fetchone():
            return jsonify({"detail": "Analysis not found"}), 404

        cursor.execute(
            "DELETE FROM download_history WHERE analysis_id = %s AND user_id = %s",
            (analysis_id, user_id),
        )
        cursor.execute(
            "DELETE FROM resume_analyses WHERE id = %s AND user_id = %s",
            (analysis_id, user_id),
        )
        connection.commit()
    except DatabaseErrors:
        if connection is not None:
            connection.rollback()
        return jsonify({"detail": "Database error while deleting the saved analysis"}), 500
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()

    response = jsonify({"deleted": True, "id": analysis_id})
    response.headers["Cache-Control"] = "private, no-store"
    return response


@app.delete("/analyses")
def clear_saved_resume_analyses():
    """Remove all reports owned by the current user."""
    try:
        current_user = get_authenticated_session_user()
    except DatabaseErrors:
        return jsonify({"detail": "Database error while validating session"}), 500
    if not current_user:
        return jsonify({"detail": "Authentication required"}), 401

    user_id = int(current_user["id"])
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute(
            """
            DELETE FROM download_history
            WHERE user_id = %s
              AND analysis_id IN (SELECT id FROM resume_analyses WHERE user_id = %s)
            """,
            (user_id, user_id),
        )
        cursor.execute("DELETE FROM resume_analyses WHERE user_id = %s", (user_id,))
        deleted_count = cursor.rowcount
        connection.commit()
    except DatabaseErrors:
        if connection is not None:
            connection.rollback()
        return jsonify({"detail": "Database error while clearing analysis history"}), 500
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()

    response = jsonify({"deleted_count": deleted_count})
    response.headers["Cache-Control"] = "private, no-store"
    return response


@app.post("/downloads")
def create_download_history():
    try:
        current_user = get_authenticated_session_user()
    except DatabaseErrors:
        return jsonify({"detail": "Database error while validating session"}), 500

    if not current_user:
        return jsonify({"detail": "Authentication required"}), 401

    payload = request.get_json(silent=True) or {}
    raw_analysis_id = payload.get("analysis_id")
    downloaded_file_name = normalize_text(
        payload.get("downloaded_file_name", "uts-cs-career-navigator-comments.txt")
    ) or "uts-cs-career-navigator-comments.txt"

    if raw_analysis_id is None or (isinstance(raw_analysis_id, str) and not raw_analysis_id.strip()):
        safe_analysis_id = None
    elif isinstance(raw_analysis_id, bool):
        return jsonify({"detail": "analysis_id must be a positive integer or null"}), 400
    elif isinstance(raw_analysis_id, int):
        safe_analysis_id = raw_analysis_id
    elif isinstance(raw_analysis_id, str) and raw_analysis_id.strip().isdigit():
        safe_analysis_id = int(raw_analysis_id.strip())
    else:
        return jsonify({"detail": "analysis_id must be a positive integer or null"}), 400

    if safe_analysis_id is not None and safe_analysis_id <= 0:
        return jsonify({"detail": "analysis_id must be a positive integer or null"}), 400

    try:
        outcome = record_download_history(
            int(current_user["id"]),
            safe_analysis_id,
            downloaded_file_name,
        )
    except DatabaseErrors:
        return jsonify({"detail": "Database error while saving download history"}), 500

    if outcome == "not_found":
        return jsonify({"detail": "Analysis not found"}), 404
    if outcome == "forbidden":
        return jsonify({"detail": "You cannot record a download for another user's analysis"}), 403

    return jsonify({"message": "Download history saved"})


def get_generated_resume_contact_links(payload: dict[str, Any]) -> list[str]:
    raw_contact_links = payload.get("contact_links") if isinstance(payload.get("contact_links"), list) else []
    return unique_clean_values(
        [
            normalize_provider_resume_link(str(payload.get("linkedin_url") or ""), "linkedin.com"),
            normalize_provider_resume_link(str(payload.get("github_url") or ""), "github.com"),
            normalize_resume_link(str(payload.get("portfolio_url") or "")),
            *[normalize_resume_link(str(link or "")) for link in raw_contact_links],
        ],
        5,
    )


def parse_generation_analysis_id(payload: dict[str, Any], client_analysis: dict[str, Any]) -> int:
    raw_analysis_id = payload.get("analysis_id", client_analysis.get("analysis_id"))
    if isinstance(raw_analysis_id, bool):
        raise ValueError("analysis_id must be a positive integer")
    try:
        analysis_id = int(raw_analysis_id)
    except (TypeError, ValueError):
        raise ValueError("analysis_id is required to generate a resume") from None
    if analysis_id <= 0:
        raise ValueError("analysis_id must be a positive integer")
    return analysis_id


def load_owned_analysis_snapshot(user_id: int, analysis_id: int) -> tuple[str, dict[str, Any] | None]:
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            "SELECT user_id, analysis_json, resume_excerpt FROM resume_analyses WHERE id = %s",
            (analysis_id,),
        )
        row = cursor.fetchone()
        if not row:
            return "not_found", None

        owner_id = row.get("user_id")
        if owner_id is None or int(owner_id) != int(user_id):
            return "forbidden", None

        try:
            analysis = json.loads(str(row.get("analysis_json") or ""))
        except (TypeError, ValueError, json.JSONDecodeError):
            return "invalid", None
        if not isinstance(analysis, dict):
            return "invalid", None

        analysis["analysis_id"] = analysis_id
        if not analysis.get("resume_excerpt") and row.get("resume_excerpt"):
            analysis["resume_excerpt"] = str(row["resume_excerpt"])
        return "owned", analysis
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()


def build_owned_generated_resume(
    payload: dict[str, Any],
    current_user: dict[str, Any],
) -> tuple[dict[str, Any] | None, Any | None]:
    client_analysis = payload.get("analysis") if isinstance(payload.get("analysis"), dict) else {}
    try:
        analysis_id = parse_generation_analysis_id(payload, client_analysis)
    except ValueError as error:
        return None, (jsonify({"detail": str(error)}), 400)

    try:
        ownership_status, analysis = load_owned_analysis_snapshot(int(current_user["id"]), analysis_id)
    except DatabaseErrors:
        return None, (jsonify({"detail": "Database error while loading the saved analysis"}), 500)

    if ownership_status == "not_found":
        return None, (jsonify({"detail": "Analysis not found"}), 404)
    if ownership_status == "forbidden":
        return None, (jsonify({"detail": "You cannot generate a resume from another user's analysis"}), 403)
    if ownership_status != "owned" or not analysis:
        return None, (jsonify({"detail": "The saved analysis is unavailable or invalid; run the analysis again"}), 409)

    resume_text = clean_resume_source_text(
        str(
            payload.get("resume_text")
            or client_analysis.get("resume_text_for_generation")
            or analysis.get("resume_excerpt")
            or ""
        )
    )
    target_role = normalize_text(str(payload.get("target_role") or ""))
    candidate_name = normalize_text(str(payload.get("candidate_name") or current_user.get("full_name") or ""))
    contact_links = get_generated_resume_contact_links(payload)
    generated_resume = build_generated_resume_payload(
        analysis,
        resume_text[:RESUME_GENERATION_MAX_TEXT_LENGTH],
        target_role,
        candidate_name,
        contact_links,
    )
    return generated_resume, None


@app.post("/generate-resume")
def generate_ats_resume():
    current_user, auth_error = authenticate_json_request()
    if auth_error is not None:
        return auth_error

    payload = request.get_json(silent=True) or {}
    generated_resume, generation_error = build_owned_generated_resume(payload, current_user or {})
    if generation_error is not None:
        return generation_error
    return jsonify(generated_resume)


@app.post("/generate-resume-pdf")
def generate_ats_resume_pdf():
    current_user, auth_error = authenticate_json_request()
    if auth_error is not None:
        return auth_error

    payload = request.get_json(silent=True) or {}
    draft, generation_error = build_owned_generated_resume(payload, current_user or {})
    if generation_error is not None:
        return generation_error

    try:
        pdf_buffer = build_generated_resume_pdf_buffer(draft)
    except RuntimeError as error:
        return jsonify({"detail": str(error)}), 500

    role_slug = re.sub(r"[^a-z0-9]+", "-", normalize_match_text(str(draft.get("target_role") or "ats-resume"))).strip("-")
    file_prefix = "uts-example-only-resume" if draft.get("sample_only") or draft.get("is_sample_resume") else "uts-ats-resume"
    file_name = f"{file_prefix}-{role_slug or 'draft'}.pdf"
    return send_file(
        pdf_buffer,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=file_name,
    )


@app.post("/feedback-report-pdf")
def generate_feedback_report_pdf():
    payload = request.get_json(silent=True) or {}
    report = payload.get("report") if isinstance(payload.get("report"), dict) else payload
    if not isinstance(report, dict):
        return jsonify({"detail": "report payload is required"}), 400

    try:
        pdf_buffer = build_feedback_report_pdf_buffer(report)
    except RuntimeError as error:
        return jsonify({"detail": str(error)}), 500

    role_slug = re.sub(
        r"[^a-z0-9]+",
        "-",
        normalize_match_text(str(report.get("recommendedRole") or report.get("selectedRole") or "feedback-report")),
    ).strip("-")
    file_name = f"uts-feedback-report-{role_slug or 'resume'}.pdf"
    return send_file(
        pdf_buffer,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=file_name,
    )


@app.get("/job-roles")
def list_job_roles():
    try:
        roles = fetch_job_roles()
    except DatabaseErrors:
        return jsonify({"detail": "Database error while loading job roles"}), 500

    return jsonify({"count": len(roles), "roles": roles})


@app.get("/job-requirements")
def get_job_requirements():
    requested_role = normalize_text(request.args.get("role", ""))
    role_id_raw = request.args.get("role_id", "")

    try:
        role_id = int(role_id_raw) if role_id_raw else None
    except (TypeError, ValueError):
        role_id = None

    try:
        matched_role: dict[str, Any] | None = None
        match_type = "none"
        if role_id:
            roles = fetch_job_roles()
            matched_role = next((role for role in roles if int(role["id"]) == role_id), None)
            match_type = "id" if matched_role else "none"
        elif requested_role:
            matched_role, match_type = find_job_role_match(requested_role)
        else:
            return jsonify({"detail": "role or role_id is required"}), 400

        if not matched_role:
            return jsonify({"matched": False, "requested_role": requested_role, "match_type": match_type})

        requirements = fetch_job_requirements(int(matched_role["id"]))
    except DatabaseErrors:
        return jsonify({"detail": "Database error while loading job requirements"}), 500

    return jsonify(
        {
            "matched": True,
            "requested_role": requested_role,
            "match_type": match_type,
            "role": matched_role,
            "requirements": requirements,
            "grouped_requirements": group_job_requirements(requirements),
            "job_description": build_job_description_from_requirements(matched_role, requirements),
        }
    )


@app.get("/job-data-cleaning/report")
def get_job_data_cleaning_report():
    admin_error = require_admin_json_response()
    if admin_error:
        return admin_error

    try:
        role_rows, requirement_rows = fetch_raw_job_data_rows()
        report = build_job_data_quality_report(role_rows, requirement_rows)
    except DatabaseErrors:
        return jsonify({"detail": "Database error while building job data cleaning report"}), 500

    return jsonify(report)


@app.post("/job-data-cleaning/run")
def run_job_data_cleaning():
    admin_error = require_admin_json_response()
    if admin_error:
        return admin_error

    payload = request.get_json(silent=True) or {}
    apply_changes = bool(payload.get("apply"))

    try:
        role_rows, requirement_rows = fetch_raw_job_data_rows()
        plan = build_job_data_cleaning_plan(role_rows, requirement_rows)
        report_before = build_job_data_quality_report(role_rows, requirement_rows)

        if not apply_changes:
            return jsonify(
                {
                    "applied": False,
                    "message": "Dry run only. Send {\"apply\": true} to update database rows.",
                    "report": report_before,
                    "plan_summary": plan["summary"],
                }
            )

        applied_summary = apply_job_data_cleaning_plan(plan)
        cleaned_role_rows, cleaned_requirement_rows = fetch_raw_job_data_rows()
        report_after = build_job_data_quality_report(cleaned_role_rows, cleaned_requirement_rows)
    except DatabaseErrors:
        return jsonify({"detail": "Database error while running job data cleaning"}), 500

    return jsonify(
        {
            "applied": True,
            "message": "Job data cleaning completed.",
            "applied_summary": applied_summary,
            "report_before": report_before,
            "report_after": report_after,
        }
    )


_job_data_insights_cache: tuple[object, dict[str, Any]] | None = None


def get_cached_job_data_insights() -> dict[str, Any]:
    """Build the dataset insights once per loaded job catalogue."""
    global _job_data_insights_cache
    catalog_version = get_job_catalog_version()
    cached = _job_data_insights_cache
    if cached is not None and cached[0] is catalog_version:
        return cached[1]
    roles, requirements_by_role = fetch_job_data_for_insights()
    insights = build_job_data_insights(
        roles,
        requirements_by_role,
        skill_vocabulary=DEFAULT_SKILL_DICTIONARY,
        stopwords=KEYWORD_STOPWORDS,
    )
    _job_data_insights_cache = (catalog_version, insights)
    return insights


@app.get("/job-data-insights")
def get_job_data_insights():
    admin_error = require_admin_json_response()
    if admin_error:
        return admin_error

    try:
        insights = get_cached_job_data_insights()
    except DatabaseErrors:
        return jsonify({"detail": "Database error while building job data insights"}), 500

    return jsonify(insights)


@app.get("/health")
def health_check():
    health = {"status": "ok", "service": APP_TITLE}
    if not is_production_environment():
        auto_export_enabled = get_accuracy_testing_auto_export_enabled()
        health["accuracy_testing_auto_export"] = auto_export_enabled
        if auto_export_enabled:
            health.update(
                {
                    "accuracy_testing_workbook": ACCURACY_TESTING_WORKBOOK,
                    "accuracy_testing_workbook_exists": os.path.exists(ACCURACY_TESTING_WORKBOOK),
                    "accuracy_testing_pending_count": len(load_pending_accuracy_testing_analyses()),
                }
            )
    return jsonify(health)


def serve_frontend_page(page_name: str):
    response = send_from_directory(FRONTEND_DIR, page_name)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@app.get("/")
def index():
    return serve_frontend_page("home_page.html")


@app.get("/home_page.html")
def home_page():
    return serve_frontend_page("home_page.html")


@app.get("/analysis_result.html")
def analysis_result_page():
    return serve_frontend_page("analysis_result.html")


@app.get("/job_data_insights.html")
def job_data_insights_page():
    try:
        current_user = get_authenticated_session_user()
    except DatabaseErrors:
        return jsonify({"detail": "Database error while validating administrator access"}), 500

    if not current_user or not is_user_admin(current_user):
        return redirect("/home_page.html?admin_required=1")
    return serve_frontend_page("job_data_insights.html")


@app.get("/app_common.js")
def app_common_script():
    return serve_frontend_page("app_common.js")


@app.get("/runtime_config.js")
def runtime_config_script():
    return serve_frontend_page("runtime_config.js")


@app.get("/tailwind.css")
def tailwind_stylesheet():
    return serve_frontend_page("tailwind.css")


@app.get("/account_workspace.css")
def account_workspace_stylesheet():
    return serve_frontend_page("account_workspace.css")


@app.get("/api")
def api_index():
    return jsonify(
        {
            "service": APP_TITLE,
            "status": "running",
            "endpoints": [
                "GET /health",
                "POST /analyze-file",
                "POST /auth/pin/send",
                "POST /users/register",
                "POST /users/login",
                "POST /users/logout",
                "GET /auth/session",
                "POST /users/password-reset",
                "GET /users",
                "GET /admin/dashboard-insights",
                "PUT /admin/users/<user_id>",
                "DELETE /admin/users/<user_id>",
                "GET /analyses",
                "GET /analyses/<analysis_id>",
                "DELETE /analyses/<analysis_id>",
                "DELETE /analyses",
                "POST /downloads",
                "POST /generate-resume",
                "POST /generate-resume-pdf",
                "POST /feedback-report-pdf",
                "GET /job-roles",
                "GET /job-requirements",
                "GET /job-data-cleaning/report",
                "POST /job-data-cleaning/run",
                "GET /job-data-insights",
            ],
        }
    )


@app.post("/analyze-file")
def analyze_file():
    # One scoring cache for the whole request: ranking, the recommended-role fit
    # and the report reuse each other's results, and nothing outlives the request.
    with scoring_cache_scope():
        return _analyze_file()


def _analyze_file():
    started_at = perf_counter()
    phase_started_at = started_at
    phase_timings: list[str] = []

    def mark_phase(name: str) -> None:
        nonlocal phase_started_at
        now = perf_counter()
        phase_timings.append(f"{name};dur={(now - phase_started_at) * 1000:.2f}")
        phase_started_at = now

    try:
        current_user = get_authenticated_session_user()
    except DatabaseErrors:
        return jsonify({"detail": "Database error while validating session"}), 500

    if not current_user:
        return jsonify({"detail": "Authentication required"}), 401

    session_user_id = int(current_user["id"])
    mark_phase("auth")

    if should_enforce_extra_rate_limits():
        try:
            enforce_analysis_rate_limit(get_request_client_ip())
        except RequestRateLimitError as error:
            return build_rate_limit_response(error)

    if "resume_file" not in request.files:
        return jsonify({"detail": "resume_file is required"}), 400

    resume_file = request.files["resume_file"]
    job_description = normalize_text(request.form.get("job_description", ""))
    requested_target_role = normalize_text(request.form.get("target_role", ""))
    target_role = requested_target_role or "selected role"

    if not resume_file.filename:
        return jsonify({"detail": "A resume file must be uploaded"}), 400

    file_extension = get_file_extension(resume_file.filename)
    if file_extension not in SUPPORTED_RESUME_EXTENSIONS:
        return jsonify({"detail": "Only PDF, PNG, JPG, or JPEG resumes are supported"}), 400

    try:
        resume_text, document_info = extract_resume_text(resume_file, resume_file.filename)
    except ValueError as error:
        return jsonify({"detail": str(error)}), 400
    mark_phase("extract")

    if not resume_text:
        if file_extension in IMAGE_EXTENSIONS:
            return (
                jsonify(
                    {
                        "detail": (
                            "Could not extract any text from the uploaded image. "
                            "Please upload a clearer screenshot/photo or export the resume as a text-based PDF."
                        )
                    }
                ),
                400,
            )

        return (
            jsonify(
                {
                    "detail": (
                        "Could not extract any text from the uploaded PDF. "
                        "This usually happens when the resume is scanned or image-only. "
                        "Please export the resume as a text-based PDF and upload it again."
                    )
                }
            ),
            400,
        )

    selected_job: dict[str, Any] | None = None
    detected_job: dict[str, Any] | None = None
    ranked_role_matches: list[dict[str, Any]] = []
    top_role_matches: list[dict[str, Any]] = []

    if requested_target_role and requested_target_role != "selected role":
        try:
            selected_job = resolve_database_job_description(requested_target_role)
        except DatabaseErrors:
            selected_job = None

    try:
        ranked_role_matches = rank_database_role_matches(resume_text)
        top_role_matches = ranked_role_matches[:3]
    except DatabaseErrors:
        top_role_matches = []

    if not job_description:
        detected_job = top_role_matches[0] if top_role_matches else None

    recommended_job, selected_fit, detected_fit = pick_recommended_database_job(
        resume_text,
        selected_job,
        detected_job,
    )
    role_recommendation = build_role_recommendation(
        requested_target_role,
        selected_job,
        detected_job,
        recommended_job,
        selected_fit,
        detected_fit,
        ranked_role_matches,
    )

    job_source: dict[str, Any] = {
        "source": "user_input" if job_description else "database_auto",
        "requested_role": requested_target_role,
    }

    if not job_description and recommended_job:
        recommended_role = recommended_job["role"]
        job_description = recommended_job["job_description"]
        target_role = recommended_role["role_name"]

        if selected_job and role_recommendation.get("is_different_from_selected"):
            source = "database_recommended_role"
        elif selected_job:
            source = "database_role_match"
        else:
            source = "database_resume_auto_detect"

        job_source = {
            "source": source,
            "requested_role": requested_target_role,
            "selected_database_role": get_database_role_name(selected_job),
            "matched_role": recommended_role["role_name"],
            "detected_database_role": get_database_role_name(detected_job),
            "match_type": recommended_job.get("match_type"),
            "confidence_score": role_recommendation.get("recommended_score"),
            "match_status": role_recommendation.get("match_status"),
            "human_review_required": role_recommendation.get("human_review_required"),
            "reason": role_recommendation.get("reason_summary"),
        }
    elif selected_job:
        target_role = get_database_role_name(selected_job) or target_role
        job_source.update(
            {
                "selected_database_role": get_database_role_name(selected_job),
                "detected_database_role": get_database_role_name(detected_job),
                "match_status": role_recommendation.get("match_status"),
                "human_review_required": role_recommendation.get("human_review_required"),
                "reason": role_recommendation.get("reason_summary"),
            }
        )

    if not job_description:
        return (
            jsonify(
                {
                    "detail": (
                        "job_description is required because the system could not auto-detect a matching database role. "
                        "Select a database role or type a similar CS job role before uploading the resume."
                    )
                }
            ),
            400,
        )

    if recommended_job:
        analysis_requirements = recommended_job.get("requirements", [])
    elif selected_job:
        analysis_requirements = selected_job.get("requirements", [])
    else:
        analysis_requirements = None
    mark_phase("role")
    analysis = build_analysis_payload(resume_text, job_description, resume_file.filename, analysis_requirements, target_role, document_info)
    analysis["target_role"] = target_role
    analysis["selected_role"] = requested_target_role
    analysis["job_description"] = job_description
    analysis["job_source"] = job_source
    analysis["role_recommendation"] = role_recommendation
    analysis["top_role_matches"] = role_recommendation.get("top_role_matches", [])
    if role_recommendation.get("match_status") == "no_suitable_role":
        role_recommendation["fallback_sample_role"] = choose_fallback_sample_role(analysis)
    analysis["review_status"] = (
        "REVIEW REQUIRED"
        if role_recommendation.get("human_review_required")
        else "ROLE MATCH AVAILABLE"
    )
    analysis["resume_contact"] = extract_resume_contact(resume_text)
    analysis["recommendation_module"] = build_part8_recommendation_module(analysis)
    mark_phase("report")

    try:
        analysis["analysis_id"] = persist_analysis(
            analysis,
            job_description,
            target_role,
            session_user_id,
            resume_upload=read_resume_upload(resume_file),
        )
        analysis["history_saved"] = True
    except DatabaseErrors:
        analysis["analysis_id"] = None
        analysis["history_saved"] = False
        analysis["history_warning"] = "Analysis completed, but it could not be saved to history."

    accuracy_testing_result = update_accuracy_testing_workbook(analysis)
    analysis.update(accuracy_testing_result)
    analysis["resume_text_for_generation"] = resume_text[:RESUME_GENERATION_MAX_TEXT_LENGTH]
    mark_phase("save")

    response = jsonify(analysis)
    total_ms = (perf_counter() - started_at) * 1000
    response.headers["Server-Timing"] = ", ".join(
        (*phase_timings, f"total;dur={total_ms:.2f}")
    )
    return response


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    debug = os.getenv("FLASK_DEBUG", "0").strip().lower() in {"1", "true", "yes", "on"}
    app.run(host="0.0.0.0", port=port, debug=debug)

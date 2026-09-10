"""Pure helpers shared by generated output and report formatting."""

from __future__ import annotations

import re
from typing import Any

from core_utils import normalize_text


def normalize_resume_link(value: str) -> str:
    link = normalize_text(str(value or "")).strip(" ,;)")
    if not link:
        return ""
    if not re.match(r"^https?://", link, re.I):
        link = "https://" + link
    return link


def get_link_label(link: str) -> str:
    normalized = normalize_text(link)
    lowered = normalized.lower()
    without_scheme = re.sub(r"^https?://", "", normalized, flags=re.I).rstrip("/")
    if "linkedin.com" in lowered:
        return "LinkedIn: " + without_scheme
    if "github.com" in lowered:
        return "GitHub: " + without_scheme
    return "Portfolio: " + without_scheme


def percent_status_label(value: Any) -> str:
    try:
        score = float(value)
    except (TypeError, ValueError):
        score = 0.0
    if score >= 75:
        return "strong"
    if score >= 55:
        return "fair"
    return "needs_improvement"

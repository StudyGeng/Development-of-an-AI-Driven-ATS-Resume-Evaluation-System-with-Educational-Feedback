"""Application data structures."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ParsedResume:
    text: str
    skills: list[str]
    education: list[str]
    experience: list[str]
    excerpt: str

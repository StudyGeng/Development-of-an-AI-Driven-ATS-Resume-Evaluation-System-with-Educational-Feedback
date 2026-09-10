"""Small dependency-free helpers used across backend concerns."""

from __future__ import annotations

import os
import re
from difflib import SequenceMatcher


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def normalize_match_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def role_similarity_score(requested: str, candidate: str) -> float:
    requested_normalized = normalize_match_text(requested)
    candidate_normalized = normalize_match_text(candidate)
    if not requested_normalized or not candidate_normalized:
        return 0.0

    sequence_score = SequenceMatcher(None, requested_normalized, candidate_normalized).ratio()
    requested_tokens = set(requested_normalized.split())
    candidate_tokens = set(candidate_normalized.split())
    if not requested_tokens or not candidate_tokens:
        return sequence_score

    token_score = len(requested_tokens & candidate_tokens) / max(len(requested_tokens), len(candidate_tokens))
    contains_score = 1.0 if requested_normalized in candidate_normalized or candidate_normalized in requested_normalized else 0.0
    return max(sequence_score, token_score, contains_score)


def count_resume_words(text: str) -> int:
    return len(re.findall(r"\b[a-zA-Z][a-zA-Z+.#/-]*\b", text))


def get_file_extension(filename: str) -> str:
    _, extension = os.path.splitext(filename or "")
    return extension.lower()

"""Self-contained scoring helpers for resume analysis."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import math
from typing import Iterator

from sklearn.feature_extraction.text import TfidfVectorizer


@dataclass
class ScoringCache:
    """Keep repeated scoring work only for the current role-ranking run."""

    tfidf: dict[tuple[str, str], float] = field(default_factory=dict)
    tfidf_terms: dict[str, frozenset[str]] = field(default_factory=dict)
    phrase: dict[tuple[str, str], bool] = field(default_factory=dict)


_scoring_cache: ContextVar[ScoringCache | None] = ContextVar("scoring_cache", default=None)
_tfidf_analyzer = TfidfVectorizer(stop_words="english", binary=True).build_analyzer()
_unique_term_idf_squared = (1.0 + math.log(3.0 / 2.0)) ** 2


def get_scoring_cache() -> ScoringCache | None:
    return _scoring_cache.get()


@contextmanager
def scoring_cache_scope() -> Iterator[None]:
    token = _scoring_cache.set(ScoringCache())
    try:
        yield
    finally:
        _scoring_cache.reset(token)


def compute_tfidf_score(resume_text: str, job_description: str) -> float:
    documents = [resume_text or "", job_description or ""]
    if not any(documents):
        return 0.0

    cache = get_scoring_cache()
    cache_key = (documents[0], documents[1])
    if cache is not None and cache_key in cache.tfidf:
        return cache.tfidf[cache_key]

    # Binary term frequency makes this small lexical signal resistant to keyword
    # stuffing. With exactly two documents, shared terms have IDF 1 and terms
    # unique to one document have IDF 1 + log(3/2). This is the same cosine
    # similarity as fitting a new TfidfVectorizer for every requirement, without
    # repeatedly constructing sparse matrices.
    def terms_for(document: str) -> frozenset[str]:
        if cache is not None and document in cache.tfidf_terms:
            return cache.tfidf_terms[document]
        terms = frozenset(_tfidf_analyzer(document))
        if cache is not None:
            cache.tfidf_terms[document] = terms
        return terms

    resume_terms = terms_for(documents[0])
    job_terms = terms_for(documents[1])
    shared_count = len(resume_terms & job_terms)
    if not shared_count:
        if cache is not None:
            cache.tfidf[cache_key] = 0.0
        return 0.0

    resume_norm_squared = shared_count + (len(resume_terms) - shared_count) * _unique_term_idf_squared
    job_norm_squared = shared_count + (len(job_terms) - shared_count) * _unique_term_idf_squared
    similarity = shared_count / math.sqrt(resume_norm_squared * job_norm_squared)
    score = max(0.0, min(1.0, similarity)) * 100.0
    if cache is not None:
        cache.tfidf[cache_key] = score
    return score

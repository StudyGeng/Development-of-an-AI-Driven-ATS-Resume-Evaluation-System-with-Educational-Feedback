"""Self-contained scoring helpers for resume analysis."""

from __future__ import annotations

from sklearn.feature_extraction.text import TfidfVectorizer


def compute_tfidf_score(resume_text: str, job_description: str) -> float:
    documents = [resume_text or "", job_description or ""]
    if not any(documents):
        return 0.0

    vectorizer = TfidfVectorizer(stop_words="english")
    try:
        matrix = vectorizer.fit_transform(documents)
    except ValueError:
        return 0.0
    resume_vector = matrix[0]
    job_vector = matrix[1]
    similarity = float((resume_vector @ job_vector.T).toarray()[0][0])
    return max(0.0, min(1.0, similarity)) * 100.0

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
CSV_PATH = ROOT / "outputs" / "resume_dataset_filter" / "Resume_CS_Only.csv"
PDF_ROOT = ROOT / "Resume_template"
OUTPUT_DIR = ROOT / "outputs" / "batch_resume_evaluation"
DEFAULT_OUTPUT = OUTPUT_DIR / "batch_results.jsonl"
_APP_MODULE: Any | None = None
_BATCH_CACHED_FUNCTIONS: tuple[Any, ...] = ()


def get_scoring_module() -> Any:
    """Load production scoring once per worker and memoize pure calls per resume."""

    global _APP_MODULE, _BATCH_CACHED_FUNCTIONS
    if _APP_MODULE is not None:
        return _APP_MODULE

    backend_dir = str(ROOT / "backend")
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    import app as app_module  # noqa: PLC0415

    app_module.phrase_has_positive_resume_evidence = lru_cache(maxsize=None)(
        app_module.phrase_has_positive_resume_evidence
    )
    app_module.tokenize_canonical_keywords = lru_cache(maxsize=None)(
        app_module.tokenize_canonical_keywords
    )
    app_module.normalize_match_text = lru_cache(maxsize=None)(
        app_module.normalize_match_text
    )
    app_module.compute_tfidf_score = lru_cache(maxsize=None)(
        app_module.compute_tfidf_score
    )
    _BATCH_CACHED_FUNCTIONS = (
        app_module.phrase_has_positive_resume_evidence,
        app_module.tokenize_canonical_keywords,
        app_module.normalize_match_text,
        app_module.compute_tfidf_score,
    )
    _APP_MODULE = app_module
    return _APP_MODULE


def build_tasks() -> list[dict[str, str]]:
    tasks: list[dict[str, str]] = []
    with CSV_PATH.open("r", encoding="utf-8-sig", newline="") as handle:
        for row_index, row in enumerate(csv.DictReader(handle), start=2):
            resume_id = str(row.get("ID") or f"row-{row_index}").strip()
            tasks.append(
                {
                    "source_key": f"csv:{resume_id}",
                    "source_group": "Resume_CS_Only.csv",
                    "source_type": "CSV resume text",
                    "source_id": resume_id,
                    "source_name": resume_id,
                    "source_category": str(row.get("Category") or "").strip(),
                    "source_path": str(CSV_PATH.relative_to(ROOT)),
                    "resume_text": str(row.get("Resume_str") or ""),
                }
            )

    for pdf_path in sorted(PDF_ROOT.rglob("*.pdf"), key=lambda item: str(item).lower()):
        relative = pdf_path.relative_to(ROOT)
        tasks.append(
            {
                "source_key": f"pdf:{relative.as_posix()}",
                "source_group": "Resume_template",
                "source_type": "PDF resume",
                "source_id": relative.as_posix(),
                "source_name": pdf_path.name,
                "source_category": pdf_path.parent.name,
                "source_path": str(relative),
                "resume_text": "",
            }
        )
    return tasks


def scalar(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def evaluate_task(task: dict[str, str]) -> dict[str, Any]:
    os.environ.setdefault("APP_ENV", "testing")
    os.environ.setdefault(
        "FLASK_SECRET_KEY",
        "batch-evaluation-tests-only-secret-key-value-1234567890",
    )
    os.environ.setdefault("ACCURACY_TESTING_AUTO_EXPORT", "0")
    app = get_scoring_module()
    for cached_function in _BATCH_CACHED_FUNCTIONS:
        cached_function.cache_clear()

    started = time.perf_counter()
    result: dict[str, Any] = {
        key: value for key, value in task.items() if key != "resume_text"
    }
    result.update(
        {
            "run_status": "ERROR",
            "error_stage": "",
            "error_message": "",
            "completed_at_utc": "",
            "runtime_seconds": 0.0,
            "extraction": {},
            "text_character_count": 0,
            "text_word_count": 0,
            "content_sha256": "",
            "most_recent_job_title": "",
            "employment_entry_count": 0,
            "ranked_roles": [],
        }
    )

    try:
        if task["source_type"] == "PDF resume":
            result["error_stage"] = "PDF extraction"
            file_bytes = (ROOT / task["source_path"]).read_bytes()
            resume_text, extraction = app.extract_pdf_text(io.BytesIO(file_bytes))
        else:
            result["error_stage"] = "CSV text cleaning"
            resume_text = app.clean_resume_source_text(task["resume_text"])
            extraction = {
                "file_type": "csv",
                "page_count": None,
                "embedded_image_count": None,
                "ocr_used": False,
                "raw_text_length": len(task["resume_text"]),
                "pdf_extraction_mode": None,
                "character_spacing_repaired": False,
            }

        result["extraction"] = {key: scalar(value) for key, value in extraction.items()}
        result["text_character_count"] = len(resume_text)
        result["text_word_count"] = app.count_resume_words(resume_text)
        result["content_sha256"] = hashlib.sha256(
            resume_text.encode("utf-8", errors="replace")
        ).hexdigest()
        result["error_stage"] = "employment-title extraction"
        employment_entries = app.extract_employment_entries(resume_text)
        result["employment_entry_count"] = len(employment_entries)
        result["most_recent_job_title"] = app.get_most_recent_job_title(employment_entries)

        result["error_stage"] = "role ranking"
        ranked_matches = app.rank_database_role_matches(resume_text)
        ranked_roles: list[dict[str, Any]] = []
        for rank, match in enumerate(ranked_matches, start=1):
            role = match.get("role") or {}
            fit = match.get("fit_result") or {}
            ranked_roles.append(
                {
                    "rank": rank,
                    "role_id": scalar(role.get("id")),
                    "role_name": str(role.get("role_name") or ""),
                    "role_family": app.get_role_family(str(role.get("role_name") or "")),
                    "recommendation_score": float(match.get("confidence_score") or 0.0),
                    "base_fit_score": float(match.get("base_confidence_score") or 0.0),
                    "role_evidence_score": float(match.get("role_evidence_score") or 0.0),
                    "title_match_score": float(match.get("title_match_score") or 0.0),
                    "title_match_available": bool(match.get("title_match_available")),
                    "exact_title_match": bool(match.get("exact_title_match")),
                    "similarity_score": float(fit.get("similarity_score") or 0.0),
                    "keyword_overlap_score": float(fit.get("keyword_overlap_score") or 0.0),
                    "matched_keywords": [str(value) for value in fit.get("matched_keywords", [])],
                    "missing_keywords": [str(value) for value in fit.get("missing_keywords", [])],
                }
            )
        result["ranked_roles"] = ranked_roles
        result["run_status"] = "PROCESSED" if ranked_roles else "NO ROLES"
        result["error_stage"] = ""
    except Exception as error:  # keep every failed source in the audit output
        result["error_message"] = f"{type(error).__name__}: {error}"
    finally:
        result["runtime_seconds"] = round(time.perf_counter() - started, 3)
        result["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    return result


def read_completed(output_path: Path) -> dict[str, dict[str, Any]]:
    completed: dict[str, dict[str, Any]] = {}
    if not output_path.exists():
        return completed
    with output_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            source_key = str(record.get("source_key") or "")
            if source_key:
                completed[source_key] = record
    return completed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=max(1, min(8, os.cpu_count() or 1)))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    tasks = build_tasks()
    if args.limit > 0:
        tasks = tasks[: args.limit]
    completed = read_completed(args.output)
    pending = [task for task in tasks if task["source_key"] not in completed]
    print(
        json.dumps(
            {
                "event": "start",
                "total_sources": len(tasks),
                "already_completed": len(tasks) - len(pending),
                "pending": len(pending),
                "workers": args.workers,
                "output": str(args.output),
            }
        ),
        flush=True,
    )

    if not pending:
        return 0

    processed_now = 0
    errors_now = 0
    with args.output.open("a", encoding="utf-8", newline="\n") as output_handle:
        with ProcessPoolExecutor(max_workers=max(1, args.workers)) as executor:
            futures = {executor.submit(evaluate_task, task): task for task in pending}
            for future in as_completed(futures):
                task = futures[future]
                try:
                    result = future.result()
                except Exception as error:
                    result = {
                        key: value for key, value in task.items() if key != "resume_text"
                    }
                    result.update(
                        {
                            "run_status": "ERROR",
                            "error_stage": "worker",
                            "error_message": f"{type(error).__name__}: {error}",
                            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
                            "runtime_seconds": 0.0,
                            "extraction": {},
                            "text_character_count": 0,
                            "text_word_count": 0,
                            "content_sha256": "",
                            "most_recent_job_title": "",
                            "employment_entry_count": 0,
                            "ranked_roles": [],
                        }
                    )
                output_handle.write(json.dumps(result, ensure_ascii=False) + "\n")
                output_handle.flush()
                processed_now += 1
                errors_now += int(result.get("run_status") != "PROCESSED")
                if processed_now == 1 or processed_now % 10 == 0 or processed_now == len(pending):
                    print(
                        json.dumps(
                            {
                                "event": "progress",
                                "completed_now": processed_now,
                                "pending_now": len(pending) - processed_now,
                                "errors_now": errors_now,
                                "last_source": result.get("source_key"),
                                "last_seconds": result.get("runtime_seconds"),
                            }
                        ),
                        flush=True,
                    )

    print(
        json.dumps(
            {
                "event": "finished",
                "completed_now": processed_now,
                "errors_now": errors_now,
                "output": str(args.output),
            }
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import csv
import html
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "outputs" / "batch_resume_evaluation"
BATCH_PATH = OUT_DIR / "batch_results.jsonl"
CSV_PATH = ROOT / "outputs" / "resume_dataset_filter" / "Resume_CS_Only.csv"


JOBTITLE_PATTERN = re.compile(
    r'<span[^>]*class=["\'][^"\']*jobtitle[^"\']*["\'][^>]*>(.*?)</span>',
    re.IGNORECASE | re.DOTALL,
)


FAMILY_ROLES: dict[str, list[str]] = {
    "cybersecurity": [
        "Cybersecurity Analyst",
        "Cybersecurity Executive",
        "Computer Security Manager",
        "Data Security Specialist",
        "Digital Forensic Specialist",
        "Information Security Consultant",
        "Security Administrator (Information Technology)",
        "Security Technologist Specialist",
    ],
    "networking": [
        "Network Analyst",
        "Network System Engineer (Information Technology)",
        "Computer Network Technician",
        "Computer Infrastructure Specialist",
    ],
    "database": [
        "Database Architect",
        "Oracle Database Engineer",
        "SQL Database Developer",
    ],
    "software_development": [
        "Software Developer",
        "Computer Programmer",
        "Computer Programmer Grade 19",
        "Computer Programming Assistant",
        "Software Designer",
        "Full Stack Developer",
        "Java Consultant",
        "Systems Programmer",
        "Hypertext Preprocessor (PHP) Web Programmer",
        "Web Designer",
    ],
    "software_testing": [
        "Software Tester",
        "Systems Tester",
        "E-Commerce System Test Engineer",
        "QA Tester or Game Tester (Digital)",
        "Net (DotNet) Developer (Quality Assurance)",
    ],
    "it_support": [
        "Computer Help Desk Operator",
        "Computer Help Desk Technician",
        "Personal Computer Support Technician",
        "Product Support Engineer",
        "Computer Technician",
        "Computer Technician Grade FT19",
        "Computer Specialist",
        "Information Technology Technical Specialist",
        "Computer User Services Assistant",
        "Computer Assistant",
        "Administrative Assistant, Computer Technician",
        "Computer Equipment Fitter",
    ],
    "cloud_devops": [
        "Application Consultant (Cloud)",
        "Cloud Computing Solution Architect",
        "Computer Infrastructure Specialist",
        "Information Technology Technical Specialist",
    ],
    "it_management": [
        "Computer Services Manager",
        "Information and Communication Technology (ICT) Development Manager",
        "Software and Applications Manager",
        "Information Technology Business Development Manager",
        "Information System Officer",
        "Assistant Computer System Executive",
    ],
    "business_analysis": [
        "Business Data Analyst",
        "Business Intelligence Analyst",
        "Business Process Specialist",
        "System Application and Products (SAP) Business Analyst",
        "Information Technology Consultant",
        "Technology Innovation Analyst",
        "Information System Officer",
    ],
    "data_ai": [
        "Business Data Analyst",
        "Business Intelligence Analyst",
        "Data Scientist",
        "Machine Learning Engineer",
        "Artificial Intelligence Engineer (AI)",
    ],
    "general_it": [
        "Information Technology Consultant",
        "Information Technology Technical Specialist",
        "Information System Officer",
        "Computer Specialist",
        "Assistant Computer System Executive",
        "Computer Engineering Assistant",
        "Communication and Computer Engineering Assistant",
    ],
}


CSV_EXACT_LABELS = {
    "37764298": "Information Technology Consultant",
    "37242217": "Information Technology Consultant",
    "64017585": "Information Technology Consultant",
    "39413067": "Information Technology Consultant",
    "51363762": "Computer Specialist",
}


CSV_MULTI_FAMILIES: dict[str, list[str]] = {
    "16899268": ["it_management", "business_analysis"],
    "23864648": ["it_management", "cloud_devops", "networking"],
    "33381211": ["it_management", "software_development"],
    "16533554": ["it_management", "networking"],
    "20879311": ["it_management", "data_ai"],
    "17111768": ["it_management", "business_analysis", "it_support"],
    "16186411": ["database", "software_development"],
    "26746496": ["database", "software_development"],
}


CSV_NEGATIVE_CONTROLS = {
    "24230851",
    "12334140",
    "39718499",
    "25857360",
    "15297298",
    "48037995",
}


def clean_markup(value: str) -> str:
    return re.sub(
        r"\s+",
        " ",
        html.unescape(re.sub(r"<[^>]+>", " ", value)),
    ).strip(" .|-")


def first_html_job_title(value: str) -> str:
    for match in JOBTITLE_PATTERN.findall(value or ""):
        cleaned = clean_markup(match)
        if cleaned:
            return cleaned
    return ""


def load_csv_titles() -> dict[str, str]:
    titles: dict[str, str] = {}
    with CSV_PATH.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            titles[str(row.get("ID") or "").strip()] = first_html_job_title(
                str(row.get("Resume_html") or "")
            )
    return titles


def dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def family_union(families: list[str]) -> list[str]:
    return dedupe([role for family in families for role in FAMILY_ROLES[family]])


def csv_family_label(resume_id: str, source_title: str) -> tuple[list[str], str, str]:
    """Return frozen source-title families without consulting system output."""

    if resume_id in CSV_MULTI_FAMILIES:
        families = CSV_MULTI_FAMILIES[resume_id]
        return families, "MEDIUM", "Explicit multi-specialty source title"

    title = source_title.casefold()
    if re.search(r"\b(infosec|security|auditor)\b", title):
        return ["cybersecurity"], "HIGH", "Explicit security source title"
    if re.search(r"\b(database|oracle)\b", title):
        return ["database"], "HIGH", "Explicit database source title"
    if re.search(r"\b(test automation|software test|qa\b|quality assurance)", title):
        return ["software_testing"], "HIGH", "Explicit software-testing source title"
    if re.search(r"\b(software|web developer|drupal|asp\.?net|programmer|\.net developer)\b", title):
        return ["software_development"], "HIGH", "Explicit software-development source title"
    if re.search(r"\b(network|lan\b|cyber transport|rf systems)\b", title):
        return ["networking"], "HIGH", "Explicit networking source title"
    if re.search(r"\b(help desk|support specialist|support technician|computer repair|technician)\b", title):
        return ["it_support"], "HIGH", "Explicit IT-support source title"
    if re.search(r"\b(cloud|aws|infrastructure operations|it infrastructure)\b", title):
        return ["cloud_devops"], "HIGH", "Explicit cloud/infrastructure source title"
    if re.search(r"\b(analytics|data analyst|business systems analyst)\b", title):
        return ["data_ai", "business_analysis"], "MEDIUM", "Explicit analytics/analysis source title"
    if re.search(
        r"\b(manager|director|vice president|vp\b|chief information officer|cio\b|administrator of information technology|it management)\b",
        title,
    ):
        return ["it_management"], "MEDIUM", "Broad IT-management source title"
    return [], "", "Source title needs human mapping"


def pdf_label(source_id: str) -> dict[str, Any]:
    normalized = source_id.replace("\\", "/")
    parts = normalized.split("/")
    folder = parts[-2]
    filename = parts[-1]
    lowered = filename.casefold()

    def mapped(
        primary: str,
        acceptable: list[str],
        family: str,
        confidence: str = "HIGH",
        note: str = "",
    ) -> dict[str, Any]:
        return {
            "label_status": "VALIDATED_TEMPLATE",
            "expected_primary_role": primary,
            "acceptable_roles": acceptable,
            "expected_family": family,
            "label_confidence": confidence,
            "label_note": note,
        }

    number_match = re.search(r"_(\d+)\.pdf$", lowered)
    number = int(number_match.group(1)) if number_match else 1

    if folder == "bad_resume":
        return {
            "label_status": "EXCLUDED_INVALID_DOCUMENT",
            "expected_primary_role": "",
            "acceptable_roles": [],
            "expected_family": "",
            "label_confidence": "",
            "label_note": "Instructional pack with several people; not one candidate resume",
        }
    if folder == "cybersecurity":
        if number in (1, 2):
            return mapped("Cybersecurity Analyst", ["Cybersecurity Analyst", "Cybersecurity Executive"], "cybersecurity")
        if number in (3, 4):
            return mapped("Security Technologist Specialist", ["Security Technologist Specialist", "Cybersecurity Analyst"], "cybersecurity")
        return mapped("Information Security Consultant", ["Information Security Consultant", "Cybersecurity Analyst"], "cybersecurity")
    if folder == "devops_it_operations":
        if number in (3, 4):
            return mapped(
                "Information and Communication Technology (ICT) Development Manager",
                [
                    "Information and Communication Technology (ICT) Development Manager",
                    "Computer Services Manager",
                    "Software and Applications Manager",
                    "Cloud Computing Solution Architect",
                ],
                "cloud_devops",
                "MEDIUM",
                "DevOps Manager has no literal catalog role",
            )
        return mapped(
            "Cloud Computing Solution Architect",
            ["Cloud Computing Solution Architect", "Application Consultant (Cloud)", "Computer Infrastructure Specialist"],
            "cloud_devops",
            "MEDIUM",
            "DevOps Engineer has no literal catalog role",
        )
    if folder == "fullstack":
        return mapped("Full Stack Developer", ["Full Stack Developer", "Software Developer"], "software_development")
    if folder == "it_manager":
        if number == 6:
            return mapped("Computer Security Manager", ["Computer Security Manager", "Information Security Consultant", "Cybersecurity Executive"], "cybersecurity")
        return mapped(
            "Computer Services Manager",
            [
                "Computer Services Manager",
                "Computer Infrastructure Specialist",
                "Information and Communication Technology (ICT) Development Manager",
                "Software and Applications Manager",
            ],
            "it_management",
            "MEDIUM" if number in (3, 4, 5) else "HIGH",
            "Infrastructure Manager has no literal catalog role" if number in (3, 4, 5) else "",
        )
    if folder == "java_developer":
        if number in (2, 3, 4):
            return mapped("Full Stack Developer", ["Full Stack Developer", "Java Consultant", "Software Developer"], "software_development")
        return mapped("Java Consultant", ["Java Consultant", "Software Developer", "Computer Programmer"], "software_development")
    if folder == "network_admin":
        return mapped(
            "Computer Network Technician",
            ["Computer Network Technician", "Network System Engineer (Information Technology)", "Network Analyst"],
            "networking",
            "MEDIUM",
            "Network Administrator has no literal catalog role",
        )
    if folder == "network_engineer":
        return mapped(
            "Network System Engineer (Information Technology)",
            ["Network System Engineer (Information Technology)", "Network Analyst", "Computer Network Technician"],
            "networking",
        )
    if folder == "python":
        return mapped(
            "Software Developer",
            ["Software Developer", "Computer Programmer", "Full Stack Developer"],
            "software_development",
            "MEDIUM",
            "Python Developer has no literal catalog role",
        )
    if folder == "qa":
        return mapped("Software Tester", ["Software Tester", "Systems Tester", "E-Commerce System Test Engineer"], "software_testing")
    if folder == "software_developer":
        if number == 7:
            return mapped("Full Stack Developer", ["Full Stack Developer", "Software Developer", "Web Designer"], "software_development")
        if number in (8, 9, 10):
            return mapped("Full Stack Developer", ["Full Stack Developer", "Software Developer", "Computer Programmer"], "software_development")
        return mapped("Software Developer", ["Software Developer", "Computer Programmer", "Software Designer"], "software_development")
    if folder == "software_engineer":
        if number in (7, 8):
            return mapped("Software Tester", ["Software Tester", "Systems Tester", "E-Commerce System Test Engineer"], "software_testing")
        return mapped("Software Developer", ["Software Developer", "Computer Programmer", "Software Designer"], "software_development")
    if folder == "web_developer":
        if number == 1:
            acceptable = ["Full Stack Developer", "Hypertext Preprocessor (PHP) Web Programmer", "Web Designer"]
        elif number in (2, 3, 4):
            acceptable = ["Full Stack Developer", "Web Designer"]
        elif number == 5:
            acceptable = ["Full Stack Developer", "Hypertext Preprocessor (PHP) Web Programmer", "Software Developer"]
        else:
            acceptable = ["Full Stack Developer", "Web Designer", "Hypertext Preprocessor (PHP) Web Programmer"]
        return mapped("Full Stack Developer", acceptable, "software_development")
    if folder == "real_resume":
        real: dict[str, tuple[str, list[str], str, str, str]] = {
            "muhammad_zikri_resume.pdf": (
                "Software Tester",
                ["Software Tester", "Machine Learning Engineer", "Artificial Intelligence Engineer (AI)"],
                "software_testing|data_ai",
                "HIGH",
                "Multi-target real resume; professional role is QA/Software Testing",
            ),
            "ngu ming henn_resume_final.pdf": (
                "Full Stack Developer",
                ["Full Stack Developer", "Software Developer", "Computer Technician", "Computer Help Desk Technician"],
                "software_development|it_support",
                "MEDIUM",
                "Ambiguous multi-role resume: full-stack internship and later IT technician",
            ),
            "resume.pdf": (
                "Full Stack Developer",
                ["Full Stack Developer", "Software Developer"],
                "software_development",
                "MEDIUM",
                "Project-focused student resume",
            ),
            "resume2.pdf": (
                "Software Developer",
                ["Software Developer", "Full Stack Developer", "Computer Programmer"],
                "software_development",
                "HIGH",
                "Software-development internship and projects",
            ),
            "resume3.pdf": (
                "Machine Learning Engineer",
                ["Machine Learning Engineer", "Artificial Intelligence Engineer (AI)", "Data Scientist"],
                "data_ai",
                "HIGH",
                "Image-only PDF; visual headline is AI/ML Engineer",
            ),
        }
        primary, acceptable, family, confidence, note = real[lowered]
        return {
            "label_status": "PROVISIONAL_REAL_RESUME",
            "expected_primary_role": primary,
            "acceptable_roles": acceptable,
            "expected_family": family,
            "label_confidence": confidence,
            "label_note": note,
        }
    raise ValueError(f"No PDF label rule for {source_id}")


def load_batch() -> list[dict[str, Any]]:
    by_key: dict[str, dict[str, Any]] = {}
    with BATCH_PATH.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                record = json.loads(line)
                by_key[str(record["source_key"])] = record
    return [by_key[key] for key in sorted(by_key)]


def safe_rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def metric_block(rows: list[dict[str, Any]]) -> dict[str, Any]:
    denominator = len(rows)
    top1 = sum(bool(row.get("top1_acceptable")) for row in rows)
    top3 = sum(bool(row.get("top3_acceptable")) for row in rows)
    ranks = [int(row["best_acceptable_rank"]) for row in rows if row.get("best_acceptable_rank")]
    return {
        "cases": denominator,
        "top1_passes": top1,
        "top1_accuracy": safe_rate(top1, denominator),
        "top3_passes": top3,
        "top3_accuracy": safe_rate(top3, denominator),
        "mean_best_rank": mean(ranks) if ranks else None,
        "median_best_rank": median(ranks) if ranks else None,
        "mrr": mean(1 / rank for rank in ranks) if ranks else None,
        "average_top1_score": mean(row["top1_score"] for row in rows) if rows else None,
        "average_expected_score": mean(row["best_acceptable_score"] for row in rows if row.get("best_acceptable_score") is not None) if ranks else None,
    }


def main() -> None:
    records = load_batch()
    csv_titles = load_csv_titles()
    if len(records) != 205:
        raise RuntimeError(f"Expected 205 unique batch records, found {len(records)}")

    pdf_hashes: dict[str, list[str]] = defaultdict(list)
    for record in records:
        if record["source_group"] == "Resume_template" and record.get("content_sha256"):
            pdf_hashes[str(record["content_sha256"])].append(str(record["source_key"]))
    duplicate_hashes = {
        digest: sorted(keys)
        for digest, keys in pdf_hashes.items()
        if len(keys) > 1
    }
    duplicate_group_by_key: dict[str, str] = {}
    duplicate_copy_keys: set[str] = set()
    for index, keys in enumerate(sorted(duplicate_hashes.values()), start=1):
        for key in keys:
            duplicate_group_by_key[key] = f"PDF-DUP-{index:02d}"
        duplicate_copy_keys.update(keys[1:])

    rows: list[dict[str, Any]] = []
    long_rankings: list[dict[str, Any]] = []
    catalog_roles = {
        str(role["role_name"])
        for record in records[:1]
        for role in record.get("ranked_roles", [])
    }
    for family, roles in FAMILY_ROLES.items():
        missing = sorted(set(roles) - catalog_roles)
        if missing:
            raise RuntimeError(f"Invalid {family} audit roles: {missing}")

    for record in records:
        source_key = str(record["source_key"])
        source_group = str(record["source_group"])
        source_id = str(record["source_id"])
        ranked = list(record.get("ranked_roles") or [])
        extraction_ok = int(record.get("text_word_count") or 0) > 0
        source_title = csv_titles.get(source_id, "") if source_group == "Resume_CS_Only.csv" else ""

        if source_group == "Resume_template":
            label = pdf_label(source_id)
        elif source_id in CSV_EXACT_LABELS:
            role = CSV_EXACT_LABELS[source_id]
            label = {
                "label_status": "PROVISIONAL_CSV_EXACT",
                "expected_primary_role": role,
                "acceptable_roles": [role],
                "expected_family": "general_it",
                "label_confidence": "HIGH",
                "label_note": "Exact catalog role appears in independent HTML job-title field",
            }
        elif source_id in CSV_NEGATIVE_CONTROLS:
            label = {
                "label_status": "NEGATIVE_CONTROL",
                "expected_primary_role": "NO IT ROLE",
                "acceptable_roles": [],
                "expected_family": "out_of_domain",
                "label_confidence": "HIGH",
                "label_note": "First source job title is outside the IT role catalog",
            }
        else:
            families, confidence, note = csv_family_label(source_id, source_title)
            label = {
                "label_status": "PROVISIONAL_CSV_FAMILY" if families else "HUMAN_REVIEW_REQUIRED",
                "expected_primary_role": "",
                "acceptable_roles": family_union(families) if families else [],
                "expected_family": "|".join(families),
                "label_confidence": confidence,
                "label_note": note,
            }

        acceptable = list(label["acceptable_roles"])
        invalid = sorted(set(acceptable) - catalog_roles)
        if invalid:
            raise RuntimeError(f"Invalid acceptable role(s) for {source_key}: {invalid}")
        acceptable_set = set(acceptable)

        top = ranked[0] if ranked else {}
        second = ranked[1] if len(ranked) > 1 else {}
        third = ranked[2] if len(ranked) > 2 else {}
        acceptable_matches = [role for role in ranked if str(role.get("role_name")) in acceptable_set]
        best = acceptable_matches[0] if acceptable_matches else None
        primary = str(label["expected_primary_role"])
        primary_match = next((role for role in ranked if str(role.get("role_name")) == primary), None)
        top1_role = str(top.get("role_name") or "")
        top1_score = float(top.get("recommendation_score") or 0)
        top2_score = float(second.get("recommendation_score") or 0)
        explicit_non_it_headline = bool(top.get("explicit_non_it_headline"))
        review_threshold = 35.0 if explicit_non_it_headline else 20.0
        human_review_required = bool(extraction_ok and top and top1_score < review_threshold)
        system_disposition = (
            "EXTRACTION FAILED"
            if not extraction_ok
            else ("NO SUITABLE ROLE" if human_review_required else "ROLE MATCH AVAILABLE")
        )
        if not extraction_ok:
            outcome = "EXTRACTION FAILED"
        elif label["label_status"] == "EXCLUDED_INVALID_DOCUMENT":
            outcome = "EXCLUDED"
        elif label["label_status"] == "NEGATIVE_CONTROL":
            outcome = "NO SUITABLE ROLE" if human_review_required else "FALSE ROLE RECOMMENDATION"
        elif acceptable:
            best_rank = int(best["rank"]) if best else None
            outcome = "PASS TOP 1" if best_rank == 1 else ("PASS TOP 3" if best_rank and best_rank <= 3 else "MISS TOP 3")
        else:
            outcome = "HUMAN REVIEW"

        row = {
            "source_key": source_key,
            "source_group": source_group,
            "source_type": record["source_type"],
            "source_id": source_id,
            "source_name": record["source_name"],
            "source_category": record["source_category"],
            "source_title": source_title,
            "run_status": record["run_status"],
            "extraction_status": "OK" if extraction_ok else "FAILED: NO EXTRACTABLE TEXT",
            "page_count": (record.get("extraction") or {}).get("page_count"),
            "word_count": int(record.get("text_word_count") or 0),
            "role_ranking_version": str(record.get("role_ranking_version") or "unknown"),
            "most_recent_job_title": str(record.get("most_recent_job_title") or ""),
            "title_match_available": bool(top.get("title_match_available")),
            "label_status": label["label_status"],
            "label_confidence": label["label_confidence"],
            "expected_family": label["expected_family"],
            "expected_primary_role": primary,
            "acceptable_roles": " | ".join(acceptable),
            "label_note": label["label_note"],
            "top1_role": top1_role,
            "top1_score": top1_score,
            "top1_base_fit": float(top.get("base_fit_score") or 0),
            "top1_role_evidence": float(top.get("role_evidence_score") or 0),
            "top1_title_match": float(top.get("title_match_score") or 0),
            "top1_title_match_title": str(top.get("title_match_title") or ""),
            "top1_title_match_source": str(top.get("title_match_source") or "unavailable"),
            "top1_explicit_non_it_headline": explicit_non_it_headline,
            "top1_non_it_headline": str(top.get("non_it_headline") or ""),
            "review_threshold": review_threshold,
            "human_review_required": human_review_required,
            "system_disposition": system_disposition,
            "top2_role": str(second.get("role_name") or ""),
            "top2_score": top2_score,
            "top3_role": str(third.get("role_name") or ""),
            "top3_score": float(third.get("recommendation_score") or 0),
            "top1_margin": round(top1_score - top2_score, 2),
            "primary_role_rank": int(primary_match["rank"]) if primary_match else None,
            "primary_role_score": float(primary_match["recommendation_score"]) if primary_match else None,
            "best_acceptable_rank": int(best["rank"]) if best else None,
            "best_acceptable_role": str(best["role_name"]) if best else "",
            "best_acceptable_score": float(best["recommendation_score"]) if best else None,
            "top1_acceptable": bool(best and int(best["rank"]) == 1),
            "top3_acceptable": bool(best and int(best["rank"]) <= 3),
            "reciprocal_rank": (1 / int(best["rank"])) if best else None,
            "outcome": outcome,
            "duplicate_group": duplicate_group_by_key.get(source_key, ""),
            "is_duplicate_copy": source_key in duplicate_copy_keys,
            "content_sha256": record.get("content_sha256", ""),
            "runtime_seconds": float(record.get("runtime_seconds") or 0),
            "error_message": record.get("error_message", ""),
        }
        rows.append(row)
        for role in ranked:
            long_rankings.append(
                {
                    "source_key": source_key,
                    "source_group": source_group,
                    "source_name": record["source_name"],
                    "rank": int(role["rank"]),
                    "role_name": str(role["role_name"]),
                    "recommendation_score": float(role["recommendation_score"]),
                    "base_fit_score": float(role["base_fit_score"]),
                    "role_evidence_score": float(role["role_evidence_score"]),
                    "title_match_score": float(role["title_match_score"]),
                    "title_match_available": bool(role["title_match_available"]),
                    "exact_title_match": bool(role["exact_title_match"]),
                    "title_match_title": str(role.get("title_match_title") or ""),
                    "title_match_source": str(role.get("title_match_source") or "unavailable"),
                    "explicit_non_it_headline": bool(role.get("explicit_non_it_headline")),
                    "non_it_headline": str(role.get("non_it_headline") or ""),
                    "role_ranking_version": str(record.get("role_ranking_version") or "unknown"),
                    "keyword_overlap_score": float(role["keyword_overlap_score"]),
                    "similarity_score": float(role["similarity_score"]),
                }
            )

    rows.sort(key=lambda row: (row["source_group"], row["source_category"], row["source_name"], row["source_key"]))
    long_rankings.sort(key=lambda row: (row["source_key"], row["rank"]))

    validated = [row for row in rows if row["label_status"] == "VALIDATED_TEMPLATE"]
    validated_unique = [row for row in validated if not row["is_duplicate_copy"]]
    real = [row for row in rows if row["label_status"] == "PROVISIONAL_REAL_RESUME"]
    real_extracted = [row for row in real if row["extraction_status"] == "OK"]
    csv_exact = [row for row in rows if row["label_status"] == "PROVISIONAL_CSV_EXACT"]
    csv_family = [row for row in rows if row["label_status"] == "PROVISIONAL_CSV_FAMILY"]
    negative = [row for row in rows if row["label_status"] == "NEGATIVE_CONTROL"]

    summary = {
        "total_sources": len(rows),
        "csv_sources": sum(row["source_group"] == "Resume_CS_Only.csv" for row in rows),
        "pdf_sources": sum(row["source_group"] == "Resume_template" for row in rows),
        "role_ranking_versions": sorted({row["role_ranking_version"] for row in rows}),
        "processed_with_95_roles": sum(row["run_status"] == "PROCESSED" for row in rows),
        "extraction_ok": sum(row["extraction_status"] == "OK" for row in rows),
        "extraction_failed": sum(row["extraction_status"] != "OK" for row in rows),
        "pdf_mult_page": sum((row["page_count"] or 0) > 1 for row in rows if row["source_group"] == "Resume_template"),
        "csv_title_parser_available": sum(bool(row["most_recent_job_title"]) for row in rows if row["source_group"] == "Resume_CS_Only.csv"),
        "validated_template_raw": metric_block(validated),
        "validated_template_unique": metric_block(validated_unique),
        "provisional_real_raw": metric_block(real),
        "provisional_real_extracted": metric_block(real_extracted),
        "provisional_csv_exact": metric_block(csv_exact),
        "provisional_csv_family": metric_block(csv_family),
        "csv_family_cases": len(csv_family),
        "csv_human_review_required": sum(row["label_status"] == "HUMAN_REVIEW_REQUIRED" for row in rows),
        "negative_control_cases": len(negative),
        "negative_controls_below_20": sum(row["top1_score"] < 20 for row in negative),
        "negative_controls_review_required": sum(row["human_review_required"] for row in negative),
        "negative_controls_false_recommendations": sum(not row["human_review_required"] for row in negative),
        "negative_control_top1_scores": [row["top1_score"] for row in negative],
        "duplicate_pdf_groups": len(duplicate_hashes),
        "duplicate_pdf_copies": len(duplicate_copy_keys),
        "top1_role_frequency": Counter(row["top1_role"] for row in rows),
    }

    role_groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["acceptable_roles"]:
            role_groups[(row["label_status"], row["expected_family"], row["expected_primary_role"])].append(row)
    role_metrics: list[dict[str, Any]] = []
    for (status, family, primary), group in sorted(role_groups.items()):
        metrics = metric_block(group)
        role_metrics.append(
            {
                "label_status": status,
                "expected_family": family,
                "expected_primary_role": primary,
                **metrics,
            }
        )

    (OUT_DIR / "evaluation_rows.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT_DIR / "all_rankings.json").write_text(json.dumps(long_rankings, ensure_ascii=False), encoding="utf-8")
    (OUT_DIR / "evaluation_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=dict), encoding="utf-8")
    (OUT_DIR / "role_metrics.json").write_text(json.dumps(role_metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=dict))


if __name__ == "__main__":
    main()

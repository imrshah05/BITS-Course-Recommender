"""Orchestrate preprocessing and validate the resulting datasets together."""

import argparse
from collections import Counter
from dataclasses import dataclass
import json
from pathlib import Path
import re

from preprocessing.academic_regulations import build_academic_regulations
from preprocessing.academic_rules import build_academic_rules
from preprocessing.course_codes import CODE
from preprocessing.dataset import build_course_dataset, write_dataset
from preprocessing.programme_requirements import build_programme_requirements
from preprocessing.timetable import build_timetable


@dataclass(frozen=True)
class PipelinePaths:
    """Configurable inputs and outputs for one preprocessing run."""

    handouts: Path = Path("data/raw/handouts")
    regulations_pdf: Path = Path("data/raw/Academic-Regulations-2023.pdf")
    bulletin_pdf: Path = Path("data/raw/bulletin.pdf")
    timetable_pdf: Path = Path("data/raw/timetable.pdf")
    courses: Path = Path("data/processed/courses.json")
    regulations: Path = Path("data/processed/academic_regulations.json")
    programme_requirements: Path = Path("data/processed/programme_requirements.json")
    academic_rules: Path = Path("data/processed/academic_rules.json")
    timetable: Path = Path("data/processed/timetable.json")
    report: Path = Path("data/processed/preprocessing_report.json")

    def raw_inputs(self):
        return (self.handouts, self.regulations_pdf, self.bulletin_pdf, self.timetable_pdf)

    def processed_inputs(self):
        return (self.courses, self.regulations, self.programme_requirements,
                self.academic_rules, self.timetable)


def _load(path):
    try:
        with Path(path).open(encoding="utf-8") as stream:
            return json.load(stream)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON dataset: {path}: {exc}") from exc


def _issues(record):
    validation = record.get("validation") or {}
    return validation.get("issues") or []


def _valid_code(value):
    return isinstance(value, str) and re.fullmatch(CODE, value) is not None


def _course_code(record):
    field = (record.get("metadata") or {}).get("course_code")
    return field.get("value") if isinstance(field, dict) else None


def _course_codes(record):
    metadata = record.get("metadata") or {}
    values = metadata.get("course_codes")
    if isinstance(values, list):
        return [value for value in values if _valid_code(value)]
    value = _course_code(record)
    return [value] if _valid_code(value) else []


def _course_traceable(record):
    source = record.get("source") or {}
    return bool(source.get("source_file") and source.get("page_numbers"))


def _record_traceable(record):
    sources = record.get("sources")
    return bool(sources) and all(
        isinstance(source, dict) and source.get("source_file") and
        type(source.get("page_number")) is int and source["page_number"] > 0 and
        isinstance(source.get("text"), str) and source["text"].strip()
        for source in sources
    )


def _dataset_summary(records, traceable):
    issues = [issue for record in records for issue in _issues(record)]
    return {
        "records": len(records),
        "needs_verification": sum(bool(record.get("needs_verification") or
                                       (record.get("validation") or {}).get("needs_verification"))
                                  for record in records),
        "warnings": sum(issue.get("severity") == "warning" for issue in issues),
        "errors": sum(issue.get("severity") == "error" for issue in issues),
        "missing_source_traceability": sum(not traceable(record) for record in records),
    }


def validate_processed_datasets(courses, academic_rules, timetable):
    """Return a stable, non-destructive integrity report for later pipeline layers."""
    course_records = courses.get("records")
    rule_records = academic_rules.get("records")
    timetable_records = timetable.get("records")
    for name, records in (("courses", course_records), ("academic_rules", rule_records),
                          ("timetable", timetable_records)):
        if not isinstance(records, list) or not records:
            raise ValueError(f"{name} must contain a non-empty records list")

    course_summary = _dataset_summary(course_records, _course_traceable)
    rule_summary = _dataset_summary(rule_records, _record_traceable)
    timetable_summary = _dataset_summary(timetable_records, _record_traceable)
    course_codes = sorted({code for record in course_records for code in _course_codes(record)})
    rule_codes = sorted({record.get("course_code") for record in rule_records
                         if _valid_code(record.get("course_code"))})
    timetable_codes = sorted({record.get("course_code") for record in timetable_records
                              if _valid_code(record.get("course_code"))})
    known = set(course_codes)
    rule_matched = sorted(set(rule_codes) & known)
    rule_unmatched = sorted(set(rule_codes) - known)
    timetable_matched = sorted(set(timetable_codes) & known)
    timetable_unmatched = sorted(set(timetable_codes) - known)

    pipeline_issues = []
    for name, summary in (("courses", course_summary), ("academic_rules", rule_summary),
                          ("timetable", timetable_summary)):
        if summary["missing_source_traceability"]:
            pipeline_issues.append({"code": "missing_source_traceability", "dataset": name,
                                    "severity": "error", "count": summary["missing_source_traceability"]})
    for name, values in (("academic_rules", rule_unmatched), ("timetable", timetable_unmatched)):
        if values:
            pipeline_issues.append({"code": "unmatched_course_codes", "dataset": name,
                                    "severity": "warning", "count": len(values)})
    malformed = {
        "courses": sum(_course_code(record) is not None and not _course_codes(record)
                       for record in course_records),
        "academic_rules": sum(record.get("course_code") is not None and
                              not _valid_code(record.get("course_code")) for record in rule_records),
        "timetable": sum(record.get("course_code") is not None and
                         not _valid_code(record.get("course_code")) for record in timetable_records),
    }
    for name, count in malformed.items():
        if count:
            pipeline_issues.append({"code": "malformed_course_codes", "dataset": name,
                                    "severity": "warning", "count": count})

    summaries = {"courses": course_summary, "academic_rules": rule_summary,
                 "timetable": timetable_summary}
    warning_count = sum(item["warnings"] for item in summaries.values()) + sum(
        item["severity"] == "warning" for item in pipeline_issues)
    error_count = sum(item["errors"] for item in summaries.values()) + sum(
        item["severity"] == "error" for item in pipeline_issues)
    return {
        "schema_version": 1,
        "datasets": summaries,
        "course_codes": {
            "courses": {"unique": len(course_codes), "values": course_codes,
                        "single_identity_records": sum((record.get("metadata") or {}).get("course_identity_type") == "single"
                                                       for record in course_records),
                        "multiple_identity_records": sum((record.get("metadata") or {}).get("course_identity_type") == "multiple"
                                                         for record in course_records),
                        "unusable_identity_records": sum(record.get("candidate_status") == "unusable_identity"
                                                         for record in course_records)},
            "academic_rules": {"unique": len(rule_codes), "matched": len(rule_matched),
                               "unmatched": len(rule_unmatched), "matched_values": rule_matched,
                               "unmatched_values": rule_unmatched},
            "timetable": {"unique": len(timetable_codes), "matched": len(timetable_matched),
                          "unmatched": len(timetable_unmatched), "matched_values": timetable_matched,
                          "unmatched_values": timetable_unmatched},
        },
        "malformed_course_code_counts": malformed,
        "pipeline_issues": sorted(pipeline_issues,
                                  key=lambda item: (item["severity"], item["dataset"], item["code"])),
        "totals": {
            "needs_verification": sum(item["needs_verification"] for item in summaries.values()),
            "warnings": warning_count,
            "errors": error_count,
            "missing_source_traceability": sum(item["missing_source_traceability"]
                                                for item in summaries.values()),
        },
        "status": "valid" if error_count == 0 else "invalid",
        "notes": ["Unmatched course codes are informational and are not treated as fatal.",
                  "Exact normalized course-code comparison is used; no fuzzy matching is performed."],
    }


def validate_existing(paths=PipelinePaths()):
    """Load committed outputs and write only the integrated validation report."""
    missing = [str(path) for path in paths.processed_inputs() if not Path(path).is_file()]
    if missing:
        raise FileNotFoundError("Missing processed inputs: " + ", ".join(missing))
    courses, regulations, requirements, rules, timetable = map(_load, paths.processed_inputs())
    report = validate_processed_datasets(courses, rules, timetable)
    report["datasets_validated"] = [Path(path).name for path in paths.processed_inputs()]
    report["supporting_source_counts"] = {
        "academic_regulations": len(regulations.get("rules", [])),
        "programme_requirements": len(requirements.get("requirements", [])),
        "programme_unresolved_sections": len(requirements.get("unresolved_sections", [])),
    }
    write_dataset(report, paths.report)
    return report


def run_preprocessing(paths=PipelinePaths(), rebuild=False):
    """Optionally rebuild all outputs, then perform integrated validation."""
    if rebuild:
        missing = [str(path) for path in paths.raw_inputs() if not Path(path).exists()]
        if missing:
            raise FileNotFoundError("Missing raw inputs: " + ", ".join(missing))
        build_course_dataset(paths.handouts, paths.courses)
        build_academic_regulations(paths.regulations_pdf, paths.regulations)
        build_programme_requirements(paths.bulletin_pdf, paths.programme_requirements)
        build_academic_rules(paths.regulations, paths.programme_requirements, paths.academic_rules)
        build_timetable(paths.timetable_pdf, paths.timetable)
    return validate_existing(paths)


def argument_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rebuild", action="store_true",
                        help="rebuild processed datasets from configurable raw inputs")
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw"))
    parser.add_argument("--processed-dir", type=Path, default=Path("data/processed"))
    return parser


def main(argv=None):
    args = argument_parser().parse_args(argv)
    raw, processed = args.raw_dir, args.processed_dir
    paths = PipelinePaths(
        handouts=raw / "handouts", regulations_pdf=raw / "Academic-Regulations-2023.pdf",
        bulletin_pdf=raw / "bulletin.pdf", timetable_pdf=raw / "timetable.pdf",
        courses=processed / "courses.json", regulations=processed / "academic_regulations.json",
        programme_requirements=processed / "programme_requirements.json",
        academic_rules=processed / "academic_rules.json", timetable=processed / "timetable.json",
        report=processed / "preprocessing_report.json")
    report = run_preprocessing(paths, rebuild=args.rebuild)
    print(json.dumps({"status": report["status"], **report["totals"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

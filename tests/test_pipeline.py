import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from preprocessing.pipeline import (PipelinePaths, main, run_preprocessing,
                                    validate_existing, validate_processed_datasets)


def source(filename="source.pdf"):
    return {"source_file": filename, "page_number": 1, "text": "evidence"}


def course(code="CS F111", verification=False, issues=None):
    return {
        "metadata": {"course_code": {"value": code, "sources": [source("handout.pdf")]}
                     if code is not None else None},
        "source": {"source_file": "handout.pdf", "page_numbers": [1]},
        "validation": {"is_valid": True, "needs_verification": verification,
                       "issues": issues or []},
    }


def multi_course(codes, expression="CE F434/BITS F494"):
    row = course(expression)
    row["metadata"]["course_codes"] = codes
    row["metadata"]["course_identity_type"] = "multiple"
    row["candidate_status"] = "usable"
    return row


def rule(code="CS F111", verification=False, issues=None):
    return {"course_code": code, "needs_verification": verification,
            "sources": [source("bulletin.pdf")],
            "validation": {"is_valid": True, "issues": issues or []}}


def section(code="CS F111", verification=False, issues=None):
    return {"course_code": code, "section": "L1", "meetings": [{"days": ["M"], "hours": [1]}],
            "needs_verification": verification, "sources": [source("timetable.pdf")],
            "validation": {"is_valid": True, "issues": issues or []}}


class PipelineTests(unittest.TestCase):
    def test_configurable_paths(self):
        paths = PipelinePaths(handouts=Path("replacement"), timetable_pdf=Path("new.pdf"))
        self.assertEqual(paths.handouts, Path("replacement"))
        self.assertEqual(paths.timetable_pdf, Path("new.pdf"))
        self.assertEqual(paths.report, Path("data/processed/preprocessing_report.json"))

    def test_matching_and_unmatched_codes(self):
        report = validate_processed_datasets(
            {"records": [course()]},
            {"records": [rule(), rule("MATH F111")]},
            {"records": [section(), section("BIO F101")]})
        self.assertEqual(report["course_codes"]["academic_rules"]["matched_values"], ["CS F111"])
        self.assertEqual(report["course_codes"]["academic_rules"]["unmatched_values"], ["MATH F111"])
        self.assertEqual(report["course_codes"]["timetable"]["unmatched_values"], ["BIO F101"])
        self.assertEqual(report["status"], "valid")

    def test_multi_code_course_identity_matches_both_codes(self):
        report = validate_processed_datasets(
            {"records": [multi_course(["CE F434", "BITS F494"])]},
            {"records": [rule("CE F434"), rule("BITS F494")]},
            {"records": [section("BITS F494")]})
        self.assertEqual(report["course_codes"]["courses"]["unique"], 2)
        self.assertEqual(report["course_codes"]["courses"]["multiple_identity_records"], 1)
        self.assertEqual(report["course_codes"]["academic_rules"]["unmatched"], 0)
        self.assertEqual(report["course_codes"]["timetable"]["unmatched"], 0)

    def test_verification_and_issue_aggregation(self):
        warning = {"code": "review", "severity": "warning"}
        error = {"code": "bad", "severity": "error"}
        report = validate_processed_datasets(
            {"records": [course(verification=True, issues=[warning])]},
            {"records": [rule(verification=True, issues=[warning])]},
            {"records": [section(verification=True, issues=[error])]})
        self.assertEqual(report["totals"]["needs_verification"], 3)
        self.assertGreaterEqual(report["totals"]["warnings"], 2)
        self.assertEqual(report["totals"]["errors"], 1)
        self.assertEqual(report["status"], "invalid")

    def test_missing_traceability_is_fatal(self):
        bad = section(); bad["sources"] = []
        report = validate_processed_datasets(
            {"records": [course()]}, {"records": [rule()]}, {"records": [bad]})
        self.assertEqual(report["totals"]["missing_source_traceability"], 1)
        self.assertEqual(report["status"], "invalid")

    def test_malformed_codes_reported_without_guessing(self):
        report = validate_processed_datasets(
            {"records": [course("CS F11")]}, {"records": [rule("CS F11")]},
            {"records": [section("CS F11")]})
        self.assertEqual(report["malformed_course_code_counts"],
                         {"courses": 1, "academic_rules": 1, "timetable": 1})
        self.assertEqual(report["course_codes"]["courses"]["unique"], 0)

    def test_empty_records_rejected(self):
        with self.assertRaisesRegex(ValueError, "courses"):
            validate_processed_datasets({"records": []}, {"records": [rule()]},
                                        {"records": [section()]})

    def test_load_existing_and_stable_report(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            files = {
                "courses.json": {"records": [course()]},
                "academic_regulations.json": {"rules": [{"id": "one"}]},
                "programme_requirements.json": {"requirements": [{"id": "one"}],
                                                 "unresolved_sections": []},
                "course_catalogue.json": {"records": [{"course_code": "CS F111"}],
                                           "summary": {"distinct_course_count": 1}},
                "academic_rules.json": {"records": [rule()]},
                "timetable.json": {"records": [section()]},
            }
            for name, value in files.items():
                (root / name).write_text(json.dumps(value), encoding="utf-8")
            paths = PipelinePaths(
                courses=root / "courses.json", regulations=root / "academic_regulations.json",
                programme_requirements=root / "programme_requirements.json",
                course_catalogue=root / "course_catalogue.json",
                academic_rules=root / "academic_rules.json", timetable=root / "timetable.json",
                report=root / "preprocessing_report.json")
            first = validate_existing(paths)
            first_bytes = paths.report.read_bytes()
            second = validate_existing(paths)
            self.assertEqual(first, second)
            self.assertEqual(first_bytes, paths.report.read_bytes())
            self.assertNotIn("timestamp", first)
            self.assertEqual(first["datasets_validated"], list(files))

    def test_missing_processed_input(self):
        with tempfile.TemporaryDirectory() as folder:
            paths = PipelinePaths(courses=Path(folder) / "missing.json")
            with self.assertRaisesRegex(FileNotFoundError, "Missing processed inputs"):
                validate_existing(paths)

    @patch("preprocessing.pipeline.validate_existing", return_value={"status": "valid"})
    @patch("preprocessing.pipeline.build_timetable")
    @patch("preprocessing.pipeline.build_academic_rules")
    @patch("preprocessing.pipeline.revalidate_programme_requirements_file")
    @patch("preprocessing.pipeline.build_source_course_catalogue")
    @patch("preprocessing.pipeline.build_programme_requirements")
    @patch("preprocessing.pipeline.build_academic_regulations")
    @patch("preprocessing.pipeline.build_course_dataset")
    def test_orchestration_order_and_paths(self, courses, regulations, requirements,
                                           catalogue, revalidate, rules, timetable, validate):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            handouts = root / "handouts"; handouts.mkdir()
            for name in ("reg.pdf", "bulletin.pdf", "timetable.pdf"):
                (root / name).touch()
            paths = PipelinePaths(
                handouts=handouts, regulations_pdf=root / "reg.pdf",
                bulletin_pdf=root / "bulletin.pdf", timetable_pdf=root / "timetable.pdf",
                courses=root / "courses.json", regulations=root / "regulations.json",
                programme_requirements=root / "requirements.json", academic_rules=root / "rules.json",
                course_catalogue=root / "course_catalogue.json",
                timetable=root / "timetable.json", report=root / "report.json")
            self.assertEqual(run_preprocessing(paths, rebuild=True), {"status": "valid"})
            courses.assert_called_once_with(paths.handouts, paths.courses)
            regulations.assert_called_once_with(paths.regulations_pdf, paths.regulations)
            requirements.assert_called_once_with(paths.bulletin_pdf, paths.programme_requirements)
            self.assertEqual(catalogue.call_count, 2)
            catalogue.assert_any_call(
                paths.courses, paths.programme_requirements, paths.course_catalogue)
            revalidate.assert_called_once_with(
                paths.programme_requirements, paths.course_catalogue)
            rules.assert_called_once_with(paths.regulations, paths.programme_requirements,
                                          paths.academic_rules)
            timetable.assert_called_once_with(paths.timetable_pdf, paths.timetable)
            validate.assert_called_once_with(paths)

    @patch("preprocessing.pipeline.run_preprocessing")
    def test_cli_paths(self, run):
        run.return_value = {"status": "valid", "totals": {"needs_verification": 0,
                            "warnings": 0, "errors": 0, "missing_source_traceability": 0}}
        with tempfile.TemporaryDirectory() as folder:
            raw, processed = Path(folder) / "raw", Path(folder) / "processed"
            self.assertEqual(main(["--raw-dir", str(raw), "--processed-dir", str(processed)]), 0)
            paths = run.call_args.args[0]
            self.assertEqual(paths.handouts, raw / "handouts")
            self.assertEqual(paths.report, processed / "preprocessing_report.json")
            self.assertFalse(run.call_args.kwargs["rebuild"])

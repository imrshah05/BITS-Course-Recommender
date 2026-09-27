import unittest

from backend.dashboard_options import DashboardOptionsService
from backend.source_course_catalogue import SourceCourseCatalogue
from preprocessing.source_course_catalogue import merge_source_course_catalogue


def source(filename, text="evidence"):
    return {"source_file": filename, "page_number": 1, "text": text}


def handout(code, title="Handout title"):
    return {
        "candidate_status": "usable",
        "metadata": {
            "course_codes": [code],
            "course_title": {"value": title, "sources": [source("handout.pdf")]},
            "department_division": None,
            "units": None,
        },
        "source": {"source_file": "handout.pdf", "page_numbers": [1]},
        "validation": {"needs_verification": False, "issues": []},
    }


def bulletin(code, title="Bulletin title", verification=False, issue=None):
    issues = [{"code": issue, "severity": "warning"}] if issue else []
    return {
        "id": "requirement-1", "kind": "listed_course",
        "course_code": code, "course_title": title, "units": 3,
        "category": "Explicit category", "programme_name": "Programme A",
        "year": None, "semester": None, "needs_verification": verification,
        "sources": [source("bulletin.pdf", f"{code} {title}")],
        "validation": {"is_valid": True, "issues": issues},
    }


class FakeRuleCatalogue:
    records = []

    def resolve_scope(self, programme):
        return []


class SourceCourseCatalogueTests(unittest.TestCase):
    def merge(self, handouts=None, bulletin_records=None):
        return merge_source_course_catalogue(
            {"records": handouts or []},
            {"requirements": bulletin_records or []})

    def test_bulletin_only_course_appears_in_selector(self):
        merged = self.merge(bulletin_records=[
            bulletin("CS F437", "Generative Artificial Intelligence")])
        options = DashboardOptionsService(
            SourceCourseCatalogue(merged), FakeRuleCatalogue()).options()
        self.assertEqual(options["courses"], [{
            "course_code": "CS F437",
            "course_title": "Generative Artificial Intelligence",
            "label": "CS F437 — Generative Artificial Intelligence",
        }])

    def test_handout_course_remains_available(self):
        merged = self.merge([handout("CS F111", "Computer Programming")])
        record = SourceCourseCatalogue(merged).get("CS F111")
        self.assertEqual(record["metadata"]["course_title"]["value"],
                         "Computer Programming")
        self.assertEqual(record["availability"], {"handout": True, "bulletin": False})

    def test_duplicate_identity_merges_sources(self):
        merged = self.merge(
            [handout("CS F211", "Data Structures & Algorithms")],
            [bulletin("CS F211", "Data Structures & Algorithms")])
        self.assertEqual(merged["summary"]["distinct_course_count"], 1)
        record = merged["records"][0]
        self.assertEqual(record["availability"], {"handout": True, "bulletin": True})
        self.assertEqual(len(record["provenance"]["handout_records"]), 1)
        self.assertEqual(len(record["provenance"]["bulletin_records"]), 1)

    def test_uncertain_identity_is_not_selectable(self):
        merged = self.merge(bulletin_records=[bulletin(
            "CS F999", verification=True, issue="ambiguous_course_identity")])
        self.assertTrue(merged["records"][0]["identity"]["needs_verification"])
        self.assertEqual(SourceCourseCatalogue(merged).list_courses(), [])

    def test_relationship_uncertainty_does_not_erase_course_existence(self):
        merged = self.merge(bulletin_records=[bulletin(
            "CS F211", "Data Structures & Algorithms", True,
            "course_not_validated_against_catalogue")])
        record = SourceCourseCatalogue(merged).get("CS F211")
        self.assertIsNotNone(record)
        self.assertTrue(record["uncertainty"][
            "bulletin_relationships_need_verification"])
        self.assertEqual(record["provenance"]["bulletin_records"][0]
                         ["sources"][0]["source_file"], "bulletin.pdf")


if __name__ == "__main__":
    unittest.main()

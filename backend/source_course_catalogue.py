"""Read the unified source-backed course identity catalogue."""

from copy import deepcopy
import json
from pathlib import Path


DEFAULT_PATH = Path(__file__).resolve().parents[1] / "data/processed/course_catalogue.json"
DEFAULT_HANDOUT_PATH = Path(__file__).resolve().parents[1] / "data/processed/courses.json"


class SourceCourseCatalogue:
    """Expose deterministic course identities for student-facing selection."""

    def __init__(self, dataset):
        if not isinstance(dataset, dict) or not isinstance(dataset.get("records"), list):
            raise ValueError("Source course catalogue must contain a records list")
        self.summary = deepcopy(dataset.get("summary") or {})
        self._records = {}
        for record in dataset["records"]:
            if not isinstance(record, dict):
                continue
            code = record.get("course_code")
            identity = record.get("identity") or {}
            if (isinstance(code, str) and code and
                    identity.get("needs_verification") is not True):
                self._records[code] = deepcopy(record)

    @classmethod
    def load(cls, path=DEFAULT_PATH):
        with Path(path).open(encoding="utf-8") as stream:
            return cls(json.load(stream))

    def list_courses(self):
        return [deepcopy(self._records[code]) for code in sorted(self._records)]

    def course_codes(self):
        return list(self._records)

    def get(self, code):
        value = self._records.get(code)
        return deepcopy(value) if value is not None else None

    def contains(self, code):
        return code in self._records

    def __len__(self):
        return len(self._records)


def source_backed_legacy_records(handout_path=DEFAULT_HANDOUT_PATH,
                                 source_path=DEFAULT_PATH):
    """Adapt deterministic Bulletin-only identities to the existing policy schema."""
    with Path(handout_path).open(encoding="utf-8") as stream:
        handouts = json.load(stream)
    records = deepcopy(handouts.get("records") or [])
    handout_codes = {
        code for record in records if isinstance(record, dict)
        for code in ((record.get("metadata") or {}).get("course_codes") or [])
        if isinstance(code, str)
    }
    source_catalogue = SourceCourseCatalogue.load(source_path)
    for course in source_catalogue.list_courses():
        code = course["course_code"]
        if code in handout_codes:
            continue
        bulletin_records = (course.get("provenance") or {}).get("bulletin_records") or []
        sources = [source for record in bulletin_records
                   for source in (record.get("sources") or [])
                   if isinstance(source, dict)]
        source_file = next((item.get("source_file") for item in sources
                            if item.get("source_file")), "bulletin.pdf")
        page_numbers = sorted({item.get("page_number") for item in sources
                               if type(item.get("page_number")) is int})
        title = ((course.get("metadata") or {}).get("course_title") or {}).get("value")
        units = ((course.get("metadata") or {}).get("units") or {}).get("value")
        records.append({
            "record_id": f"bulletin-course:{code}",
            "candidate_status": "usable",
            "metadata": {
                "course_code": {"value": code, "sources": deepcopy(sources)},
                "course_codes": [code], "course_identity_type": "single",
                "course_title": {"value": title, "sources": deepcopy(sources)},
                "department_division": {"value": None, "sources": []},
                "units": {"value": units, "sources": deepcopy(sources)},
            },
            # Absence remains unknown to the prerequisite engine.
            "prerequisites": None,
            "instructors": [], "syllabus": [], "evaluation": [],
            "exams": {"midsemester": None, "comprehensive": None},
            "attendance": [], "makeup": [],
            "source": {"source_file": source_file, "page_numbers": page_numbers},
            "observations": ["Course identity is Bulletin-backed; no handout is available."],
            "validation": {"is_valid": True, "needs_verification": False, "issues": []},
        })
    return records

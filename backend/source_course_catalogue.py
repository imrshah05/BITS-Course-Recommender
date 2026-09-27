"""Read the unified source-backed course identity catalogue."""

from copy import deepcopy
import json
from pathlib import Path


DEFAULT_PATH = Path(__file__).resolve().parents[1] / "data/processed/course_catalogue.json"


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

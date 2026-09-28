"""Indexed candidate-course catalogue built from processed handout records."""

from collections import defaultdict
from copy import deepcopy
from dataclasses import asdict, dataclass
import json
from pathlib import Path

from preprocessing.course_codes import normalize_course_code


DEFAULT_COURSES_PATH = Path(__file__).resolve().parents[1] / "data/processed/courses.json"
OPTIONAL_METADATA = ("course_title", "department_division", "units")
CONFLICT_FIELDS = OPTIONAL_METADATA


@dataclass(frozen=True)
class CatalogueIssue:
    code: str
    severity: str
    path: str
    message: str
    source_file: object = None
    course_code: object = None


class CourseCatalogue:
    """Reusable identity index retaining every usable source handout record."""

    def __init__(self, records):
        if not isinstance(records, list):
            raise ValueError("Course catalogue records must be a list")
        self.source_record_count = len(records)
        self.excluded_records = []
        self.shared_source_records = []
        self.verification_required_records = []
        self._issues = []
        indexed = defaultdict(list)

        for index, raw_record in enumerate(records):
            path = f"records[{index}]"
            if not isinstance(raw_record, dict):
                self._exclude(raw_record, index, "malformed_record",
                              "Course source record must be a mapping")
                continue
            record = deepcopy(raw_record)
            source_file = _source_file(record)
            metadata = record.get("metadata")
            if not isinstance(metadata, dict):
                self._exclude(record, index, "malformed_metadata",
                              "Course source record has no metadata mapping")
                continue
            identities = metadata.get("course_codes")
            if not isinstance(identities, list):
                self._exclude(record, index, "malformed_identity_collection",
                              "Course identities must be a list")
                continue
            status = record.get("candidate_status")
            if status != "usable":
                reason = "unusable_course_identity" if status == "unusable_identity" \
                    else "unsupported_candidate_status"
                self._exclude(record, index, reason,
                              "Source record has no reliable usable course identity")
                continue

            normalized = []
            for identity_index, identity in enumerate(identities):
                code = normalize_course_code(identity)
                if code is None:
                    self._issue("malformed_course_identity", "error",
                                f"{path}.metadata.course_codes[{identity_index}]",
                                f"Malformed course identity {identity!r}", source_file)
                elif code not in normalized:
                    normalized.append(code)
                else:
                    self._issue("duplicate_identity_in_record", "warning",
                                f"{path}.metadata.course_codes[{identity_index}]",
                                f"Course identity {code} is repeated in one source record",
                                source_file, code)
            if not normalized:
                self._exclude(record, index, "unusable_course_identity",
                              "Usable source record has no valid normalized identity")
                continue

            source_record = _source_record(record, index, normalized)
            if len(normalized) > 1:
                self.shared_source_records.append(deepcopy(source_record))
                self._issue("shared_multi_identity_record", "warning", path,
                            f"Source record explicitly represents {len(normalized)} identities",
                            source_file)
            validation = record.get("validation") or {}
            if validation.get("needs_verification"):
                self.verification_required_records.append(deepcopy(source_record))
                self._issue("source_record_needs_verification", "warning", path,
                            "Source record retains a Phase 2 verification requirement",
                            source_file)
            missing = [field for field in OPTIONAL_METADATA
                       if _field_value(metadata.get(field)) is None]
            if missing:
                self._issue("missing_optional_metadata", "warning", path,
                            f"Optional metadata is missing: {', '.join(missing)}",
                            source_file)
            for code in normalized:
                indexed[code].append(deepcopy(source_record))

        self._courses = {}
        for code in sorted(indexed):
            source_records = sorted(indexed[code], key=_record_sort_key)
            conflicts = _metadata_conflicts(source_records)
            if len(source_records) > 1:
                self._issue("multiple_source_records", "warning", f"courses.{code}",
                            f"Course identity {code} has {len(source_records)} source records",
                            course_code=code)
                self._issue("duplicate_normalized_identity", "warning", f"courses.{code}",
                            f"Normalized identity {code} occurs in multiple source records",
                            course_code=code)
            for field, values in conflicts.items():
                self._issue("conflicting_source_metadata", "warning",
                            f"courses.{code}.{field}",
                            f"Source records disagree on {field}: {values}",
                            course_code=code)
            self._courses[code] = {
                "normalized_course_code": code,
                "source_records": source_records,
                "source_record_count": len(source_records),
                "has_multiple_source_records": len(source_records) > 1,
                "has_shared_source_record": any(
                    item["course_identity_type"] == "multiple" for item in source_records),
                "needs_verification": any(item["needs_verification"]
                                          for item in source_records),
                "metadata_conflicts": conflicts,
            }

        self.excluded_records.sort(key=_record_sort_key)
        self.shared_source_records.sort(key=_record_sort_key)
        self.verification_required_records.sort(key=_record_sort_key)
        self.validation = self._validation()

    @classmethod
    def from_dict(cls, dataset):
        if not isinstance(dataset, dict) or not isinstance(dataset.get("records"), list):
            raise ValueError("Course catalogue must contain a records list")
        return cls(dataset["records"])

    @classmethod
    def load(cls, path=DEFAULT_COURSES_PATH):
        """Load and index existing processed JSON without reading source PDFs."""
        with Path(path).open(encoding="utf-8") as stream:
            return cls.from_dict(json.load(stream))

    @classmethod
    def load_source_backed(cls):
        """Load handout records plus deterministic Bulletin-only identities."""
        from backend.source_course_catalogue import source_backed_legacy_records
        return cls(source_backed_legacy_records())

    def get(self, course_code):
        code = normalize_course_code(course_code)
        course = self._courses.get(code) if code else None
        return deepcopy(course) if course is not None else None

    def contains(self, course_code):
        code = normalize_course_code(course_code)
        return bool(code and code in self._courses)

    def source_records_for(self, course_code):
        course = self.get(course_code)
        return course["source_records"] if course else []

    def list_courses(self):
        return [deepcopy(self._courses[code]) for code in sorted(self._courses)]

    def course_codes(self):
        return list(self._courses)

    def __len__(self):
        return len(self._courses)

    def __iter__(self):
        return iter(self.list_courses())

    def _exclude(self, record, index, reason, message):
        source_file = _source_file(record) if isinstance(record, dict) else None
        excluded = {
            "source_record_index": index,
            "source_file": source_file,
            "reason": reason,
            "record": deepcopy(record),
        }
        self.excluded_records.append(excluded)
        self._issue(reason, "error" if reason.startswith("malformed") else "warning",
                    f"records[{index}]", message, source_file)

    def _issue(self, code, severity, path, message, source_file=None, course_code=None):
        issue = CatalogueIssue(code, severity, path, message, source_file, course_code)
        if issue not in self._issues:
            self._issues.append(issue)

    def _validation(self):
        issues = [asdict(issue) for issue in self._issues]
        return {
            "is_valid": not any(issue["severity"] == "error" for issue in issues),
            "issues": issues,
            "error_count": sum(issue["severity"] == "error" for issue in issues),
            "warning_count": sum(issue["severity"] == "warning" for issue in issues),
            "summary": {
                "source_record_count": self.source_record_count,
                "candidate_identity_count": len(self._courses),
                "excluded_record_count": len(self.excluded_records),
                "shared_source_record_count": len(self.shared_source_records),
                "multiple_source_identity_count": sum(
                    course["has_multiple_source_records"] for course in self._courses.values()),
                "verification_required_record_count": len(
                    self.verification_required_records),
                "identity_with_conflicts_count": sum(
                    bool(course["metadata_conflicts"]) for course in self._courses.values()),
            },
        }


def _source_record(record, index, normalized):
    metadata = record["metadata"]
    validation = record.get("validation") or {}
    return {
        "source_record_index": index,
        "record_id": record.get("record_id") or _source_file(record) or f"record-{index}",
        "candidate_status": record.get("candidate_status"),
        "course_identity_type": metadata.get("course_identity_type"),
        "explicit_course_codes": normalized,
        "original_course_code": deepcopy(metadata.get("course_code")),
        "course_title": deepcopy(metadata.get("course_title")),
        "department_division": deepcopy(metadata.get("department_division")),
        "units": deepcopy(metadata.get("units")),
        "prerequisites": deepcopy(record.get("prerequisites")),
        "instructors": deepcopy(record.get("instructors")),
        "syllabus": deepcopy(record.get("syllabus")),
        "evaluation": deepcopy(record.get("evaluation")),
        "exams": deepcopy(record.get("exams")),
        "attendance": deepcopy(record.get("attendance")),
        "makeup": deepcopy(record.get("makeup")),
        "validation": deepcopy(validation),
        "needs_verification": bool(validation.get("needs_verification")),
        "source": deepcopy(record.get("source")),
        "observations": deepcopy(record.get("observations")),
    }


def _metadata_conflicts(records):
    conflicts = {}
    for field in CONFLICT_FIELDS:
        values = {}
        for record in records:
            value = _field_value(record.get(field))
            if value is not None:
                values[_stable(value)] = deepcopy(value)
        if len(values) > 1:
            conflicts[field] = [values[key] for key in sorted(values)]
    return conflicts


def _field_value(field):
    return field.get("value") if isinstance(field, dict) else field


def _stable(value):
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return repr(value)


def _source_file(record):
    source = record.get("source") or {}
    return source.get("source_file") if isinstance(source, dict) else None


def _record_sort_key(record):
    source = record.get("source") or {}
    return (str(record.get("source_file") or source.get("source_file") or ""),
            record.get("source_record_index", -1))

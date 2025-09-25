"""Normalize and validate student academic profiles."""

from dataclasses import asdict, dataclass, field
import math

from preprocessing.course_codes import normalize_course_code


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    severity: str
    path: str
    message: str


@dataclass
class CourseEntry:
    course_code: object
    normalized_course_code: object
    status: str
    grade: object = None
    units: object = None
    catalogue_status: str = "not_checked"
    reported_status: object = None

    def to_dict(self):
        return asdict(self)


@dataclass
class StudentProfile:
    programme: object
    second_programme: object
    current_academic_year: object
    current_semester: object
    completed_courses: list = field(default_factory=list)
    ongoing_courses: list = field(default_factory=list)
    validation: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data, known_course_codes=None):
        """Construct a normalized profile from JSON-like input."""
        if not isinstance(data, dict):
            raise TypeError("Student profile input must be a mapping")
        issues = []
        known = _normalize_known_codes(known_course_codes)

        programme = _clean_text(data.get("programme"))
        second_programme = _clean_text(data.get("second_programme"))
        year = _whole_number(data.get("current_academic_year"))
        semester = _whole_number(data.get("current_semester"))

        if programme is None:
            _issue(issues, "missing_programme", "error", "programme",
                   "A programme is required")
        if year is None or year < 1:
            _issue(issues, "invalid_academic_year", "error", "current_academic_year",
                   "Academic year must be a positive integer")
        if semester not in (1, 2):
            _issue(issues, "invalid_semester", "error", "current_semester",
                   "Semester must be 1 or 2")

        completed = _course_list(data.get("completed_courses", []), "completed",
                                 "completed_courses", known, issues)
        ongoing = _course_list(data.get("ongoing_courses", []), "ongoing",
                               "ongoing_courses", known, issues)
        _duplicates(completed, "completed_courses", issues)
        _duplicates(ongoing, "ongoing_courses", issues)

        completed_codes = {entry.normalized_course_code for entry in completed
                           if entry.normalized_course_code}
        ongoing_codes = {entry.normalized_course_code for entry in ongoing
                         if entry.normalized_course_code}
        for code in sorted(completed_codes & ongoing_codes):
            _issue(issues, "incompatible_course_status", "error", "courses",
                   f"{code} appears in both completed and ongoing courses")

        validation = {
            "is_valid": not any(issue.severity == "error" for issue in issues),
            "issues": [asdict(issue) for issue in issues],
            "error_count": sum(issue.severity == "error" for issue in issues),
            "warning_count": sum(issue.severity == "warning" for issue in issues),
        }
        return cls(programme, second_programme, year, semester,
                   completed, ongoing, validation)

    def to_dict(self):
        """Return a deterministic JSON-serializable representation."""
        return {
            "programme": self.programme,
            "second_programme": self.second_programme,
            "current_academic_year": self.current_academic_year,
            "current_semester": self.current_semester,
            "completed_courses": [entry.to_dict() for entry in self.completed_courses],
            "ongoing_courses": [entry.to_dict() for entry in self.ongoing_courses],
            "validation": self.validation,
        }


def normalize_student_profile(data, known_course_codes=None):
    """Normalize and validate a profile, returning JSON-serializable data."""
    return StudentProfile.from_dict(data, known_course_codes).to_dict()


def _clean_text(value):
    if not isinstance(value, str):
        return None
    value = " ".join(value.split())
    return value or None


def _whole_number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _normalize_known_codes(values):
    if values is None:
        return None
    normalized = set()
    for value in values:
        code = normalize_course_code(value)
        if code:
            normalized.add(code)
    return normalized


def _course_list(value, expected_status, path, known, issues):
    if not isinstance(value, list):
        _issue(issues, "invalid_course_collection", "error", path,
               "Course collection must be a list")
        return []
    entries = []
    for index, raw in enumerate(value):
        item_path = f"{path}[{index}]"
        if isinstance(raw, str):
            raw = {"course_code": raw}
        if not isinstance(raw, dict):
            _issue(issues, "invalid_course_entry", "error", item_path,
                   "Course entry must be a mapping or course-code string")
            continue

        supplied_code = raw.get("course_code")
        preserved_code = supplied_code.strip() if isinstance(supplied_code, str) else supplied_code
        normalized_code = normalize_course_code(supplied_code)
        if normalized_code is None:
            _issue(issues, "malformed_course_code", "error", item_path + ".course_code",
                   "Course code does not match a supported source-backed form")
            catalogue_status = "malformed"
        elif known is None:
            catalogue_status = "not_checked"
        elif normalized_code in known:
            catalogue_status = "known"
        else:
            catalogue_status = "unknown"
            _issue(issues, "unknown_course_code", "warning", item_path + ".course_code",
                   f"{normalized_code} is syntactically valid but absent from the supplied catalogue")

        reported_status = _clean_text(raw.get("status"))
        if reported_status is not None:
            reported_status = reported_status.casefold()
            if reported_status not in ("completed", "ongoing"):
                _issue(issues, "invalid_course_status", "error", item_path + ".status",
                       "Course status must be completed or ongoing")
            elif reported_status != expected_status:
                _issue(issues, "incompatible_course_status", "error", item_path + ".status",
                       f"Course is listed as {expected_status} but reports {reported_status}")

        grade = raw.get("grade")
        if grade is not None:
            if not isinstance(grade, str) or not grade.strip():
                _issue(issues, "malformed_grade", "error", item_path + ".grade",
                       "Grade must be a non-empty string when supplied")
            else:
                grade = grade.strip().upper()

        units = raw.get("units")
        if units is not None:
            if isinstance(units, bool) or not isinstance(units, (int, float)) \
                    or not math.isfinite(units) or units < 0:
                _issue(issues, "malformed_units", "error", item_path + ".units",
                       "Units must be a finite non-negative number when supplied")
            elif isinstance(units, float) and units.is_integer():
                units = int(units)

        entries.append(CourseEntry(preserved_code, normalized_code, expected_status,
                                   grade, units, catalogue_status, reported_status))
    return entries


def _duplicates(entries, path, issues):
    seen = set()
    reported = set()
    for entry in entries:
        code = entry.normalized_course_code
        if code and code in seen and code not in reported:
            _issue(issues, "duplicate_course", "error", path,
                   f"{code} appears more than once")
            reported.add(code)
        if code:
            seen.add(code)


def _issue(issues, code, severity, path, message):
    issues.append(ValidationIssue(code, severity, path, message))

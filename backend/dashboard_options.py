"""Read-only dashboard choices derived from processed academic data."""

from backend.programme_requirements import AcademicRuleCatalogue
from backend.source_course_catalogue import SourceCourseCatalogue


class DashboardOptionsService:
    """Build stable form options without changing academic records."""

    def __init__(self, course_catalogue=None, rule_catalogue=None):
        self.course_catalogue = course_catalogue or SourceCourseCatalogue.load()
        self.rule_catalogue = rule_catalogue or AcademicRuleCatalogue.load()

    def options(self):
        programmes = sorted({
            programme.strip()
            for rule in self.rule_catalogue.records
            if isinstance(rule, dict)
            for programme in [(rule.get("scope") or {}).get("programme")]
            if isinstance(programme, str) and programme.strip()
            and self.rule_catalogue.resolve_scope(programme) == [programme]
        }, key=str.casefold)
        courses = []
        for course in self.course_catalogue.list_courses():
            code = course.get("course_code") or course.get("normalized_course_code")
            title = _course_title(course)
            courses.append({
                "course_code": code,
                "course_title": title,
                "label": f"{code} — {title}" if title else code,
            })
        return {
            "programmes": programmes,
            "academic_years": list(range(1, 7)),
            "semesters": [1, 2],
            "courses": courses,
            "summary": {
                "programme_count": len(programmes),
                "course_count": len(courses),
            },
        }


def _course_title(course):
    metadata = course.get("metadata") if isinstance(course, dict) else None
    title = (metadata or {}).get("course_title") if isinstance(metadata, dict) else None
    if isinstance(title, dict):
        value = title.get("value")
        if isinstance(value, str) and value.strip():
            return value.strip()
    source_records = course.get("source_records") or []
    titles = []
    for record in source_records:
        title = record.get("course_title") if isinstance(record, dict) else None
        if isinstance(title, dict):
            title = title.get("value")
        if isinstance(title, str) and title.strip() and title.strip() not in titles:
            titles.append(title.strip())
    return titles[0] if len(titles) == 1 else None

"""Build recommendation-safe candidate pools from deterministic eligibility."""

from copy import deepcopy
from dataclasses import asdict, dataclass

from backend.course_catalogue import CourseCatalogue
from backend.eligibility import ELIGIBILITY_STATES, EligibilityEngine
from preprocessing.course_codes import normalize_course_code


POOL_STATES = ("confirmed", "verification_required", "excluded")
RELEVANCE_STATES = ("explicit", "not_established", "ambiguous")
REQUIREMENT_COLLECTIONS = (
    "satisfied_requirements",
    "partially_satisfied_requirements",
    "remaining_requirements",
    "unevaluable_requirements",
)


@dataclass(frozen=True)
class CandidatePoolIssue:
    code: str
    severity: str
    path: str
    message: str


class CandidatePoolBuilder:
    """Combine a reusable catalogue, eligibility engine, and explicit requirements."""

    def __init__(self, catalogue, eligibility_engine=None):
        if not isinstance(catalogue, CourseCatalogue):
            catalogue = CourseCatalogue.from_dict(catalogue)
        self.catalogue = catalogue
        self.eligibility_engine = eligibility_engine or EligibilityEngine(catalogue)

    def build(self, academic_history, academic_requirements=None,
              restrictions_by_course=None):
        """Build three disjoint pools without weakening eligibility decisions."""
        eligibility = self.eligibility_engine.evaluate_all(
            academic_history, restrictions_by_course)
        relevance = _requirement_relevance_index(academic_requirements)
        pools = {state: [] for state in POOL_STATES}

        for eligibility_state in ELIGIBILITY_STATES:
            for result in eligibility.get("groups", {}).get(eligibility_state, []):
                entry = _candidate_entry(self.catalogue, result, relevance)
                pools[entry["candidate_pool_state"]].append(entry)

        for entries in pools.values():
            entries.sort(key=lambda item: item.get("normalized_course_code") or "")
        output = {
            "confirmed_candidates": pools["confirmed"],
            "verification_required_candidates": pools["verification_required"],
            "excluded_candidates": pools["excluded"],
            "eligibility_summary": deepcopy(eligibility.get("summary") or {}),
        }
        output["validation"] = validate_candidate_pool(output)
        output["summary"] = _summary(output)
        return output


def filter_candidates(pool, pool_state=None, normalized_category=None,
                      programme_scope=None, manual_verification=None):
    """Filter candidate entries using structured fields only."""
    if not isinstance(pool, dict):
        raise TypeError("Candidate pool must be a mapping")
    if pool_state is not None and pool_state not in POOL_STATES:
        raise ValueError(f"Unsupported candidate pool state: {pool_state}")
    collections = {
        "confirmed": "confirmed_candidates",
        "verification_required": "verification_required_candidates",
        "excluded": "excluded_candidates",
    }
    names = [collections[pool_state]] if pool_state else list(collections.values())
    entries = [entry for name in names for entry in pool.get(name, [])
               if isinstance(entry, dict)]
    if normalized_category is not None:
        entries = [entry for entry in entries
                   if normalized_category in entry.get("relevant_normalized_categories", [])]
    if programme_scope is not None:
        entries = [entry for entry in entries
                   if programme_scope in entry.get("relevant_programme_scopes", [])]
    if manual_verification is not None:
        entries = [entry for entry in entries
                   if bool(entry.get("requires_manual_verification")) is manual_verification]
    return deepcopy(entries)


def validate_candidate_pool(pool):
    """Validate pool-state safety, identity uniqueness, and traceability."""
    issues = []
    seen = {}
    collections = {
        "confirmed_candidates": "confirmed",
        "verification_required_candidates": "verification_required",
        "excluded_candidates": "excluded",
    }
    for collection, expected_pool_state in collections.items():
        entries = pool.get(collection, []) if isinstance(pool, dict) else []
        if not isinstance(entries, list):
            _issue(issues, "malformed_candidate_collection", "error", collection,
                   "Candidate collection must be a list")
            continue
        for index, entry in enumerate(entries):
            path = f"{collection}[{index}]"
            if not isinstance(entry, dict):
                _issue(issues, "malformed_candidate_entry", "error", path,
                       "Candidate entry must be a mapping")
                continue
            code = normalize_course_code(entry.get("normalized_course_code"))
            if code is None:
                _issue(issues, "candidate_identity_missing", "error", path,
                       "Candidate has no valid normalized identity")
            elif code in seen:
                _issue(issues, "duplicate_candidate_identity", "error", path,
                       f"Candidate identity {code} also occurs at {seen[code]}")
            else:
                seen[code] = path
            state = entry.get("candidate_pool_state")
            if state not in POOL_STATES:
                _issue(issues, "malformed_candidate_pool_state", "error", path,
                       f"Unsupported candidate pool state {state!r}")
            elif state != expected_pool_state:
                _issue(issues, "candidate_collection_mismatch", "error", path,
                       "Candidate pool state does not match its collection")
            eligibility = entry.get("eligibility_result")
            if not isinstance(eligibility, dict):
                _issue(issues, "missing_eligibility_result", "error", path,
                       "Candidate has no structured eligibility result")
                eligibility_state = None
            else:
                eligibility_state = entry.get("eligibility_state")
            expected = _pool_state(eligibility_state)
            if state in POOL_STATES and state != expected:
                _issue(issues, "eligibility_pool_state_mismatch", "error", path,
                       "Eligibility state is inconsistent with candidate pool state")
            relevance = entry.get("requirement_relevance")
            if not isinstance(relevance, dict) or relevance.get("state") not in RELEVANCE_STATES:
                _issue(issues, "malformed_requirement_relevance", "error", path,
                       "Candidate has malformed requirement relevance")
            if not entry.get("source_records") or not entry.get("source_references"):
                _issue(issues, "source_traceability_lost", "error", path,
                       "Candidate source traceability is unavailable")

    errors = [asdict(issue) for issue in issues if issue.severity == "error"]
    warnings = [asdict(issue) for issue in issues if issue.severity == "warning"]
    return {
        "is_valid": not errors,
        "issues": errors + warnings,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "duplicate_candidate_identity_count": sum(
            issue.code == "duplicate_candidate_identity" for issue in issues),
    }


def _candidate_entry(catalogue, result, relevance_index):
    result = deepcopy(result) if isinstance(result, dict) else {}
    raw_state = result.get("eligibility_state")
    state = raw_state if raw_state in ELIGIBILITY_STATES else "ambiguous"
    code = normalize_course_code(result.get("normalized_course_code"))
    candidate = catalogue.get(code) if code else None
    relationships = deepcopy(relevance_index.get("explicit", {}).get(code, []))
    unresolved = deepcopy(relevance_index.get("ambiguous", {}).get(code, []))
    if unresolved:
        relevance_state = "ambiguous"
    elif relationships:
        relevance_state = "explicit"
    else:
        relevance_state = "not_established"
    local_issues = []
    if raw_state not in ELIGIBILITY_STATES:
        _issue(local_issues, "malformed_eligibility_state", "error", "eligibility_state",
               f"Unsupported eligibility state {raw_state!r}")
    if candidate is None:
        _issue(local_issues, "candidate_identity_missing", "error", "course_identity",
               "Catalogue identity could not be resolved")
    reason_codes = list(dict.fromkeys((result.get("reason_codes") or []) +
                                      [issue.code for issue in local_issues]))
    source_records = deepcopy((candidate or {}).get("source_records") or [])
    entry = {
        "normalized_course_code": code,
        "course_identity": deepcopy(candidate),
        "source_records": source_records,
        "eligibility_state": state,
        "eligibility_result": result,
        "candidate_pool_state": _pool_state(state),
        "requirement_relevance": {
            "state": relevance_state,
            "relationships": relationships,
            "unresolved_relationships": unresolved,
        },
        "relevant_programme_scopes": sorted({item["programme_scope"]
                                               for item in relationships}),
        "relevant_normalized_categories": sorted({item["normalized_category"]
                                                    for item in relationships}),
        "unresolved_requirement_relationships": unresolved,
        "requires_manual_verification": state in ("unknown", "ambiguous") or
                                        relevance_state == "ambiguous",
        "reason_codes": reason_codes,
        "validation": _entry_validation(local_issues, result.get("validation")),
        "source_references": _source_references(source_records, relationships, unresolved),
    }
    return entry


def _pool_state(eligibility_state):
    if eligibility_state == "eligible":
        return "confirmed"
    if eligibility_state == "ineligible":
        return "excluded"
    return "verification_required"


def _requirement_relevance_index(summary):
    index = {"explicit": {}, "ambiguous": {}}
    if summary is None:
        return index
    if not isinstance(summary, dict):
        return index
    progress = summary.get("requirement_progress") or summary
    programmes = progress.get("programme_progress") or []
    if not isinstance(programmes, list):
        return index
    category_requirements = {}
    for programme in programmes:
        if not isinstance(programme, dict):
            continue
        scope = programme.get("programme")
        role = programme.get("requested_programme_role")
        categories = programme.get("categories") or []
        for category in categories if isinstance(categories, list) else []:
            if not isinstance(category, dict):
                continue
            normalized_category = category.get("normalized_category")
            for collection in REQUIREMENT_COLLECTIONS:
                records = category.get(collection) or []
                for record in records if isinstance(records, list) else []:
                    _index_requirement(index, record, scope, role,
                                       normalized_category, collection)
                    if (record.get("rule_type") == "category_total" and
                            collection in ("remaining_requirements",
                                           "partially_satisfied_requirements")):
                        category_requirements.setdefault(
                            (scope, normalized_category), []).append(
                                (record, role, collection))
        unresolved = programme.get("uncategorized_requirements") or []
        for record in unresolved if isinstance(unresolved, list) else []:
            _index_requirement(index, record, scope, role, None,
                               "uncategorized_requirements")
    for item in progress.get("unresolved_requirements") or []:
        if isinstance(item, dict):
            record = item.get("record") if isinstance(item.get("record"), dict) else item
            scope = (record.get("scope") or {}).get("programme")
            _index_requirement(index, record, scope,
                               record.get("requested_programme_role"),
                               record.get("normalized_category"),
                               "unresolved_requirements", force_ambiguous=True)
    descriptive = summary.get("descriptive_information") or []
    for membership in descriptive if isinstance(descriptive, list) else []:
        if not isinstance(membership, dict) or membership.get("rule_type") != "elective_membership":
            continue
        scope = (membership.get("scope") or {}).get("programme")
        category = membership.get("normalized_category")
        targets = category_requirements.get((scope, category), [])
        if len(targets) != 1:
            continue
        requirement, role, collection = targets[0]
        code = normalize_course_code(membership.get("course_code"))
        if not code:
            continue
        relationship = {
            "rule_id": requirement.get("rule_id"),
            "programme_scope": scope,
            "requested_programme_role": role,
            "normalized_category": category,
            "completion_status": requirement.get("completion_status"),
            "relationship_source": collection,
            "sources": deepcopy(requirement.get("sources") or []),
            "membership_rule_id": membership.get("rule_id"),
            "membership_sources": deepcopy(membership.get("sources") or []),
            "membership_basis": "source_backed_discipline_elective_list",
        }
        index["explicit"].setdefault(code, []).append(relationship)
    return index


def _index_requirement(index, record, scope, role, category, collection,
                       force_ambiguous=False):
    if not isinstance(record, dict):
        return
    codes = []
    direct = normalize_course_code(record.get("course_code"))
    if direct:
        codes.append(direct)
    alternatives = record.get("alternatives")
    if isinstance(alternatives, dict) and isinstance(alternatives.get("options"), list):
        for option in alternatives["options"]:
            code = normalize_course_code(option)
            if code and code not in codes:
                codes.append(code)
    if not codes:
        return
    relationship = {
        "rule_id": record.get("rule_id"),
        "programme_scope": scope,
        "requested_programme_role": role,
        "normalized_category": category,
        "completion_status": record.get("completion_status"),
        "relationship_source": collection,
        "sources": deepcopy(record.get("sources") or []),
    }
    explicit = all(isinstance(value, str) and value.strip()
                   for value in (scope, role, category)) and not force_ambiguous
    bucket = "explicit" if explicit else "ambiguous"
    for code in codes:
        if relationship not in index[bucket].setdefault(code, []):
            index[bucket][code].append(deepcopy(relationship))


def _source_references(source_records, relationships, unresolved):
    references = []
    for record in source_records:
        source = record.get("source") or {}
        reference = {"source_file": source.get("source_file"),
                     "page_numbers": deepcopy(source.get("page_numbers") or [])}
        if reference not in references:
            references.append(reference)
    for relationship in relationships + unresolved:
        for source in ((relationship.get("sources") or []) +
                       (relationship.get("membership_sources") or [])):
            reference = deepcopy(source)
            if reference not in references:
                references.append(reference)
    return references


def _entry_validation(issues, eligibility_validation):
    combined = [asdict(issue) for issue in issues]
    for issue in (eligibility_validation or {}).get("issues", []):
        if issue not in combined:
            combined.append(deepcopy(issue))
    errors = [issue for issue in combined if issue.get("severity") == "error"]
    warnings = [issue for issue in combined if issue.get("severity") == "warning"]
    return {"is_valid": not errors, "issues": combined,
            "error_count": len(errors), "warning_count": len(warnings)}


def _summary(output):
    confirmed = output["confirmed_candidates"]
    verification = output["verification_required_candidates"]
    excluded = output["excluded_candidates"]
    entries = confirmed + verification + excluded
    relevance_counts = {state: 0 for state in RELEVANCE_STATES}
    for entry in entries:
        relevance_counts[entry["requirement_relevance"]["state"]] += 1
    return {
        "total_candidate_count": len(entries),
        "confirmed_candidate_count": len(confirmed),
        "verification_required_candidate_count": len(verification),
        "excluded_candidate_count": len(excluded),
        "requirement_relevance_counts": relevance_counts,
        "duplicate_candidate_identity_count": output["validation"].get(
            "duplicate_candidate_identity_count", 0),
        "unconfirmed_eligibility_in_confirmed_pool_count": sum(
            entry.get("eligibility_state") != "eligible" for entry in confirmed),
    }


def _issue(issues, code, severity, path, message):
    issue = CandidatePoolIssue(code, severity, path, message)
    if issue not in issues:
        issues.append(issue)

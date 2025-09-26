"""Conservative evaluation of explicit structured course prerequisites."""

from collections import Counter
from copy import deepcopy
from dataclasses import asdict, dataclass
import json

from backend.course_catalogue import CourseCatalogue
from preprocessing.course_codes import normalize_course_code


STATES = {"satisfied", "not_satisfied", "no_explicit_prerequisite", "unknown", "ambiguous"}


@dataclass(frozen=True)
class PrerequisiteIssue:
    code: str
    severity: str
    path: str
    message: str


class PrerequisiteEngine:
    """Evaluate source-backed prerequisite structures against Phase 3 history."""

    def __init__(self, catalogue):
        if not isinstance(catalogue, CourseCatalogue):
            catalogue = CourseCatalogue.from_dict(catalogue)
        self.catalogue = catalogue
        self._unusable_identities = _excluded_identities(catalogue.excluded_records)

    def evaluate(self, course_code, academic_history):
        if not isinstance(academic_history, dict):
            raise TypeError("Academic history must be a mapping")
        issues = []
        normalized = normalize_course_code(course_code)
        if normalized is None:
            _issue(issues, "malformed_target_course", "error", "course_code",
                   "Target course code is malformed")
            return _result(course_code, None, "malformed", "unknown", issues)

        candidate = self.catalogue.get(normalized)
        if candidate is None:
            target_status = "unusable_identity" if normalized in self._unusable_identities else "unknown"
            code = "unusable_target_identity" if target_status == "unusable_identity" \
                else "target_course_not_found"
            _issue(issues, code, "warning", "course_code",
                   f"No usable candidate identity is available for {normalized}")
            return _result(course_code, normalized, target_status, "unknown", issues)

        completed, ongoing = _history_codes(academic_history, issues)
        claims = []
        malformed = []
        for index, source_record in enumerate(candidate["source_records"]):
            raw = source_record.get("prerequisites")
            if raw is None:
                continue
            claim, reason = _parse_claim(raw, source_record)
            if reason:
                reason_code = ("unresolved_prerequisite_information"
                               if isinstance(raw, dict) and raw.get("kind") == "unresolved"
                               else "malformed_prerequisite_structure")
                malformed.append({
                    "source_record": _source_reference(source_record),
                    "prerequisites": deepcopy(raw),
                    "reason_code": reason_code,
                    "reason": reason,
                })
                _issue(issues, reason_code, "warning",
                       f"source_records[{index}].prerequisites", reason)
            else:
                claims.append(claim)

        if not claims and not malformed:
            _issue(issues, "prerequisite_information_absent", "warning", "prerequisites",
                   "No structured prerequisite information is available")
            return _result(course_code, normalized, "resolved", "unknown", issues,
                           candidate=candidate)
        if malformed:
            reasons = sorted({item["reason_code"] for item in malformed})
            return _result(course_code, normalized, "resolved", "ambiguous", issues,
                           candidate=candidate, ambiguity={
                               "reasons": reasons,
                               "records": malformed,
                           })

        signatures = {}
        for claim in claims:
            signatures.setdefault(_claim_signature(claim), []).append(claim)
        if len(signatures) > 1:
            _issue(issues, "conflicting_prerequisite_information", "warning", "prerequisites",
                   "Source records provide conflicting prerequisite structures")
            return _result(course_code, normalized, "resolved", "ambiguous", issues,
                           candidate=candidate, requirements=claims,
                           ambiguity={"reasons": ["conflicting_prerequisite_information"],
                                      "records": claims})

        compatible = next(iter(signatures.values()))
        if any(claim["needs_verification"] for claim in compatible):
            _issue(issues, "prerequisite_evidence_needs_verification", "warning",
                   "prerequisites", "Prerequisite-specific evidence requires verification")
            return _result(course_code, normalized, "resolved", "ambiguous", issues,
                           candidate=candidate, requirements=compatible,
                           ambiguity={"reasons": ["prerequisite_evidence_needs_verification"],
                                      "records": compatible})

        claim = compatible[0]
        evidence = [item for compatible_claim in compatible
                    for item in compatible_claim["evidence"]]
        if claim["kind"] == "none":
            return _result(course_code, normalized, "resolved",
                           "no_explicit_prerequisite", issues, candidate=candidate,
                           requirements={"kind": "none", "evidence": evidence})

        required = claim["course_codes"]
        completed_matches = sorted(set(required) & completed)
        ongoing_matches = sorted((set(required) & ongoing) - set(completed_matches))
        if claim["operator"] == "all":
            satisfied = len(completed_matches) == len(required)
            unmet = [code for code in required if code not in completed]
        else:
            satisfied = bool(completed_matches)
            unmet = [] if satisfied else list(required)
        return _result(
            course_code, normalized, "resolved",
            "satisfied" if satisfied else "not_satisfied", issues,
            candidate=candidate,
            requirements={"kind": "courses", "operator": claim["operator"],
                          "course_codes": required, "evidence": evidence},
            completed_matches=completed_matches,
            ongoing_matches=ongoing_matches,
            unmet=unmet,
        )

    def analyse_catalogue(self):
        """Return prerequisite coverage using an empty, valid academic history."""
        counts = Counter()
        verification_required = 0
        examples = {state: [] for state in STATES}
        empty_history = {"completed_courses": [], "ongoing_courses": []}
        for code in self.catalogue.course_codes():
            result = self.evaluate(code, empty_history)
            state = result["prerequisite_state"]
            counts[state] += 1
            if any(isinstance(record.get("prerequisites"), dict) and
                   record["prerequisites"].get("needs_verification")
                   for record in result["source_records"]):
                verification_required += 1
            if len(examples[state]) < 3:
                examples[state].append(code)
        return {
            "total_candidate_identities": len(self.catalogue),
            "state_counts": {state: counts[state] for state in sorted(STATES)},
            "machine_evaluable_count": counts["satisfied"] + counts["not_satisfied"],
            "explicit_no_prerequisite_count": counts["no_explicit_prerequisite"],
            "unknown_count": counts["unknown"],
            "ambiguous_count": counts["ambiguous"],
            "prerequisite_bearing_verification_required_count": verification_required,
            "examples": {state: values for state, values in examples.items() if values},
        }


def _parse_claim(raw, source_record):
    if not isinstance(raw, dict):
        return None, "Prerequisite information must be a structured mapping"
    kind = raw.get("kind")
    evidence = deepcopy(raw.get("sources") or [])
    if not isinstance(evidence, list):
        return None, "Prerequisite sources must be a list"
    evidence.append(_source_reference(source_record))
    common = {
        "kind": kind,
        "needs_verification": bool(raw.get("needs_verification")),
        "evidence": evidence,
    }
    if kind == "none":
        return common, None
    if kind == "unresolved":
        return None, "Source-backed prerequisite text is not machine-evaluable"
    if kind != "courses":
        return None, "Prerequisite kind must be 'none' or 'courses'"
    operator = raw.get("operator")
    values = raw.get("course_codes")
    if operator not in ("all", "any"):
        return None, "Course prerequisites require an explicit 'all' or 'any' operator"
    if not isinstance(values, list) or not values:
        return None, "Course prerequisites require a non-empty course_codes list"
    codes = []
    for value in values:
        code = normalize_course_code(value)
        if code is None:
            return None, f"Malformed prerequisite course identity {value!r}"
        if code not in codes:
            codes.append(code)
    common.update({"operator": operator, "course_codes": sorted(codes)})
    return common, None


def _claim_signature(claim):
    if claim["kind"] == "none":
        return "none"
    return json.dumps({"kind": claim["kind"], "operator": claim["operator"],
                       "course_codes": claim["course_codes"]}, sort_keys=True)


def _history_codes(history, issues):
    completed = _course_codes(history.get("completed_courses", []), "completed_courses", issues)
    ongoing = _course_codes(history.get("ongoing_courses", []), "ongoing_courses", issues)
    return completed, ongoing


def _course_codes(entries, path, issues):
    if not isinstance(entries, list):
        _issue(issues, "malformed_academic_history", "error", path,
               "Academic history course collection must be a list")
        return set()
    codes = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            _issue(issues, "malformed_academic_history", "error", f"{path}[{index}]",
                   "Academic history course must be a normalized mapping")
            continue
        code = normalize_course_code(entry.get("normalized_course_code"))
        if code:
            codes.add(code)
    return codes


def _excluded_identities(excluded_records):
    identities = set()
    for excluded in excluded_records:
        record = excluded.get("record") if isinstance(excluded, dict) else None
        metadata = record.get("metadata") if isinstance(record, dict) else None
        values = metadata.get("course_codes") if isinstance(metadata, dict) else None
        if isinstance(values, list):
            for value in values:
                code = normalize_course_code(value)
                if code:
                    identities.add(code)
    return identities


def _source_reference(source_record):
    source = source_record.get("source") or {}
    return {
        "record_id": source_record.get("record_id"),
        "source_file": source.get("source_file"),
        "page_numbers": deepcopy(source.get("page_numbers") or []),
    }


def _result(requested, normalized, target_status, state, issues, candidate=None,
            requirements=None, completed_matches=None, ongoing_matches=None,
            unmet=None, ambiguity=None):
    validation = {
        "is_valid": not any(issue.severity == "error" for issue in issues),
        "issues": [asdict(issue) for issue in issues],
        "error_count": sum(issue.severity == "error" for issue in issues),
        "warning_count": sum(issue.severity == "warning" for issue in issues),
    }
    return {
        "requested_course_code": requested,
        "normalized_course_code": normalized,
        "target_resolution_status": target_status,
        "prerequisite_state": state,
        "prerequisite_requirements": deepcopy(requirements),
        "completed_matches": completed_matches or [],
        "ongoing_matches": ongoing_matches or [],
        "unmet_prerequisites": unmet or [],
        "ambiguity": deepcopy(ambiguity),
        "source_records": deepcopy(candidate.get("source_records") or []) if candidate else [],
        "validation": validation,
    }


def _issue(issues, code, severity, path, message):
    issue = PrerequisiteIssue(code, severity, path, message)
    if issue not in issues:
        issues.append(issue)

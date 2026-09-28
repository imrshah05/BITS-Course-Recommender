"""Evaluate source-backed Open Elective and cross-discipline policy."""

from copy import deepcopy
import json
from pathlib import Path
import re

from preprocessing.course_codes import normalize_course_code


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REQUIREMENTS = ROOT / "data/processed/programme_requirements.json"
DEFAULT_REGULATIONS = ROOT / "data/processed/academic_regulations.json"
REQUIRED_POLICIES = {
    "open_elective_host_region", "cross_discipline_prior_preparation",
    "course_prerequisite_independent", "dual_degree_del_to_opel",
}
COLLECTION_STATES = {
    "satisfied_requirements": "satisfied",
    "partially_satisfied_requirements": "partially_satisfied",
    "remaining_requirements": "remaining",
    "unevaluable_requirements": "unevaluable",
}


class ElectivePolicyEngine:
    """Keep OPEL accounting and registration eligibility as separate decisions."""

    def __init__(self, requirements=None, regulations=None):
        self.requirements = requirements or _load(DEFAULT_REQUIREMENTS)
        self.regulations = regulations or _load(DEFAULT_REGULATIONS)
        policies = self.regulations.get("structured_policies") or []
        self.policies = {item.get("rule_type"): item for item in policies
                         if isinstance(item, dict) and
                         (item.get("validation") or {}).get("is_valid") is True and
                         item.get("needs_verification") is not True}
        self.enabled = REQUIRED_POLICIES <= set(self.policies)
        self.programmes = [item for item in self.requirements.get("programmes") or []
                           if isinstance(item, dict)]
        self.records = [item for item in self.requirements.get("requirements") or []
                        if isinstance(item, dict)]
        self.memberships = _memberships(self.records, self.programmes)
        self.discipline_aliases = _discipline_aliases(self.records)

    def add_open_elective_relationships(self, index, academic_requirements):
        """Add potential OPEL relationships without asserting eligibility."""
        if not self.enabled:
            return
        categories, requested = _category_requirements(academic_requirements)
        own_scopes = {_key(item["programme"]): item["programme"] for item in requested}
        if not own_scopes:
            return
        for code, memberships in self.memberships.items():
            member_scopes = {_key(item["programme_scope"]): item for item in memberships}
            belongs_to_own = set(member_scopes) & set(own_scopes)
            outside = self._outside_own_disciplines(memberships, own_scopes)
            for target in requested:
                target_scope = target["programme"]
                requirement = categories.get((target_scope, "open_elective"))
                if requirement is None:
                    continue
                basis = None
                membership = None
                own_del = [item for item in memberships
                           if _key(item["programme_scope"]) == _key(target_scope)
                           and item["normalized_category"] == "discipline_elective"]
                humanities = [item for item in memberships
                              if item["normalized_category"] == "humanities_elective"]
                del_requirement = categories.get((target_scope, "discipline_elective"))
                huel_requirement = categories.get((target_scope, "humanities_elective"))
                if own_del and del_requirement and del_requirement["state"] == "satisfied":
                    basis = "own_discipline_elective_after_del_accounted"
                    membership = own_del[0]
                elif humanities and huel_requirement and huel_requirement["state"] == "satisfied":
                    basis = "humanities_elective_after_huel_accounted"
                    membership = humanities[0]
                elif outside:
                    basis = "outside_own_disciplines_may_satisfy_open_elective"
                    membership = sorted(memberships, key=lambda item: (
                        item["programme_scope"], item["rule_id"]))[0]
                elif len(requested) == 2:
                    other = [item for item in memberships
                             if _key(item["programme_scope"]) != _key(target_scope)
                             and _key(item["programme_scope"]) in own_scopes
                             and item["normalized_category"] == "discipline_elective"]
                    if other:
                        basis = "dual_degree_discipline_elective_counts_as_other_open_elective"
                        membership = sorted(other, key=lambda item: item["programme_scope"])[0]
                if basis is None or membership is None:
                    continue
                relationship = {
                    "rule_id": requirement["rule_id"],
                    "programme_scope": target_scope,
                    "requested_programme_role": target.get("role"),
                    "normalized_category": "open_elective",
                    "completion_status": requirement["state"],
                    "relationship_source": requirement["collection"],
                    "sources": deepcopy(requirement["sources"]),
                    "membership_rule_id": membership["rule_id"],
                    "membership_sources": deepcopy(membership["sources"]),
                    "policy_rule_id": self.policies["open_elective_host_region"]["id"],
                    "policy_sources": deepcopy(
                        self.policies["open_elective_host_region"]["sources"]),
                    "relationship_basis": basis,
                    "potential_relationship": True,
                }
                bucket = index["explicit"].setdefault(code, [])
                if relationship not in bucket:
                    bucket.append(relationship)

    def cross_discipline_restrictions(self, academic_history):
        """Return per-course checks for the explicit third-year preparation gate."""
        if not self.enabled or not isinstance(academic_history, dict):
            return {}
        requested = [academic_history.get("programme"),
                     academic_history.get("second_programme")]
        requested = [item for item in requested if isinstance(item, str) and item.strip()]
        if not requested:
            return {}
        own = {_key(item) for item in requested}
        completed = {normalize_course_code(item.get("normalized_course_code"))
                     for item in academic_history.get("completed_courses") or []
                     if isinstance(item, dict)}
        completed.discard(None)
        required, curriculum_sources, limitation = self._preparation_requirement(requested)
        policy = self.policies["cross_discipline_prior_preparation"]
        checks = {}
        for code, memberships in self.memberships.items():
            if not self._outside_own_disciplines(memberships, own):
                continue
            sources = _unique_sources(deepcopy(policy["sources"]) + curriculum_sources)
            if limitation or not required:
                state, reason = "unknown", "cross_discipline_preparation_cannot_be_resolved"
                message = limitation or "The source-backed preparation package is unavailable"
            else:
                missing = sorted(required - completed)
                if missing:
                    state, reason = "failed", "cross_discipline_prior_preparation_not_met"
                    message = ("Cross-discipline registration requires completion through "
                               "Year 3 Semester 1; missing: " + ", ".join(missing))
                else:
                    state, reason = "passed", "cross_discipline_prior_preparation_met"
                    message = ("The explicit named-course preparation through Year 3 "
                               "Semester 1 is complete")
            checks[code] = [{
                "restriction_id": policy["id"], "state": state,
                "reason_code": reason, "message": message, "sources": sources,
            }]
        return checks

    def _outside_own_disciplines(self, memberships, own_scopes):
        memberships = [item for item in memberships
                       if item.get("normalized_category") in
                       ("discipline_core", "discipline_elective")]
        linked = {_key(item["programme_scope"]) for item in memberships
                  if item.get("scope_kind") == "programme"}
        if linked & set(own_scopes):
            return False
        own_lists = {_key(raw) for scope in own_scopes
                     for raw in self.discipline_aliases.get(scope, set())}
        listed = {_key(item["programme_scope"]) for item in memberships
                  if item.get("scope_kind") == "discipline_course_list"}
        if listed & set(own_scopes):
            return False
        if listed & own_lists:
            return False
        # A raw discipline list is usable as an outside region only when the
        # student's own discipline has an independently proven list mapping.
        return bool(linked or (listed and own_lists))

    def _preparation_requirement(self, requested):
        scopes = self._curriculum_scopes(requested)
        if not scopes:
            return set(), [], "No unique source-backed curriculum scope matches the student"
        codes, sources = set(), []
        for scope, periods in scopes:
            for record in self.records:
                if (record.get("kind") != "required_course" or
                        record.get("programme_name") != scope or
                        record.get("needs_verification") or
                        not _period_at_most(record.get("year"), record.get("semester"), 3, 1)):
                    continue
                if periods is not None and (record.get("year"), record.get("semester")) not in periods:
                    continue
                code = normalize_course_code(record.get("course_code"))
                if code:
                    codes.add(code)
                    sources.extend(record.get("sources") or [])
        return codes, _unique_sources(sources), None

    def _curriculum_scopes(self, requested):
        if len(requested) == 1:
            match = _unique_programme(requested[0], self.programmes, "semester-wise chart")
            return [(match, None)] if match else []
        composites = [item["name"] for item in self.programmes
                      if item.get("context") == "composite dual-degree chart" and
                      all(_key(value) in _key(item.get("name")) for value in requested)]
        if len(composites) != 1:
            return []
        composite = composites[0]
        scopes = [(composite, None)]
        for record in self.records:
            if (record.get("kind") == "curriculum_reference" and
                    record.get("programme_name") == composite and
                    record.get("needs_verification") is not True):
                target = record.get("referenced_programme_name")
                periods = {(item.get("year"), item.get("semester"))
                           for item in record.get("covered_periods") or []
                           if isinstance(item, dict)}
                if target and periods:
                    scopes.append((target, periods))
        return scopes


def _memberships(records, programmes):
    output = {}
    contexts = {item.get("id"): item.get("context") for item in programmes}
    for record in records:
        kind = record.get("kind")
        linked = kind in ("discipline_membership", "elective_membership")
        raw_list = (contexts.get(record.get("programme_id")) == "discipline course list"
                    and kind in ("required_course", "elective_option"))
        humanities = (contexts.get(record.get("programme_id")) == "institutional course pool"
                      and (record.get("category") or "").casefold() ==
                      "humanities electives" and kind == "elective_option")
        if not linked and not raw_list and not humanities:
            continue
        code = normalize_course_code(record.get("course_code"))
        scope = record.get("programme_name")
        if not code or not scope or record.get("needs_verification"):
            continue
        item = {
            "rule_id": record.get("id"), "programme_scope": scope,
            "normalized_category": ("humanities_elective" if humanities else
                "discipline_core" if kind in ("discipline_membership", "required_course")
                else "discipline_elective"),
            "scope_kind": ("programme" if linked else
                           "institutional_course_pool" if humanities else
                           "discipline_course_list"),
            "sources": deepcopy(record.get("sources") or []),
        }
        output.setdefault(code, []).append(item)
    return output


def _discipline_aliases(records):
    aliases = {}
    for record in records:
        if record.get("kind") not in ("discipline_membership", "elective_membership"):
            continue
        programme = record.get("programme_name")
        source = record.get("membership_source_programme_name")
        if programme and source and not record.get("needs_verification"):
            aliases.setdefault(_key(programme), set()).add(source)
    return aliases


def _category_requirements(summary):
    categories, requested = {}, []
    progress = (summary or {}).get("requirement_progress") or {}
    for programme in progress.get("programme_progress") or []:
        if not isinstance(programme, dict) or not programme.get("programme"):
            continue
        requested.append({"programme": programme["programme"],
                          "role": programme.get("requested_programme_role")})
        for category in programme.get("categories") or []:
            if not isinstance(category, dict):
                continue
            normalized = category.get("normalized_category")
            for collection, state in COLLECTION_STATES.items():
                for record in category.get(collection) or []:
                    if not isinstance(record, dict) or not record.get("rule_id"):
                        continue
                    key = (programme["programme"], normalized)
                    value = {"rule_id": record["rule_id"], "state": state,
                             "collection": collection,
                             "sources": deepcopy(record.get("sources") or [])}
                    if key not in categories or state in ("remaining", "partially_satisfied"):
                        categories[key] = value
    return categories, requested


def _unique_programme(value, programmes, context):
    matches = [item.get("name") for item in programmes
               if item.get("context") == context and _key(item.get("name")) == _key(value)]
    return matches[0] if len(matches) == 1 else None


def _period_at_most(year, semester, max_year, max_semester):
    return (type(year) is int and semester in (1, 2) and
            (year, semester) <= (max_year, max_semester))


def _key(value):
    return re.sub(r"[^a-z0-9]+", "", value.casefold()) if isinstance(value, str) else ""


def _unique_sources(sources):
    output = []
    for source in sources:
        if isinstance(source, dict) and source not in output:
            output.append(source)
    return output


def _load(path):
    with Path(path).open(encoding="utf-8") as stream:
        return json.load(stream)

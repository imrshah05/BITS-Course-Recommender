"""Grounded student-facing explanations for recommendation results."""

from copy import deepcopy
import json
import re

from backend.gemini import GeminiClient, GeminiError


MAX_AI_EXPLANATIONS = 6


class RecommendationExplanationService:
    """Explain existing results without changing their status or academic facts."""

    def __init__(self, client=None, max_ai_explanations=MAX_AI_EXPLANATIONS):
        self.client = client or GeminiClient()
        self.max_ai_explanations = max(0, int(max_ai_explanations))

    def explain(self, result):
        output = deepcopy(result)
        records = []
        for group, status in (("confirmed_recommendations", "confirmed"),
                              ("verification_required", "verification_required")):
            items = output.get(group) or []
            for item in items:
                if not isinstance(item, dict):
                    continue
                facts = _facts(item, status)
                item["explanation"] = {
                    "text": _deterministic_explanation(item, status, facts),
                    "method": "deterministic",
                    "grounded_fact_ids": [fact["id"] for fact in facts],
                }
                if len(records) < self.max_ai_explanations:
                    records.append({"course_code": item.get("course_code"),
                                    "status": status, "facts": facts, "item": item})
        if not records:
            return output
        try:
            response = self.client.generate_structured(
                _prompt(records), EXPLANATION_SCHEMA)
            generated = _validate_response(response, records)
        except (GeminiError, TypeError, ValueError):
            return output
        for record in records:
            explanation = generated.get((record["course_code"], record["status"]))
            if explanation:
                record["item"]["explanation"] = explanation
        return output


def explain_recommendation_results(result, client=None, max_ai_explanations=6):
    """Convenience interface for grounded result explanations."""
    return RecommendationExplanationService(client, max_ai_explanations).explain(result)


def _facts(item, status):
    facts = [{"id": "status", "text": (
        "The deterministic pipeline confirmed this recommendation."
        if status == "confirmed" else
        "The deterministic pipeline requires verification before recommendation.") }]
    title = item.get("course_title")
    if isinstance(title, str) and title.strip():
        facts.append({"id": "title", "text": f"The source-backed title is {title.strip()}."})
    eligibility = item.get("eligibility_state")
    if isinstance(eligibility, str) and eligibility:
        facts.append({"id": "eligibility", "text":
                      f"The deterministic eligibility state is {eligibility}."})
    requirement = item.get("requirement_filter_state")
    if isinstance(requirement, str) and requirement:
        facts.append({"id": "requirement", "text":
                      f"The requirement-filter state is {requirement}."})
    reasons = [reason for reason in item.get("reasons") or []
               if isinstance(reason, str) and reason]
    if reasons:
        facts.append({"id": "reasons", "text":
                      "Recorded reason codes: " + ", ".join(reasons) + "."})
    preference = item.get("preference_match") or {}
    matched = [entry.get("value") for entry in preference.get("matched_preferences") or []
               if isinstance(entry, dict) and isinstance(entry.get("value"), str)]
    if matched:
        facts.append({"id": "preferences", "text":
                      "Source-backed preference matches: " + ", ".join(matched) + "."})
    return facts


def _deterministic_explanation(item, status, facts):
    code = item.get("course_code") or "This course"
    fact_ids = {fact["id"] for fact in facts}
    if status == "confirmed":
        text = f"{code} passed the deterministic academic safety checks"
        if "preferences" in fact_ids:
            text += " and has source-backed evidence matching your preferences"
        return text + "."
    reasons = set(item.get("reasons") or [])
    if "preference_relevance_not_confirmed" in reasons:
        return (f"{code} needs verification because its relevance to your stated "
                "preferences was not confirmed.")
    if "prerequisite_unknown" in reasons:
        return (f"{code} needs verification because the available prerequisite "
                "information is incomplete.")
    if item.get("eligibility_state") in ("unknown", "ambiguous"):
        return (f"{code} needs verification because deterministic eligibility is "
                f"{item.get('eligibility_state')}.")
    return (f"{code} needs verification because the deterministic pipeline did not "
            "confirm recommendation safety.")


def _prompt(records):
    payload = [{"course_code": record["course_code"], "status": record["status"],
                "facts": record["facts"]} for record in records]
    return (
        "Write one concise, student-friendly explanation of at most two sentences for "
        "each result. Use only the supplied facts. Do not add or change eligibility, "
        "prerequisites, academic requirements, requirement satisfaction, course status, "
        "or recommendation status. Cite every fact used by its fact id.\nResults:\n" +
        json.dumps(payload, ensure_ascii=False)
    )


def _validate_response(response, records):
    if not isinstance(response, dict) or set(response) != {"explanations"}:
        raise ValueError("Malformed explanation output")
    rows = response["explanations"]
    if not isinstance(rows, list):
        raise ValueError("Explanation output must be a list")
    allowed = {(record["course_code"], record["status"]): record for record in records}
    generated = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != {
                "course_code", "status", "fact_ids", "text"}:
            raise ValueError("Unexpected explanation fields")
        key = (row["course_code"], row["status"])
        if key not in allowed or key in generated:
            raise ValueError("Explanation identity or status changed")
        text = row["text"]
        fact_ids = row["fact_ids"]
        known_ids = {fact["id"] for fact in allowed[key]["facts"]}
        if (not isinstance(text, str) or not text.strip() or len(text) > 500
                or not isinstance(fact_ids, list) or not fact_ids
                or any(not isinstance(value, str) or value not in known_ids
                       for value in fact_ids)):
            raise ValueError("Explanation is not grounded")
        codes = set(re.findall(r"\b[A-Z]{2,8}\s+[FG]\d{3}[A-Z]?\b", text.upper()))
        if codes and codes != {str(row["course_code"]).upper()}:
            raise ValueError("Explanation introduced another course identity")
        if row["status"] == "verification_required" and re.search(
                r"(?i)\b(?:confirmed recommendation|definitely eligible|safe to take)\b", text):
            raise ValueError("Explanation contradicts verification status")
        lowered = text.casefold()
        available_text = " ".join(fact["text"] for fact in allowed[key]["facts"]).casefold()
        for subject in ("prerequisite", "requirement", "preference", "completed", "ongoing"):
            if subject in lowered and subject not in available_text:
                raise ValueError("Explanation introduced an unsupported academic claim")
        eligibility = next((fact["text"] for fact in allowed[key]["facts"]
                            if fact["id"] == "eligibility"), "").casefold()
        stated_states = {state for state in ("eligible", "ineligible", "unknown", "ambiguous")
                         if re.search(rf"\b{state}\b", lowered)}
        if stated_states and any(state not in eligibility for state in stated_states):
            raise ValueError("Explanation changed the eligibility state")
        generated[key] = {"text": " ".join(text.split()), "method": "gemini",
                          "grounded_fact_ids": list(dict.fromkeys(fact_ids))}
    return generated


EXPLANATION_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {"explanations": {
        "type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "course_code": {"type": "string"},
                "status": {"type": "string",
                           "enum": ["confirmed", "verification_required"]},
                "fact_ids": {"type": "array", "items": {"type": "string"}},
                "text": {"type": "string"},
            },
            "required": ["course_code", "status", "fact_ids", "text"],
        }
    }},
    "required": ["explanations"],
}

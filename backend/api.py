"""Minimal WSGI API and static dashboard server."""

from http import HTTPStatus
from functools import lru_cache
from io import BytesIO
import json
from pathlib import Path
from wsgiref.simple_server import make_server

from backend.course_catalogue import CourseCatalogue
from backend.course_suggestions import CourseSuggestionService
from backend.dashboard_options import DashboardOptionsService
from backend.dashboard_recommendations import DashboardRecommendationService
from backend.gemini import configured_intent_parser, gemini_configuration
from backend.student_profile import normalize_student_profile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_ROOT = PROJECT_ROOT / "frontend"
STATIC_ROUTES = {
    "/": (FRONTEND_ROOT / "index.html", "text/html; charset=utf-8"),
    "/assets/styles.css": (FRONTEND_ROOT / "styles.css", "text/css; charset=utf-8"),
    "/assets/app.js": (FRONTEND_ROOT / "app.js", "text/javascript; charset=utf-8"),
}
MAX_JSON_BODY_BYTES = 1_000_000


def application(environ, start_response):
    """Serve foundational API endpoints and explicit dashboard assets."""
    method = environ.get("REQUEST_METHOD", "GET").upper()
    path = environ.get("PATH_INFO", "/")
    if path == "/api/options" and method == "GET":
        return _json_response(start_response, HTTPStatus.OK, {
            "options": _dashboard_options().options(),
        })
    if path == "/api/course-suggestions" and method == "POST":
        try:
            payload = _read_json_body(environ)
            if not isinstance(payload, dict) or set(payload) != {
                    "programmes", "current_academic_year", "current_semester"}:
                raise ValueError(
                    "Request body must contain programmes, current_academic_year, and current_semester")
            result = _course_suggestions().suggest(
                payload["programmes"], payload["current_academic_year"],
                payload["current_semester"])
        except RequestError as error:
            return _json_response(start_response, error.status, {
                "error": error.code, "message": error.message,
            })
        except ValueError as error:
            return _json_response(start_response, HTTPStatus.BAD_REQUEST, {
                "error": "invalid_course_suggestion_payload", "message": str(error),
            })
        return _json_response(start_response, HTTPStatus.OK, {
            "course_suggestions": result,
        })
    if path == "/api/student-profile" and method == "POST":
        try:
            payload = _read_json_body(environ)
        except RequestError as error:
            return _json_response(start_response, error.status, {
                "error": error.code, "message": error.message,
            })
        try:
            profile = normalize_student_profile(payload, _known_course_codes())
        except (TypeError, ValueError) as error:
            return _json_response(start_response, HTTPStatus.BAD_REQUEST, {
                "error": "invalid_profile_payload", "message": str(error),
            })
        status = HTTPStatus.OK if profile["validation"]["is_valid"] \
            else HTTPStatus.UNPROCESSABLE_ENTITY
        return _json_response(start_response, status, {"profile": profile})
    if path == "/api/intent" and method == "POST":
        try:
            payload = _read_json_body(environ)
        except RequestError as error:
            return _json_response(start_response, error.status, {
                "error": error.code, "message": error.message,
            })
        if not isinstance(payload, dict) or set(payload) != {"query"}:
            return _json_response(start_response, HTTPStatus.BAD_REQUEST, {
                "error": "invalid_intent_payload",
                "message": "Request body must contain only a query field.",
            })
        intent = _intent_parser().parse(payload["query"])
        status = HTTPStatus.OK if intent["validation"]["is_valid"] \
            else HTTPStatus.UNPROCESSABLE_ENTITY
        return _json_response(start_response, status, {"intent": intent})
    if path == "/api/recommendations" and method == "POST":
        try:
            payload = _read_json_body(environ)
        except RequestError as error:
            return _json_response(start_response, error.status, {
                "error": error.code, "message": error.message,
            })
        if not isinstance(payload, dict) or set(payload) != {"profile", "query"}:
            return _json_response(start_response, HTTPStatus.BAD_REQUEST, {
                "error": "invalid_recommendation_payload",
                "message": "Request body must contain profile and query fields.",
            })
        try:
            result = _recommendation_service().recommend(
                payload["profile"], payload["query"])
        except (TypeError, ValueError) as error:
            return _json_response(start_response, HTTPStatus.BAD_REQUEST, {
                "error": "invalid_recommendation_payload", "message": str(error),
            })
        status = HTTPStatus.OK if result["validation"]["is_valid"] \
            else HTTPStatus.UNPROCESSABLE_ENTITY
        return _json_response(start_response, status, {"recommendations": result})
    if method != "GET":
        return _json_response(start_response, HTTPStatus.METHOD_NOT_ALLOWED, {
            "error": "method_not_allowed",
            "message": "This endpoint currently supports GET requests only.",
        }, extra_headers=[("Allow", "GET")])
    if path == "/api/health":
        return _json_response(start_response, HTTPStatus.OK, {
            "status": "ok",
            "service": "bits-academic-course-recommender",
            "api_version": "v1",
        })
    if path == "/api/config":
        return _json_response(start_response, HTTPStatus.OK, {
            "api_base_path": "/api",
            "features": {
                "student_profile": True,
                "preference_query": True,
                "gemini": gemini_configuration()["configured"],
                "recommendations": True,
                "timetable": False,
            },
        })
    if path.startswith("/api/"):
        return _json_response(start_response, HTTPStatus.NOT_FOUND, {
            "error": "not_found",
            "message": "API endpoint not found.",
        })
    asset = STATIC_ROUTES.get(path)
    if asset is None:
        return _text_response(start_response, HTTPStatus.NOT_FOUND,
                              b"Dashboard resource not found.\n", "text/plain; charset=utf-8")
    file_path, content_type = asset
    try:
        content = file_path.read_bytes()
    except OSError:
        return _text_response(start_response, HTTPStatus.INTERNAL_SERVER_ERROR,
                              b"Dashboard resource unavailable.\n",
                              "text/plain; charset=utf-8")
    return _text_response(start_response, HTTPStatus.OK, content, content_type)


def run(host="127.0.0.1", port=8000):
    """Run the local development server."""
    with make_server(host, port, application) as server:
        print(f"Dashboard available at http://{host}:{port}")
        server.serve_forever()


class RequestError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _read_json_body(environ):
    raw_length = environ.get("CONTENT_LENGTH", "")
    try:
        length = int(raw_length) if raw_length else 0
    except (TypeError, ValueError) as error:
        raise RequestError(HTTPStatus.BAD_REQUEST, "invalid_content_length",
                           "Content-Length must be a whole number.") from error
    if length <= 0:
        raise RequestError(HTTPStatus.BAD_REQUEST, "empty_request_body",
                           "A JSON student profile is required.")
    if length > MAX_JSON_BODY_BYTES:
        raise RequestError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "request_too_large",
                           "Student profile payload is too large.")
    body = environ.get("wsgi.input", BytesIO()).read(length)
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RequestError(HTTPStatus.BAD_REQUEST, "invalid_json",
                           "Request body must be valid UTF-8 JSON.") from error


@lru_cache(maxsize=1)
def _course_suggestions():
    return CourseSuggestionService()


@lru_cache(maxsize=1)
def _dashboard_options():
    return DashboardOptionsService()


@lru_cache(maxsize=1)
def _known_course_codes():
    return tuple(CourseCatalogue.load().course_codes())


@lru_cache(maxsize=1)
def _intent_parser():
    return configured_intent_parser()


@lru_cache(maxsize=1)
def _recommendation_service():
    return DashboardRecommendationService()


def _json_response(start_response, status, payload, extra_headers=None):
    body = json.dumps(payload, sort_keys=True).encode("utf-8")
    headers = [("Content-Type", "application/json; charset=utf-8"),
               ("Content-Length", str(len(body))),
               ("Cache-Control", "no-store")]
    headers.extend(extra_headers or [])
    start_response(f"{status.value} {status.phrase}", headers)
    return [body]


def _text_response(start_response, status, body, content_type):
    start_response(f"{status.value} {status.phrase}", [
        ("Content-Type", content_type),
        ("Content-Length", str(len(body))),
    ])
    return [body]


if __name__ == "__main__":
    run()

"""Minimal WSGI API and static dashboard server."""

from http import HTTPStatus
import json
from pathlib import Path
from wsgiref.simple_server import make_server


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_ROOT = PROJECT_ROOT / "frontend"
STATIC_ROUTES = {
    "/": (FRONTEND_ROOT / "index.html", "text/html; charset=utf-8"),
    "/assets/styles.css": (FRONTEND_ROOT / "styles.css", "text/css; charset=utf-8"),
    "/assets/app.js": (FRONTEND_ROOT / "app.js", "text/javascript; charset=utf-8"),
}


def application(environ, start_response):
    """Serve foundational API endpoints and explicit dashboard assets."""
    method = environ.get("REQUEST_METHOD", "GET").upper()
    path = environ.get("PATH_INFO", "/")
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
                "student_profile": False,
                "recommendations": False,
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

"""Local HTTP server: Stitch frontend plus the real ticket API.

One in-memory ticket at a time, same as the CLI. Static files come from
``stitch_frontend_design_prototype``. JSON responses hold only the human view
(redacted complaint, draft, routing, controls). The raw complaint is dropped
after ``Orchestrator.submit``.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from io import BytesIO
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app import APPROVE_UNAVAILABLE, APPROVE_UNCONNECTED, DISCARDED, App, build_app
from config import Settings, load_settings
from ingress.redactor import IngressError
from paths import PROJECT_ROOT
from refinement.view import APPROVE, DISCARD, REFINE, RETRY, HumanView, build_view

FRONTEND_DIR = PROJECT_ROOT / "stitch_frontend_design_prototype"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765

_MIME = {
    ".html": "text/html; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".ico": "image/x-icon",
    ".json": "application/json; charset=utf-8",
}

_ALIASES = {"": "index.html", "workbench": "workbench.html"}


class Desk:
    """The one open ticket, and the lock around it."""

    def __init__(self, app: App) -> None:
        self.app = app
        self.session = None
        self.run = None
        self.notice: str | None = None
        self._lock = threading.Lock()

    def view(self) -> HumanView | None:
        if self.session is None:
            return None
        return build_view(
            self.session, run=self.run, notice=self.notice, max_rounds=self.app.max_rounds
        )


def view_payload(view: HumanView) -> dict[str, Any]:
    return asdict(view)


def _parse_json(raw: bytes) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("body must be JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("body must be a JSON object")
    return data


def _read_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length") or 0)
    raw = handler.rfile.read(length) if length > 0 else b""
    return _parse_json(raw)


def _safe_static(root: Path, url_path: str) -> Path | None:
    rel = url_path.lstrip("/")
    rel = _ALIASES.get(rel, rel)
    if not rel or ".." in Path(rel).parts:
        return None
    target = (root / rel).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError:
        return None
    return target if target.is_file() else None


def make_handler(desk: Desk, static_root: Path) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            sys.stderr.write("%s - %s\n" % (self.address_string(), format % args))

        def _cors(self) -> None:
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")

        def do_OPTIONS(self) -> None:
            self.send_response(HTTPStatus.NO_CONTENT)
            self._cors()
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/api/health":
                self._json(HTTPStatus.OK, {"ok": True, "frontend": str(static_root), "has_ticket": desk.session is not None})
                return
            if path == "/api/tickets/current":
                self._current()
                return
            self._static(path)

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            if path == "/api/tickets":
                self._open()
                return
            if path == "/api/tickets/current/refine":
                self._refine()
                return
            if path == "/api/tickets/current/approve":
                self._approve()
                return
            if path == "/api/tickets/current/retry":
                self._retry()
                return
            if path == "/api/tickets/current/discard":
                self._discard()
                return
            self._json(HTTPStatus.NOT_FOUND, {"error": "unknown path"})

        def _static(self, path: str) -> None:
            target = _safe_static(static_root, path)
            if target is None:
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return
            body = target.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", _MIME.get(target.suffix.lower(), "application/octet-stream"))
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self._cors()
            self.end_headers()
            self.wfile.write(body)

        def _current(self) -> None:
            with desk._lock:
                view = desk.view()
            if view is None:
                self._json(HTTPStatus.NOT_FOUND, {"error": "no open ticket"})
                return
            self._json(HTTPStatus.OK, view_payload(view))

        def _open(self) -> None:
            try:
                body = _read_json(self)
            except ValueError as error:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
                return
            complaint = body.get("complaint")
            if not isinstance(complaint, str):
                self._json(HTTPStatus.BAD_REQUEST, {"error": "complaint must be a string"})
                return
            try:
                with desk._lock:
                    run = desk.app.orchestrator.submit(complaint)
                    desk.session = run.session
                    desk.run = run
                    desk.notice = None
                    view = desk.view()
            except IngressError as error:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(error), "code": error.code})
                return
            finally:
                complaint = None
            assert view is not None
            self._json(HTTPStatus.OK, view_payload(view))

        def _refine(self) -> None:
            try:
                body = _read_json(self)
            except ValueError as error:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
                return
            feedback = body.get("feedback")
            if not isinstance(feedback, str):
                self._json(HTTPStatus.BAD_REQUEST, {"error": "feedback must be a string"})
                return
            with desk._lock:
                if desk.session is None:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "no open ticket"})
                    return
                result = desk.app.refiner.refine(desk.session, feedback)
                if result.status == "refused":
                    desk.notice = result.message
                    view = desk.view()
                    self._json(
                        HTTPStatus.CONFLICT,
                        {"error": result.message, "code": result.refusal, "view": view_payload(view) if view else None},
                    )
                    return
                desk.run = result.run
                desk.notice = result.message
                view = desk.view()
            feedback = None
            assert view is not None
            self._json(HTTPStatus.OK, view_payload(view))

        def _approve(self) -> None:
            with desk._lock:
                view = desk.view()
                if view is None or desk.session is None:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "no open ticket"})
                    return
                if RETRY in view.controls:
                    self._run_retry()
                    return
                if APPROVE not in view.controls:
                    self._json(HTTPStatus.CONFLICT, {"error": APPROVE_UNAVAILABLE, "view": view_payload(view)})
                    return
                if desk.app.gate is None:
                    self._json(HTTPStatus.CONFLICT, {"error": APPROVE_UNCONNECTED, "view": view_payload(view)})
                    return
                fired = desk.run.agents_fired if desk.run is not None else desk.session.approval.agents_fired
                try:
                    result = desk.app.gate.approve(desk.session, agents_fired=fired)
                except RuntimeError as error:
                    self._json(HTTPStatus.CONFLICT, {"error": str(error), "view": view_payload(desk.view())})
                    return
                desk.notice = result.message
                payload = {"status": result.status, "message": result.message, "view": view_payload(desk.view())}
            code = HTTPStatus.OK if result.status in {"done", "idempotent"} else HTTPStatus.CONFLICT
            self._json(code, payload)

        def _retry(self) -> None:
            with desk._lock:
                self._run_retry()

        def _run_retry(self) -> None:
            view = desk.view()
            if view is None or desk.session is None:
                self._json(HTTPStatus.NOT_FOUND, {"error": "no open ticket"})
                return
            if desk.app.gate is None:
                self._json(HTTPStatus.CONFLICT, {"error": APPROVE_UNCONNECTED, "view": view_payload(view)})
                return
            if RETRY not in view.controls:
                self._json(HTTPStatus.CONFLICT, {"error": APPROVE_UNAVAILABLE, "view": view_payload(view)})
                return
            try:
                result = desk.app.gate.retry(desk.session)
            except RuntimeError as error:
                self._json(HTTPStatus.CONFLICT, {"error": str(error), "view": view_payload(desk.view())})
                return
            desk.notice = result.message
            payload = {"status": result.status, "message": result.message, "view": view_payload(desk.view())}
            code = HTTPStatus.OK if result.status in {"done", "idempotent"} else HTTPStatus.CONFLICT
            self._json(code, payload)

        def _discard(self) -> None:
            with desk._lock:
                if desk.session is None:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "no open ticket"})
                    return
                if DISCARD not in (desk.view().controls if desk.view() else ()):
                    self._json(HTTPStatus.CONFLICT, {"error": "This ticket is closed."})
                    return
                desk.app.refiner.discard(desk.session)
                desk.notice = DISCARDED
                view = desk.view()
            assert view is not None
            self._json(HTTPStatus.OK, {"message": DISCARDED, "view": view_payload(view)})

        def _json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self._cors()
            self.end_headers()
            self.wfile.write(body)

    return Handler


def serve(
    *,
    app: App | None = None,
    settings: Settings | None = None,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    static_root: Path | None = None,
) -> ThreadingHTTPServer:
    """Build the desk (unless ``app`` is given) and start serving. Returns the server."""
    chosen = settings or load_settings()
    built = app if app is not None else build_app(chosen)
    root = static_root or FRONTEND_DIR
    if not root.is_dir():
        raise RuntimeError(f"frontend folder is missing: {root}")
    desk = Desk(built)
    server = ThreadingHTTPServer((host, port), make_handler(desk, root))
    return server


_desk: Desk | None = None
_desk_ready = threading.Lock()


def get_desk() -> Desk:
    """The process-wide desk. Vercel reuses a warm instance when it can."""
    global _desk
    with _desk_ready:
        if _desk is None:
            _desk = Desk(build_app())
        return _desk


def _invoke(handler_cls: type[BaseHTTPRequestHandler], request: bytes) -> bytes:
    """Run one stdlib handler without a listening socket."""
    handler = handler_cls.__new__(handler_cls)
    handler.rfile = BytesIO(request)
    handler.wfile = BytesIO()
    handler.client_address = ("127.0.0.1", 0)
    handler.close_connection = True
    handler.handle_one_request()
    return handler.wfile.getvalue()


def _split_response(raw: bytes) -> tuple[str, list[tuple[str, str]], bytes]:
    head, _, body = raw.partition(b"\r\n\r\n")
    lines = head.decode("iso-8859-1").split("\r\n")
    status = lines[0].split(" ", 1)[1] if lines and " " in lines[0] else "500 Internal Server Error"
    headers: list[tuple[str, str]] = []
    for line in lines[1:]:
        if ":" not in line:
            continue
        name, value = line.split(":", 1)
        headers.append((name.strip(), value.strip()))
    return status, headers, body


def wsgi_app(environ: dict[str, Any], start_response: Any) -> list[bytes]:
    """WSGI callable Vercel loads as ``app``. Same routes as the local server."""
    method = environ.get("REQUEST_METHOD", "GET")
    path = environ.get("PATH_INFO") or "/"
    query = environ.get("QUERY_STRING") or ""
    target = path if not query else f"{path}?{query}"
    try:
        length = int(environ.get("CONTENT_LENGTH") or 0)
    except ValueError:
        length = 0
    payload = environ["wsgi.input"].read(length) if length else b""
    header = f"{method} {target} HTTP/1.1\r\n"
    if length:
        header += f"Content-Length: {length}\r\nContent-Type: application/json\r\n"
    header += "\r\n"
    raw = _invoke(make_handler(get_desk(), FRONTEND_DIR), header.encode("ascii") + payload)
    status, headers, body = _split_response(raw)
    start_response(status, headers)
    return [body]


def listen_address() -> tuple[str, int]:
    """Render sets ``PORT`` and expects ``0.0.0.0``. Local runs stay on 127.0.0.1:8765."""
    port_raw = os.environ.get("PORT")
    if port_raw:
        return os.environ.get("HOST", "0.0.0.0"), int(port_raw)
    return DEFAULT_HOST, DEFAULT_PORT


def main(argv: list[str] | None = None) -> int:
    del argv
    host, port = listen_address()
    try:
        server = serve(host=host, port=port)
    except (RuntimeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(f"Frontend (Stitch):  http://{host}:{port}/", flush=True)
    print(f"Backend workbench:  http://{host}:{port}/workbench", flush=True)
    print(f"API health:         http://{host}:{port}/api/health", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

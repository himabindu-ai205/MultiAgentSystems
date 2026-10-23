"""Local HTTP desk: static frontend plus the ticket API."""

from __future__ import annotations

import json
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path

from agents.stubs import stub_compliance, stub_facts, stub_pattern
from app import App
from orchestrator.dispatch import Orchestrator
from orchestrator.drafter import Drafter
from orchestrator.graph import AgentSet
from refinement.refiner import Refiner
from refinement.router import RefinementRouter
from reviewer.gate import Reviewer
from paths import PROJECT_ROOT
from server import Desk, FRONTEND_DIR, make_handler

QUESTION = "What is the exit load of the HDFC Large Cap fund?"
PAN = "ABCDE1234F"


def stub_app() -> App:
    agents = AgentSet(
        facts=lambda *a, **k: stub_facts(),
        pattern=lambda *a, **k: stub_pattern(scenario="one_off"),
        compliance=lambda *a, **k: stub_compliance(),
    )
    orchestrator = Orchestrator(agents, Drafter(), reviewer=Reviewer())
    return App(orchestrator, Refiner(orchestrator, RefinementRouter()))


def start_server(app: App | None = None):
    desk = Desk(app or stub_app())
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(desk, FRONTEND_DIR))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, desk


def call(server, method, path, body=None):
    status, _, data = call_headers(server, method, path, body)
    return status, data


def call_headers(server, method, path, body=None):
    conn = HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
    payload = None if body is None else json.dumps(body)
    headers = {"Content-Type": "application/json"} if payload else {}
    conn.request(method, path, body=payload, headers=headers)
    response = conn.getresponse()
    raw = response.read()
    header_map = {name.lower(): value for name, value in response.getheaders()}
    conn.close()
    data = json.loads(raw.decode("utf-8")) if raw.startswith(b"{") or raw.startswith(b"[") else raw
    return response.status, header_map, data


def test_health_and_stitch_frontend_are_served():
    server, _ = start_server()
    try:
        status, data = call(server, "GET", "/api/health")
        assert status == 200 and data["ok"] is True and data["has_ticket"] is False
        status, body = call(server, "GET", "/")
        assert status == 200 and b"HDFC Mutual Fund FAQ Assistant" in body
        assert b"desk.js" in body
        assert b"Large Cap" in body and b"Gold FoF" in body
        assert b"New Ticket" not in body
        assert b"TKT-92041" not in body
        assert b"Ticket Overview" not in body
        assert b"Rahul" not in body
        status, script = call(server, "GET", "/desk.js")
        assert status == 200 and b"Scheme desk" in body and b"On this desk" in body
        assert b"function send" in script or b"async function send" in script
        assert b"The reviewer has not passed this reply yet." not in script
        assert b"Reviewer:" in script
        assert b"!controls.includes(\"approve\")" in script
        status, body = call(server, "GET", "/workbench")
        assert status == 200 and b"Run agents" in body
    finally:
        server.shutdown()
        server.server_close()


def test_opening_a_ticket_returns_the_human_view_and_not_the_raw_path():
    server, _ = start_server()
    try:
        status, data = call(server, "POST", "/api/tickets", {"complaint": QUESTION})
        assert status == 200
        assert data["complaint"] == QUESTION
        assert data["verdict"] == "PASS"
        assert "approve" in data["controls"]
        assert data["citation"]
        status, current = call(server, "GET", "/api/tickets/current")
        assert status == 200 and current["request_id"] == data["request_id"]
    finally:
        server.shutdown()
        server.server_close()


def test_empty_complaint_is_rejected_before_a_ticket_exists():
    server, _ = start_server()
    try:
        status, data = call(server, "POST", "/api/tickets", {"complaint": "   "})
        assert status == 400
        assert "empty" in data["error"].lower()
        status, current = call(server, "GET", "/api/tickets/current")
        assert status == 404
    finally:
        server.shutdown()
        server.server_close()


def test_pii_in_the_complaint_is_shown_as_a_placeholder():
    server, _ = start_server()
    try:
        status, data = call(
            server, "POST", "/api/tickets", {"complaint": f"refund my units, PAN {PAN}"}
        )
        assert status == 200
        assert PAN not in json.dumps(data)
        assert "[PAN]" in data["complaint"]
    finally:
        server.shutdown()
        server.server_close()


def test_refine_stops_after_five_rounds():
    server, _ = start_server()
    try:
        call(server, "POST", "/api/tickets", {"complaint": QUESTION})
        for used in range(5):
            status, data = call(server, "POST", "/api/tickets/current/refine", {"feedback": "shorter"})
            assert status == 200
            assert data["refinements_left"] == 4 - used
        status, data = call(server, "POST", "/api/tickets/current/refine", {"feedback": "shorter"})
        assert status == 409
        assert "refine" not in data["view"]["controls"]
        assert data["view"]["refinements_left"] == 0
    finally:
        server.shutdown()
        server.server_close()


def test_refine_button_reruns_every_agent_and_keeps_the_full_reply():
    server, desk = start_server()
    try:
        call(server, "POST", "/api/tickets", {"complaint": QUESTION})
        status, data = call(server, "POST", "/api/tickets/current/refine", {"feedback": "rerun"})
        assert status == 200
        assert data["refinements_left"] == 4
        assert set(data["agents_fired"]) >= {"facts", "pattern", "compliance", "orchestrator"}
        assert "look into it" in data["draft_text"]
        assert desk.session.refinement.style == "standard"
    finally:
        server.shutdown()
        server.server_close()


def test_refine_and_discard_follow_the_cli_controls():
    server, _ = start_server()
    try:
        call(server, "POST", "/api/tickets", {"complaint": QUESTION})
        status, data = call(server, "POST", "/api/tickets/current/refine", {"feedback": "shorter"})
        assert status == 200
        assert data["refinements_left"] == 4
        status, data = call(server, "POST", "/api/tickets/current/discard", {})
        assert status == 200
        assert data["message"].startswith("The ticket was discarded")
        assert data["view"] is None
        status, data = call(server, "GET", "/api/tickets/current")
        assert status == 404
        status, data = call(server, "POST", "/api/tickets/current/discard", {})
        assert status == 200
    finally:
        server.shutdown()
        server.server_close()


def test_approve_without_a_gate_is_refused_and_the_ticket_stays_open():
    server, _ = start_server()
    try:
        call(server, "POST", "/api/tickets", {"complaint": QUESTION})
        status, data = call(server, "POST", "/api/tickets/current/approve", {})
        assert status == 409
        assert "connected" in data["error"].lower()
        assert "approve" in data["view"]["controls"]
    finally:
        server.shutdown()
        server.server_close()


def test_path_traversal_is_rejected():
    server, _ = start_server()
    try:
        status, data = call(server, "GET", "/../pyproject.toml")
        assert status == 404
        assert data["error"] == "not found"
    finally:
        server.shutdown()
        server.server_close()


def test_wsgi_entrypoint_serves_health_and_a_ticket():
    from io import BytesIO

    import server as server_mod

    desk = Desk(stub_app())
    original = server_mod.get_desk
    server_mod.get_desk = lambda: desk
    captured: dict[str, str] = {}

    def start_response(status, headers):
        captured["status"] = status

    try:
        chunks = server_mod.wsgi_app(
            {
                "REQUEST_METHOD": "GET",
                "PATH_INFO": "/api/health",
                "QUERY_STRING": "",
                "CONTENT_LENGTH": "0",
                "wsgi.input": BytesIO(),
            },
            start_response,
        )
        assert captured["status"].startswith("200")
        assert b'"ok": true' in b"".join(chunks)

        body = json.dumps({"complaint": QUESTION}).encode()
        chunks = server_mod.wsgi_app(
            {
                "REQUEST_METHOD": "POST",
                "PATH_INFO": "/api/tickets",
                "QUERY_STRING": "",
                "CONTENT_LENGTH": str(len(body)),
                "wsgi.input": BytesIO(body),
            },
            start_response,
        )
        assert captured["status"].startswith("200")
        assert json.loads(b"".join(chunks))["complaint"] == QUESTION
    finally:
        server_mod.get_desk = original


def test_api_allows_a_browser_on_another_origin():
    server, _ = start_server()
    try:
        status, headers, _ = call_headers(server, "OPTIONS", "/api/tickets")
        assert status == 204
        assert headers.get("access-control-allow-origin") == "*"
        assert "POST" in headers.get("access-control-allow-methods", "")
    finally:
        server.shutdown()
        server.server_close()


def test_split_deploy_points_vercel_at_the_page_and_render_at_the_api():
    page = (FRONTEND_DIR / "config.js").read_text(encoding="utf-8")
    assert "multiagentsystems.onrender.com" in page
    render = (PROJECT_ROOT / "render.yaml").read_text(encoding="utf-8")
    assert "python scripts/serve_local.py" in render
    assert "SERVE_FRONTEND" not in render
    vercel = (PROJECT_ROOT / "vercel.json").read_text(encoding="utf-8")
    assert "stitch_frontend_design_prototype" in vercel


def test_render_mode_serves_the_api_and_not_the_page():
    desk = Desk(stub_app())
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(desk, FRONTEND_DIR, pages=False))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, body = call(server, "GET", "/")
        assert status == 404
        assert body["error"] == "This host is the API only."
        assert b"HDFC" not in json.dumps(body).encode()
        status, data = call(server, "GET", "/api/health")
        assert status == 200 and data["ok"] is True
        status, data = call(server, "POST", "/api/tickets", {"complaint": QUESTION})
        assert status == 200 and data["complaint"] == QUESTION
    finally:
        server.shutdown()
        server.server_close()


def test_frontend_dir_is_the_stitch_folder():
    assert FRONTEND_DIR.name == "stitch_frontend_design_prototype"
    assert Path(FRONTEND_DIR / "workbench.html").is_file()

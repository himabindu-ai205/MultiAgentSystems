"""The allow-list logger writes only listed events and fields."""

import json
import logging

import pytest

from logging_allowlist import AllowListLogger
from schemas import new_request_id

SECRET = "my PAN is ABCDE1234F and my phone is 9123456780"
HASH = "a" * 64


@pytest.fixture()
def sink():
    records = []
    return records


@pytest.fixture()
def log(sink):
    return AllowListLogger(sink=sink.append)


def test_allowed_event_is_written_with_a_timestamp(log, sink):
    rid = new_request_id()
    record = log.log("agent_run", request_id=rid, agent="facts", status="covered", duration_ms=12.5)
    assert record == sink[0]
    assert record["event"] == "agent_run"
    assert record["request_id"] == rid
    assert record["agent"] == "facts"
    assert "ts" in record


def test_unlisted_field_is_dropped(log, sink):
    record = log.log(
        "agent_run", request_id=new_request_id(), agent="pattern", status="ok", message=SECRET
    )
    assert "message" not in record
    assert SECRET not in json.dumps(sink)
    assert log.dropped_fields == [("agent_run", "message")]


def test_listed_field_with_free_text_value_is_dropped(log, sink):
    record = log.log(
        "agent_run",
        request_id=new_request_id(),
        agent="facts",
        status="error",
        error_code=SECRET,
    )
    assert "error_code" not in record
    assert SECRET not in json.dumps(sink)
    # Only the field name is remembered, never the value.
    assert SECRET not in json.dumps(log.dropped_fields)


def test_error_code_must_look_like_a_code(log):
    ok = log.log("agent_run", request_id=new_request_id(), agent="facts", status="error",
                 error_code="FACTS_TIMEOUT")
    assert ok["error_code"] == "FACTS_TIMEOUT"
    bad = log.log("agent_run", request_id=new_request_id(), agent="facts", status="error",
                  error_code="timeout while reading")
    assert "error_code" not in bad


def test_unknown_event_is_dropped(log, sink):
    assert log.log("debug_dump", request_id=new_request_id(), text=SECRET) is None
    assert sink == []
    assert log.dropped_events == ["debug_dump"]


def test_event_without_a_valid_request_id_is_dropped(log, sink):
    assert log.log("agent_run", agent="facts", status="ok") is None
    assert log.log("agent_run", request_id="not-a-uuid", agent="facts", status="ok") is None
    assert log.log("agent_run", request_id=SECRET, agent="facts", status="ok") is None
    assert sink == []
    assert SECRET not in json.dumps(log.dropped_events + [list(x) for x in log.dropped_fields])


def test_value_shapes_are_checked(log):
    rid = new_request_id()
    assert "agent" not in log.log("agent_run", request_id=rid, agent="somebody", status="ok")
    assert "status" not in log.log("agent_run", request_id=rid, agent="facts", status="great")
    assert "duration_ms" not in log.log("agent_run", request_id=rid, agent="facts", duration_ms=True)
    assert "duration_ms" not in log.log("agent_run", request_id=rid, agent="facts", duration_ms=-1)
    assert "duration_ms" not in log.log("agent_run", request_id=rid, agent="facts", duration_ms="fast")


def test_agent_run_accepts_start_and_end_times(log):
    rid = new_request_id()
    record = log.log(
        "agent_run",
        request_id=rid,
        agent="facts",
        status="covered",
        duration_ms=12.5,
        started_at="2026-10-21T02:50:12.345000+00:00",
        ended_at="2026-10-21T02:50:12Z",
    )
    assert record["started_at"] == "2026-10-21T02:50:12.345000+00:00"
    assert record["ended_at"] == "2026-10-21T02:50:12Z"
    assert log.dropped_fields == []


@pytest.mark.parametrize(
    "bad",
    [SECRET, "yesterday", "2026-10-21", "2026-10-21T02:50:12+05:30", "2026-10-21T02:50:12+00:00 extra", 5, None],
)
def test_a_time_field_with_any_other_value_is_dropped(log, bad):
    record = log.log("agent_run", request_id=new_request_id(), agent="facts", status="ok", started_at=bad)
    assert "started_at" not in record
    assert SECRET not in json.dumps(log.dropped_fields)


def test_disposition_event(log):
    rid = new_request_id()
    record = log.log(
        "disposition",
        request_id=rid,
        value="HOLD_DISAGREEMENT",
        signals={"facts": "covered", "pattern": "rising", "compliance": "clean"},
    )
    assert record["value"] == "HOLD_DISAGREEMENT"
    assert record["signals"]["pattern"] == "rising"

    bad = log.log("disposition", request_id=rid, value="HOLD_DISAGREEMENT",
                  signals={"facts": SECRET})
    assert "signals" not in bad
    bad = log.log("disposition", request_id=rid, value="HOLD_DISAGREEMENT",
                  signals={"notes": "covered"})
    assert "signals" not in bad
    assert "value" not in log.log("disposition", request_id=rid, value="SENT")


def test_reviewer_event_takes_codes_not_quotes(log):
    rid = new_request_id()
    record = log.log("reviewer", request_id=rid, round=1, verdict="FAIL",
                     failure_types=("UNCITED_CLAIM", "PII_SURVIVED"))
    assert record["failure_types"] == ["UNCITED_CLAIM", "PII_SURVIVED"]
    bad = log.log("reviewer", request_id=rid, round=1, verdict="FAIL",
                  failure_types=["sentence 2 says: " + SECRET])
    assert "failure_types" not in bad


def test_refinement_event_stores_category_and_agent_lists_only(log, sink):
    rid = new_request_id()
    record = log.log(
        "refinement",
        request_id=rid,
        round=2,
        category="scheme_change",
        agents_fired=["facts", "orchestrator"],
        agents_cached=["pattern", "compliance"],
        feedback="they are asking about the gold fund, not mid cap",
    )
    assert record["category"] == "scheme_change"
    assert record["agents_fired"] == ["facts", "orchestrator"]
    assert "feedback" not in record
    assert "gold fund" not in json.dumps(sink)
    bad = log.log("refinement", request_id=rid, round=1, category="make it shorter")
    assert "category" not in bad


def test_approval_event_allows_only_the_two_permitted_tools(log):
    rid = new_request_id()
    record = log.log("approval", request_id=rid, action="approve", draft_hash=HASH,
                     mcp_tools_called=("append_to_google_doc", "create_email_draft"))
    assert record["mcp_tools_called"] == ["append_to_google_doc", "create_email_draft"]
    bad = log.log("approval", request_id=rid, action="approve", draft_hash=HASH,
                  mcp_tools_called=["send_email"])
    assert "mcp_tools_called" not in bad
    assert "draft_hash" not in log.log("approval", request_id=rid, draft_hash="abc")


def test_default_sink_writes_json_through_standard_logging(caplog):
    logger = AllowListLogger()
    with caplog.at_level(logging.INFO, logger="support_agents.audit"):
        logger.log("agent_run", request_id=new_request_id(), agent="compliance", status="ok",
                   message=SECRET)
    assert len(caplog.records) == 1
    payload = json.loads(caplog.records[0].getMessage())
    assert payload["agent"] == "compliance"
    assert SECRET not in caplog.text

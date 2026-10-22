"""Allow-list logger (Architecture section 11.2).

Only the listed event types and the listed fields can be written. A field that is
not on the list, or whose value does not have the expected shape, is dropped and
only its *name* is counted. Values are never kept, so free text cannot slip into a
log through a field that merely exists.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any

from constants import (
    DISPOSITION_VALUES,
    FAILURE_CODES,
    FIRED_AGENTS,
    MCP_TOOLS,
    REFINEMENT_CATEGORIES,
)
from schemas import is_request_id

Validator = Callable[[Any], bool]

_ERROR_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,39}$")
_SIGNAL_VALUE = re.compile(r"^[a-z_]{1,32}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _is_int(value: Any) -> bool:
    # bool is a subclass of int, and a log field should never be a bool by accident.
    return isinstance(value, int) and not isinstance(value, bool)


def _one_of(*allowed: str) -> Validator:
    allowed_set = frozenset(allowed)
    return lambda value: isinstance(value, str) and value in allowed_set


def _list_of(*allowed: str) -> Validator:
    allowed_set = frozenset(allowed)
    return lambda value: (
        isinstance(value, (list, tuple))
        and all(isinstance(item, str) and item in allowed_set for item in value)
    )


def _error_code(value: Any) -> bool:
    return isinstance(value, str) and bool(_ERROR_CODE.match(value))


def _non_negative_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0


def _round(value: Any) -> bool:
    return _is_int(value) and 0 <= value <= 10


def _signals(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and set(value) <= {"facts", "pattern", "compliance"}
        and all(isinstance(v, str) and _SIGNAL_VALUE.match(v) for v in value.values())
    )


def _sha256(value: Any) -> bool:
    return isinstance(value, str) and bool(_SHA256.match(value))


# A UTC time such as 2026-10-21T02:50:12.345+00:00. Nothing else fits this shape, so free
# text cannot be written through a time field.
_UTC_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(\+00:00|Z)$")


def _utc_time(value: Any) -> bool:
    return isinstance(value, str) and bool(_UTC_TIME.match(value))


_AGENT_NAMES = (*FIRED_AGENTS, "reviewer")
_STATUS_VALUES = ("covered", "not_covered", "ambiguous", "ok", "error", "timeout")

# event type -> {allowed field -> validator for its value}
EVENT_SPECS: dict[str, dict[str, Validator]] = {
    "agent_run": {
        "request_id": is_request_id,
        "agent": _one_of(*_AGENT_NAMES),
        "status": _one_of(*_STATUS_VALUES),
        "duration_ms": _non_negative_number,
        # When the agent started and ended. The trace uses the start times to show that
        # the three agents ran together.
        "started_at": _utc_time,
        "ended_at": _utc_time,
        "error_code": _error_code,
    },
    "disposition": {
        "request_id": is_request_id,
        "value": _one_of(*DISPOSITION_VALUES),
        "signals": _signals,
    },
    "reviewer": {
        "request_id": is_request_id,
        "round": _round,
        "verdict": _one_of("PASS", "FAIL"),
        "failure_types": _list_of(*FAILURE_CODES),
    },
    "refinement": {
        "request_id": is_request_id,
        "round": _round,
        "category": _one_of(*REFINEMENT_CATEGORIES),
        "agents_fired": _list_of(*FIRED_AGENTS),
        "agents_cached": _list_of(*FIRED_AGENTS),
    },
    "approval": {
        "request_id": is_request_id,
        "action": _one_of("approve", "reject", "retry"),
        "draft_hash": _sha256,
        "mcp_tools_called": _list_of(*MCP_TOOLS),
    },
}

Sink = Callable[[dict[str, Any]], None]


def _default_sink() -> Sink:
    logger = logging.getLogger("support_agents.audit")

    def emit(record: dict[str, Any]) -> None:
        logger.info(json.dumps(record, sort_keys=True))

    return emit


class AllowListLogger:
    """Writes only allow-listed events and fields. Everything else is dropped."""

    def __init__(self, sink: Sink | None = None) -> None:
        self._sink: Sink = sink or _default_sink()
        # Names only. Values are never recorded here.
        self.dropped_events: list[str] = []
        self.dropped_fields: list[tuple[str, str]] = []

    def log(self, event: str, **fields: Any) -> dict[str, Any] | None:
        """Write one event. Returns the record written, or None if it was dropped."""
        spec = EVENT_SPECS.get(event)
        if spec is None:
            self.dropped_events.append(event if isinstance(event, str) else "<non-string>")
            return None

        record: dict[str, Any] = {}
        for name, value in fields.items():
            validator = spec.get(name)
            if validator is not None and validator(value):
                record[name] = list(value) if isinstance(value, tuple) else value
            else:
                self.dropped_fields.append((event, name))

        # An event with no valid request ID cannot be tied to a ticket, so it is dropped.
        if "request_id" not in record:
            self.dropped_events.append(event)
            return None

        record = {
            "event": event,
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            **record,
        }
        self._sink(record)
        return record

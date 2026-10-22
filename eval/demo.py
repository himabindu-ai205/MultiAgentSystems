"""Walk one clean answer, one not-covered, one disagreement, one PII case,
one refinement and one approval. Prints the allow-listed trace and the graph shape.

    python eval/demo.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from agents.stubs import stub_compliance, stub_facts, stub_pattern  # noqa: E402
from approval.gate import ApprovalGate  # noqa: E402
from approval.mcp_client import McpClient  # noqa: E402
from data_layer import load_all  # noqa: E402
from eval.fixtures import load_branches  # noqa: E402
from eval.runner import RULES_ONLY, _FailingMcp, _build  # noqa: E402
from orchestrator.dispatch import Orchestrator  # noqa: E402
from orchestrator.drafter import Drafter  # noqa: E402
from orchestrator.graph import NODE_NAMES, AgentSet  # noqa: E402
from orchestrator.report import format_run  # noqa: E402
from reviewer.gate import Reviewer  # noqa: E402


DIAGRAM = """
START
  | fan-out (same step)
  +-- facts
  +-- pattern
  +-- compliance_inbound
  | join
  v
merge + draft + reviewer
  | human: refine / approve / discard
  v
approval gate -> append_to_google_doc -> create_email_draft
                 (never send_email)
""".strip()


def main() -> int:
    data = load_all()
    events: list[dict] = []
    mcp = _FailingMcp()
    app = _build(RULES_ONLY, data, events, mcp=mcp)
    branches = {row["id"]: row for row in load_branches()}

    print(DIAGRAM)
    print()
    print("=== 1. Clean answer ===")
    clean = app.orchestrator.submit(branches["B1"]["text"])
    print(format_run(clean))

    print("\n=== 2. Not covered ===")
    missing = app.orchestrator.submit(branches["B2"]["text"])
    print(format_run(missing))

    print("\n=== 3. Disagreement (Facts covered, Pattern rising) ===")
    disagreement = Orchestrator(
        AgentSet(
            facts=lambda *a, **k: stub_facts(),
            pattern=lambda *a, **k: stub_pattern(scenario="rising"),
            compliance=lambda *a, **k: stub_compliance(),
        ),
        Drafter(),
        reviewer=Reviewer(),
    ).submit("What is the exit load of the HDFC Large Cap fund?")
    print(format_run(disagreement))

    print("\n=== 4. PII ===")
    pii = app.orchestrator.submit(branches["B4"]["text"])
    print(format_run(pii))
    print("raw PAN present:", "ABCDE1234F" in json.dumps(pii.session.model_dump(mode="json")))

    print("\n=== 5. Refinement (scheme change) ===")
    refined = app.refiner.refine(clean.session, "they're asking about the Large Cap fund")
    if refined.run is not None:
        print("agents_fired:", refined.run.agents_fired)
        print("agents_cached:", refined.run.agents_cached)
        print(format_run(refined.run))

    print("\n=== 6. Approval ===")
    gate = ApprovalGate(McpClient(mcp), settings=RULES_ONLY)
    ready = app.orchestrator.submit(branches["B1"]["text"])
    outcome = gate.approve(ready.session, agents_fired=ready.agents_fired)
    print(outcome.status, outcome.message)
    print("mcp calls:", mcp.calls)

    print("\n=== Allow-listed trace (tail) ===")
    for event in events[-8:]:
        print(json.dumps(event, sort_keys=True))
    print("nodes:", NODE_NAMES)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

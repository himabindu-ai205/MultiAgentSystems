"""Walk the full multi-agent flow on one complaint and print every stage.

Default complaint (as given for a manual check):

    why was I charged an exit load, what was the amount and why I was charged?

That question names no scheme, so Facts will come back ``ambiguous`` and ask which
fund. A second default names HDFC Large Cap so you can also see a cited amount.

Run from the project root:

    python scripts/verify_flow.py
    python scripts/verify_flow.py --rules-only
    python scripts/verify_flow.py --refine "they're asking about the Mid Cap fund"
    python scripts/verify_flow.py "your complaint here"

``--rules-only`` skips Groq and Gemini (no API keys, no token use). Without it, the
run uses ``config/settings.toml`` (Groq for the specialists/drafter, Gemini for the
Reviewer). Approval is Phase 9 and is not part of this walk.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from app import build_app  # noqa: E402
from config import Settings, load_settings  # noqa: E402
from ingress.redactor import IngressError  # noqa: E402
from logging_allowlist import AllowListLogger  # noqa: E402
from orchestrator.report import EXIT_LOAD_NAMED, EXIT_LOAD_QUESTION, format_run  # noqa: E402


def _rules_only() -> Settings:
    none = {"provider": "none", "model": ""}
    return Settings(
        llm={
            "provider": "none",
            "model": "",
            "facts": none,
            "pattern": none,
            "compliance": none,
            "drafter": none,
            "reviewer": none,
            "refinement": none,
        }
    )


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Print a full-stack walkthrough of one complaint.")
    parser.add_argument(
        "complaints",
        nargs="*",
        help="Complaints to run. Default: the exit-load question, then the same with HDFC Large Cap named.",
    )
    parser.add_argument(
        "--rules-only",
        action="store_true",
        help="Do not call Groq or Gemini. Rules and templates only.",
    )
    parser.add_argument(
        "--refine",
        metavar="FEEDBACK",
        help="After each first run, apply this refinement and print the second pass.",
    )
    return parser.parse_args(argv)


def _cases(complaints: list[str]) -> list[tuple[str, str]]:
    if complaints:
        return [(f"complaint {i}", text) for i, text in enumerate(complaints, start=1)]
    return [
        ("exit load, no scheme named", EXIT_LOAD_QUESTION),
        ("exit load, HDFC Large Cap named", EXIT_LOAD_NAMED),
    ]


def main(argv: list[str] | None = None) -> int:
    args = _parse(sys.argv[1:] if argv is None else argv)
    settings = _rules_only() if args.rules_only else load_settings()
    events: list[dict] = []
    logger = AllowListLogger(sink=events.append)
    try:
        app = build_app(settings, logger=logger)
    except (RuntimeError, ValueError) as error:
        print(error)
        print('Hint: pass --rules-only to walk the stack without API keys.')
        return 1

    code = 0
    for i, (title, complaint) in enumerate(_cases(args.complaints)):
        events.clear()
        if i:
            print()
        try:
            run = app.orchestrator.submit(complaint)
        except IngressError as error:
            print(f"{title}\n  skipped: {error}")
            code = 2
            continue
        print(format_run(run, title=title, events=events))
        if args.refine:
            events.clear()
            result = app.refiner.refine(run.session, args.refine)
            print()
            if result.status == "refused":
                print(f"Refinement refused: {result.message}")
            elif result.run is not None:
                print(format_run(result.run, title=title + " (after refine)", events=events))
                if result.message:
                    print(f"Note: {result.message}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())

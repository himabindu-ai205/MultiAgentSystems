# Known limits of the Phase 10 acceptance run

These match Architecture §14 and eval.md section 12, plus what this recorded run showed.

- **Few real clean answers.** The 20 Play Store reviews are almost all about the app. The corpus only answers scheme-page questions. E1 is therefore mostly `NOT_COVERED`. That is the correct outcome, not a failure. The clean-answer path is the E2 B1 scheme question (and the demo's first walk-through).
- **Synthetic PII.** The reviews contain none, so B4 inserts a fake PAN. Real people's PII can be written in ways these detectors do not cover.
- **Small sets.** Twenty complaints show that the pipeline is well-formed. They are not a rate. Do not read a quality metric from E1 as a statistic.
- **Disagreement is rare on live reviews.** Facts `covered` plus Pattern `rising` needs a scheme question that also matches a surging theme. The demo shows that row with a Pattern stub set to `rising`, which is the decision-table case. E2 B3 checks recurring/rising on a real charts complaint instead.
- **Recorded vs live.** The committed evidence is `--mode recorded` (every model provider set to `none`). Live Groq/Gemini wording can differ; the gates are written so that a single miss still fails the check.
- **Hinglish.** Coverage depends on the phrase lists. Gaps will show up as missed advice refusals or missed theme matches; add those prompts to `eval/sets/e3_advice.json` when found.
- **MCP.** The Python process does not speak Cursor's Gmail MCP. The demo and extra checks use a fake transport. A write to the configured Google Doc needs `mcp.google_doc_id`, `mcp.email_recipient`, and an injected `McpTransport`.
- **Human scoring.** Reply-quality rubric items Q3 and Q12 are not scored here. Gates are code checks only.

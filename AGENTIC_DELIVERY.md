# Agentic Delivery Loop — Property Analysis Product

This document is the constitution for the autonomous build loop. Read it before
any work cycle. The loop continues until all phase acceptance criteria pass.
Stop only when a true blocker (defined below) appears.

## The Loop

```
plan -> implement -> verify -> critique -> checkpoint -> continue
```

One cycle:

1. **Read state.** Open `AGENT_STATE.md` and `AGENT_BACKLOG.md`. Identify
   highest-priority unblocked task.
2. **Plan.** Write the smallest coherent slice that satisfies that task's
   acceptance criteria. Do not over-scope.
3. **Implement.** Edit code. No shortcut hacks. Fix root causes, not symptoms.
4. **Verify.** Run the task's verification command. Run regression at phase
   gates. Run `agent_check.py`. Every assertion in acceptance criteria must
   pass before the slice is "done."
5. **Critique.** Re-read the diff. Look for: hallucinated values, suppressed
   blockers, fixture-as-live leaks, broken provenance, missing source notes,
   tests that pass by silencing errors.
6. **Checkpoint.** Update `AGENT_STATE.md` with: what changed, what passed,
   what's the next action. Update task status in `AGENT_BACKLOG.md`.
7. **Continue.** Pick the next task. Do not stop until the phase is complete
   or a true blocker appears.

## Phase Gates

Each phase ends when:
- All tasks in the phase are marked `done`.
- Regression suite passes (all cases, all checks).
- `agent_check.py` passes.
- `AGENT_STATE.md` reflects the phase-complete state.

Do not start the next phase until the current phase gate passes.

## Stop Conditions (a cycle ends)

- Phase gate reached and verified.
- A true blocker is encountered (see below).
- The session context budget is exhausted (rare).

Do NOT stop after a single small task. The expectation is to keep going.

## Human Escalation (true blockers)

Escalate (stop and surface the question) only when:

1. **Missing API key or secret** that cannot be inferred. (e.g., a paid API
   key the user hasn't shipped.)
2. **Irreversible packaging / deploy choice** that affects the product shape
   in a way the docs do not already decide. (e.g., "publish to PyPI vs not"
   is a Sean decision; "use SQLite vs flat files" is not — pick SQLite.)
3. **Business decision** that depends on Joe's preferences and is not in any
   spec doc. (e.g., "should batch mode email Joe a digest?" — ask.)
4. **Acceptance criterion fundamentally cannot be met** even after honest
   debugging. Document the blocker, mark the task `blocked`, move on.

Do NOT escalate for:
- Routine technical choices that fit existing patterns.
- Library version pinning.
- Schema design decisions when the spec is clear.
- Test names, file layout, docstring wording.

## Verification Commands

The loop runs these in order at each phase gate:

```bash
cd C:/Users/<your-name>/Documents/joe-property-analysis

# 1. Regression suite (deterministic, no network)
# Windows PowerShell:
$env:WPRDC_MODE = "off"; python -m scripts.property_analysis.regression
# macOS/Linux:
WPRDC_MODE=off python -m scripts.property_analysis.regression

# 2. Lightweight integrity check
python -m scripts.property_analysis.agent_check

# 3. Smoke test the CLI on Joe's known property
python -m scripts.property_analysis.analyze \
  --address "1417 S Canal St, Pittsburgh, PA 15215" \
  --price 130000 --arv 255000 --rehab 55000 \
  --rate 0.095 --down 1.0 \
  --output-dir c:/tmp/pa_loop_smoke
```

All three must pass with zero failures before a phase is marked complete.

## Trust / No-Hallucination Rules

These rules are enforced by code AND by the loop:

1. **No load-bearing number enters the model unless its source is one of:**
   user input (CLI / intake), local static DB (HUD FMR, geo.db, millage
   table), deterministic API/fetcher (WPRDC, RentCast in live mode), or
   manual override in `reference_data.xlsx`.
2. **Every number carries `Provenance`.** value + source + confidence +
   attempts + reconciliation + raw payload.
3. **Blockers suppress recommendations.** If any blocker is active, neither
   the markdown report nor the JSON nor the UI may display a verdict, max
   bid, or pass/fail counts. The string `DIAGNOSTIC_ONLY` is the substitute.
4. **Fixture data is never shown as live.** RentCast adapter mode is named
   prominently in every Rent Stack section. Cache namespaces are isolated
   per mode. The default mode in production is `off`.
5. **Claude does not invent underwriting facts.** No LLM call inside the
   production path produces rent, tax, ARV, rehab, beds, or thresholds.
   The IC memo and diligence-question modules are deterministic / rules-based.
6. **No silent estimates.** When the lookup chain cannot resolve an input,
   a blocker fires. Estimates are allowed only when explicitly marked low-
   confidence in the source-audit table.
7. **No destructive file or git operations.** No `rm -rf`, no `git reset
   --hard`, no force pushes. Output files go to per-deal packet folders.

## Guardrails the Loop Must Enforce

Before committing any change, verify:

- `agent_check.py` passes (catches diagnostic-leak regressions, fixture-as-
  live mistakes, missing provenance, packet folder integrity).
- Regression suite includes a case for any new code path.
- New code does not introduce `print(...)` of secrets, API keys, or PII.
- New modules import via `scripts.property_analysis.*` style (consistent).
- Generated artifacts land in `output/projects/<client>/<address>/<run>/`,
  never in repo root or in `output/projects/<client>/` flat.

## Context Preservation

Each cycle MUST update `AGENT_STATE.md`. The file is the resume point. If
a future cycle starts cold (new context), it reads:
- `AGENTIC_DELIVERY.md` (this file — the rules)
- `AGENT_STATE.md` (where we are)
- `AGENT_BACKLOG.md` (what's next)

and is operational without rereading the whole repo. Keep these three files
SHORT and CURRENT.

## Phase Plan (current product)

| Phase | Goal | Acceptance |
|---|---|---|
| 1 — Data Trust | `geo.db` + county disambiguation + AR regression + source freshness | All SAFMR lookups resolve county; ambiguous ZIPs without resolvable county block recommendation; Arkansas test case passes |
| 2 — Intake | DB-backed deal intake, CLI flags (`--create-deal`, `--from-intake`, `--list-deals`, `--analyze-ready`) | Create / list / analyze-ready round-trip works; packet links back to deal_id |
| 3 — Forward-Looking | Deterministic sensitivity engine; rule-based diligence questions; "what would change this" | Sensitivity table renders 7 perturbations; binding-constraint reason text appears; diligence questions appear in IC memo |
| 4 — UI | Local FastAPI one-pager: intake / source review / underwriting / packet | Server starts at localhost; can analyze a deal end-to-end; UI never shows verdict when blockers exist |
| 5 — Productization | Regression 18-20 cases; install flow; docs; no artifact pollution | All checks pass; clean-venv install succeeds; every packet has Excel + markdown + sources.json |

## When the Loop Is Done

When Phase 5 acceptance is met:
- Update `AGENT_STATE.md` with `status: PRODUCT V1 COMPLETE`.
- Move all backlog items to `done` or `deferred` (with reason).
- Surface the completion to Sean with: regression result, agent_check
  result, smoke test result, packet path, list of features shipped.

The autonomous loop then terminates.

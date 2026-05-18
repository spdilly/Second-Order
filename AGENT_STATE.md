# Agent State — Property Analysis Product

**Last updated**: 2026-05-18 (Phase 6 / V1.1 SHIPPED)
**Status**: V1.1 RELEASED — published to github.com/spdilly/Second-Order
**Current phase**: 6 of N — **done**
**Loop owner**: autonomous (resumable via `/property-autonomous` — no
open phase)

## Next action

None for Phase 6. The deferred backlog in `AGENT_BACKLOG.md` is the
inventory of next-phase candidates. Likely Phase 7 leads:
- RentCast live wiring (waits on Joe's API key)
- Side-by-side deal comparison view
- Mobile-responsive layout

## What V1.1 shipped

- **Project-centric workflow.** Edit any input on the deal page → click
  Run analysis → fresh packet folder, prior runs preserved. History
  page lists every run with verdict + headline metrics; any historical
  run is openable through `/deals/{id}/runs/{run_id}`.
- **Verdict block in sources.json.** Every non-diagnostic run records
  recommendation, max-bid, and headline metrics so the history viewer
  reads it directly (no math re-run).
- **DB-backed Property_Overrides.** Migrated from `reference_data.xlsx`
  into `deal_intake.property_overrides` (one-time import). CRUD via the
  `/overrides` web UI.
- **DB-backed Thresholds.** Same pattern, key/value schema. Editable
  via `/thresholds`.
- **Per-deal threshold overrides.** Five nullable columns on the deal
  table; when set, override beats global at analyze time. UI on the
  deal-edit form.
- **PDF export.** `/deals/{id}/runs/{run_id}/pdf` via Playwright/Chromium.
- **Batch intake.** Paste newline-separated addresses into the home
  textarea; each line becomes a draft deal.
- **Search + status filter** on home page.
- **Extended lifecycle states** — offer_made, under_contract, passed,
  closed, preserved through update_deal calls.

## Phase 6 direction (captured from Sean 2026-05-18)

> "Need a history page. Or, like, a create a file. This file can then
> be edited, assumptions changed. A 'project' for each address. Each
> file could live in a web interface as well, and not have to rely so
> much on excel. Same math."

Phase 6 selected themes (multi-select from the scoping question):
1. Data-maintenance web UI — overrides + thresholds + PDF export
2. Workflow — batch entry, history search, status tracking
3. The reframed Project model — each address is an editable persistent
   project with run history; web UI is the primary editing surface

Phase 6 trust bar: same as V1 (full regression + agent_check, every
feature gets a test).

Out of Phase 6 scope (Phase 7 candidates or deferred): comparison view,
what-if recalc sliders, mobile layout, RentCast live wiring (that pass
runs separately once Joe sends an API key).

## Phase status

| Phase | Status | Notes |
|---|---|---|
| 1 — Data Trust | **done** (2026-05-17) | geo.db + county disambiguation + AR regression + source freshness. |
| 2 — Intake | **done** (2026-05-17) | SQLite deal_intake.db, 22-col Deal dataclass, CLI flags `--create-deal/--from-intake/--list-deals/--analyze-ready`, `mark_analyzed` round-trip. |
| 3 — Forward-Looking | **done** (2026-05-17) | Deterministic 9-perturbation sensitivity engine, binding-constraint magnitude in Decision Breakdown, rule-based diligence question generator. |
| 4 — UI | **done** (2026-05-17) | FastAPI server-rendered (Jinja2), 7 routes (`/healthz`, `/`, `/deals`, `/deals/{id}`, `/deals/{id}/analyze`, `/results/{id}/{run_id}`, `/packets/{id}`, `/files/{id}/{filename}`). Diagnostic-only suppression enforced in UI; smoke test asserts no verdict string ever leaks. |
| 5 — Productization | **done** (2026-05-17) | requirements.txt pinned; INSTALL.md + Joe-facing README + Monday test script refreshed; clean-venv install verified (caught + fixed Starlette ≥0.32 signature breakage); 21-case regression map documented. |

## What Phase 5 shipped

- `requirements.txt` — pinned the runtime deps used by V1: `fastapi>=0.115`, `uvicorn[standard]`, `jinja2`, `python-multipart`, `markdown`, `openpyxl`, `requests`, `usaddress`, `httpx`.
- `INSTALL.md` (project root) — fresh-machine setup, webapp/CLI usage, annual refresh commands, trust posture, verification expectation (`agent_check.py` 7/7 PASS).
- `output/projects/Joe Berlin/README.md` — rewritten to match V1: four entry points (webapp / Claude skill / CLI single / CLI intake), per-deal packet folders, DIAGNOSTIC ONLY safety mode, sensitivity + diligence sections, data-source matrix with full Provenance discipline.
- `output/projects/Joe Berlin/MONDAY_TEST_SCRIPT.md` — 8-step pre-handoff script: regression, agent_check, single-deal CLI, intake DB round-trip, webapp browser smoke, DIAGNOSTIC ONLY safety check, AR address, Excel + markdown artifact checks.
- `AGENT_BACKLOG.md` — full regression case map (1-21) documented inline so a future maintainer (or Sean) can see which trust property each case defends.
- `webapp/main.py` — TemplateResponse signature migrated to the new Starlette form `(request, name, ctx)`. Old `(name, ctx_with_request)` form raises in starlette ≥ 0.32 because the dict is interpreted as a request object. Caught only by the clean-venv test (T-504); would have shipped broken to Joe on any modern Python install.

## What Phase 4 shipped

- `webapp/__init__.py`, `webapp/main.py` — FastAPI app, 8 routes, Jinja2 filters (money/pct/ratio), Markdown→HTML rendering for the report body.
- `webapp/templates/base.html` — institutional CSS framework (no CDN, no JS framework), badge/banner/btn/source-grid classes.
- `webapp/templates/home.html` — intake form (10 fields) + deals list with status badges.
- `webapp/templates/deal.html` — source review per deal with NOT READY banner and disabled Analyze button when required inputs are missing.
- `webapp/templates/result.html` — per-run result. **Diagnostic-only banner replaces verdict** when blockers exist. Inputs source-grid renders every field's provenance (value/source/confidence/reconciliation). Rendered markdown report embedded below.
- `webapp/templates/packet.html` — packet folder file listing with download buttons.
- `/files/{deal_id}/{filename}` — secure file download endpoint with path-escape guard (rejects `..`, `/`, `\`, and any resolved path outside the packet folder).
- `scripts/property_analysis/intake.py` — now honors `PROPERTY_ANALYSIS_DB` env var so tests use an isolated DB.
- `scripts/property_analysis/regression.py` — Case 21 (Webapp routes smoke + diagnostic-only UI suppression). Uses FastAPI `TestClient`. Exercises every route. Includes a forced-diagnostic deal that asserts the UI suppresses every verdict string variant.

## Trust layer (cumulative, all shipped)

- Provenance dataclass; every input has `value / source / confidence / attempts / reconciliation`.
- HUD SAFMR + county FMR fallback (51,895 + 4,764 rows).
- `HUD SAHUD FMR` label bug fixed (idempotent `_hud_source_label()`).
- RentCast adapter with live / fixture / off modes; per-mode cache namespaces.
- Default RentCast mode is `off` (no canned data shown as live).
- WPRDC Allegheny adapter (ArcGIS + property-api, no auth, cached).
- Allegheny millage table → computed annual tax (within 5-7% of Joe's known data).
- Per-deal packet folders: `output/projects/<client>/<slug>/<YYYY-MM-DD_HHMMSS>/`
  contains `<slug>_proforma.xlsx`, `<slug>_report.md`, `<slug>_sources.json`.
- Diagnostic-only mode suppresses verdict in **markdown, JSON, and UI**.
- Property Facts section with UNSOUND-condition and nominal-sale flags.
- Phase 1: geo.db (Census 2020 ZCTA-county), ambiguity blocker, source_freshness in sources.json.
- Phase 2: DB-backed deal intake; `--from-intake` round-trips through analyze and marks the deal analyzed.
- Phase 3: 9-perturbation sensitivity, deal-flippers, binding-constraint magnitude, rule-based diligence questions.
- Phase 4: server-rendered UI; verdict suppression survives the full UI render.

## Modified files log (Phase 4 build cycle)

| File | Change | Reason |
|---|---|---|
| `webapp/__init__.py` | created | Package marker |
| `webapp/main.py` | created | FastAPI app + 8 routes + markdown rendering + path-escape file download |
| `webapp/templates/base.html` | created | CSS framework, badges/banners/btns |
| `webapp/templates/home.html` | created | Deal intake form + list |
| `webapp/templates/deal.html` | created | Per-deal source review + Analyze trigger |
| `webapp/templates/result.html` | created | Per-run result; diagnostic suppression |
| `webapp/templates/packet.html` | created | Packet file listing + downloads |
| `scripts/property_analysis/intake.py` | modified | `PROPERTY_ANALYSIS_DB` env override for test isolation |
| `scripts/property_analysis/regression.py` | modified | Case 21 (webapp routes + diagnostic UI assertion) |

## Active risks

| Risk | Status |
|---|---|
| Tax data for non-Allegheny addresses still requires manual override | Acceptable for V1; outside Allegheny → DIAGNOSTIC_ONLY (correct behavior). |
| RentCast free-tier API key not provided — adapter defaults to off | Acceptable for V1; not a blocker. |
| geo.db uses Census 2020 vintage; may miss new ZIPs | Annual refresh script ships. |
| `python-multipart` and `markdown` are now hard webapp deps | Need to pin in `requirements.txt` (Phase 5 / T-502). |
| Webapp ships no auth — local-only by design | Acceptable for V1 (Joe's workstation). Doc this clearly in README. |

## Last regression result

```
21 cases (140+ checks)
Result: ALL CASES PASSED
Date: 2026-05-17, V1 FINAL GATE
Command: python -m scripts.property_analysis.regression
Plus: python -m scripts.property_analysis.agent_check (7/7 PASS)
Plus: clean-venv install verified — pip install -r requirements.txt
      then agent_check 7/7 PASS inside the fresh venv
Plus: live uvicorn smoke (twice) — GET /healthz 200, GET / 200 (7639 bytes)
```

## Decisions made (durable, cumulative)

1. No LLM in production decision path. (Sean's call.)
2. Distribution: private git repo. Not zip. Updates via git pull.
3. RentCast adapter pattern extended to WPRDC adapter (live/off; no fixture mode).
4. County hint chain: WPRDC (Allegheny) → geo.db (Census) → none → blocker.
5. Diagnostic-only suppresses verdict in markdown, JSON, **and UI**.
6. Deal intake is DB-backed (SQLite), not Excel-first.
7. Sensitivity perturbations: rent -10%, rehab +20%, tax +20%, exit cap +100bps, rate +100bps, price +/-5%, price +/-10%.
8. geo.db built from Census 2020 ZCTA-county relationship. Annual refresh via `build_geo_db.py`. Dominant county by land-area overlap when ZIP spans multiple counties.
9. **Phase 4 addition**: UI is server-rendered Jinja2 with no SPA / no CDN / no JS framework. Renders without internet. Markdown report rendered via `markdown` lib with `tables` + `fenced_code` extensions only. File downloads go through a single `/files/{deal_id}/{filename}` endpoint with path-traversal guard; packet folders are never served via StaticFiles directly.
10. **Phase 4 addition**: Tests use `PROPERTY_ANALYSIS_DB` env var to isolate from production intake DB. Same mechanism extends to clean-install verification (T-504).
11. **Phase 5 addition**: `TemplateResponse(request, name, ctx)` is the only supported call style going forward. Starlette deprecated the dict-with-request form and raises in ≥ 0.32. requirements.txt pins `fastapi>=0.115` which carries a Starlette new enough to enforce this.

## Decisions deferred to Sean (true blockers)

None currently. (Loop can continue to Phase 5.)

## Resume protocol

V1 is complete. If a future session needs to extend the product:

1. Read `AGENTIC_DELIVERY.md` (rules — still authoritative).
2. Read this file (state).
3. Read `AGENT_BACKLOG.md` (deferred items D-001 through D-006).
4. Pick the highest-priority unblocked deferred item.
5. Run the loop. Update this file when slice is done.

Sealing gate run on 2026-05-17:

- `python -m scripts.property_analysis.regression` → ALL CASES PASSED (21)
- `python -m scripts.property_analysis.agent_check` → ALL AGENT CHECKS PASSED (7)
- Clean-venv install via fresh `python -m venv` + `pip install -r requirements.txt` → agent_check 7/7 PASS inside the new venv
- `uvicorn webapp.main:app` boots; `/healthz` and `/` both return 200

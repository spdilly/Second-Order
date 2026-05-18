# Agent Backlog — Property Analysis Product

Tasks are prioritized by phase. Within a phase, work top-to-bottom.

Status legend: `todo`, `in_progress`, `done`, `blocked`, `deferred`.

Every task has acceptance criteria and a verification command. A task is
not `done` until BOTH the criteria are met AND the verification command
passes.

---

## PHASE 1 — Data Trust

### T-101 — Build `geo.db` from Census ZIP-to-county crosswalk
**Status**: done (2026-05-17). 46,960 rows, 33,791 ZIPs. Sanity: 15215→Allegheny, 72202→Pulaski, 40601→Franklin.
**Why**: 10,106 SAFMR ZIPs map to multiple HUD areas; without a county
resolver, non-Allegheny ambiguous lookups can't disambiguate.
**Spec**:
- Download Census ZCTA-to-county relationship file (or HUD ZIP-COUNTY
  crosswalk if Census is unavailable).
- Build `templates/property_analysis/data/geo.db` SQLite with:
  - table `zip_county(zip TEXT, county_fips TEXT, county_name TEXT,
    state_fips TEXT, state TEXT, weight REAL)`
  - index on `zip`
  - metadata table with source URL + build timestamp
- Add `scripts/property_analysis/build_geo_db.py` (annual refresh).
**Acceptance**:
- `geo.db` exists at the expected path, > 100KB.
- SQLite query `SELECT COUNT(DISTINCT zip) FROM zip_county` returns 30,000+.
- Query for ZIP 15215 returns county_name containing "Allegheny".
- Query for ZIP 72202 (Sean's Little Rock) returns county_name containing "Pulaski".
**Verification**:
```bash
python -m scripts.property_analysis.build_geo_db
python -c "import sqlite3; conn=sqlite3.connect('templates/property_analysis/data/geo.db'); print(conn.execute('SELECT zip, county_name, state FROM zip_county WHERE zip IN (\"15215\",\"72202\",\"40601\")').fetchall())"
```

### T-102 — `geo.py` module with `lookup_county(zip)` and `lookup_state(zip)`
**Status**: done (2026-05-17). CountyInfo dataclass with ambiguous flag + alternates. Smoke validates 5 sample ZIPs.
**Why**: The lookup chain needs a deterministic resolver to feed county_hint.
**Spec**:
- `scripts/property_analysis/geo.py` with:
  - `lookup_county(zip: str) -> Optional[CountyInfo]`
  - `lookup_state(zip: str) -> Optional[str]`
  - Returns None gracefully if ZIP not in DB.
- Module is read-only against `geo.db`.
**Acceptance**:
- `lookup_county("15215").county_name == "Allegheny"` (case-insensitive contains).
- `lookup_county("72202").county_name` contains "Pulaski".
- `lookup_county("99999")` returns None.
- All-zip lookup works under 5ms (cached/indexed).
**Verification**:
```bash
python -m scripts.property_analysis.geo
```
(Module's `__main__` smoke-tests these cases.)

### T-103 — Wire county hint into `resolve_rent` for non-PA addresses
**Status**: done (2026-05-17). County-hint chain in analyze.py: WPRDC (Allegheny) → geo.db (US-wide) → none.
**Why**: Today only WPRDC supplies county_hint; non-PA addresses get
`confidence=medium` and the ambiguity is unresolved.
**Spec**:
- In `analyze.py`, when address is not PA (or WPRDC didn't return data),
  call `geo.lookup_county(zip)` and pass result as `county_hint` to
  `resolve_rent`.
- When geo returns a county AND the SAFMR lookup is ambiguous, confidence
  goes back to `high` and the reconciliation note explains which county
  was used.
**Acceptance**:
- Analyzing an Arkansas address (e.g., a Little Rock ZIP 72202) resolves
  HUD FMR with `confidence=high` (no ambiguity) since 72202 is unambiguous.
- Analyzing an ambiguous ZIP (e.g., 40601) without WPRDC but WITH geo.db
  resolves to a specific HUD area (likely Frankfort, KY MSA — the county
  containing 40601 is Franklin County).
- The Provenance attempt note for `monthly_rent` includes the geo-resolved
  county name when it was used as a disambiguator.
**Verification**:
```bash
python -m scripts.property_analysis.analyze \
  --address "100 Main St, Little Rock, AR 72202" \
  --price 100000 --arv 130000 --rehab 20000 --beds 3 \
  --output-dir c:/tmp/pa_t103 2>&1 | grep -i "geo"
```

### T-104 — Block recommendation when SAFMR is ambiguous AND county is unresolvable
**Status**: done (2026-05-17). AMBIGUOUS_ZIP blocker fires; diagnostic mode suppresses verdict.
**Why**: Current behavior silently picks the highest-rent match. For a
trust product, if we can't determine which county the ZIP is in, we should
DIAGNOSTIC_ONLY the report.
**Spec**:
- In `analyze.py` after lookups, if `p_rent.reconciliation` contains
  ambiguity language AND no county hint resolved AND `p_rent.confidence`
  is not "high", add a blocker:
  > "AMBIGUOUS_ZIP: ZIP X maps to N HUD areas. The model cannot pick a
  > canonical Section 8 rent without knowing the county. Pass --county or
  > add a Property_Overrides entry."
- Diagnostic mode then kicks in (verdict suppressed).
**Acceptance**:
- Analyzing ZIP 40601 (Kentucky, 6 HUD areas) without geo.db enabled OR
  with geo.db returning no resolution produces a blocker.
- Same address with geo.db successful produces NO blocker (county
  disambiguates).
**Verification**:
```bash
python -m scripts.property_analysis.analyze \
  --address "200 Capitol Ave, Frankfort, KY 40601" \
  --price 150000 --arv 200000 --rehab 25000 --beds 3 \
  --output-dir c:/tmp/pa_t104 2>&1 | grep -i "AMBIG\|DIAGNOSTIC"
```

### T-105 — Add source freshness metadata to `sources.json`
**Status**: done (2026-05-17). source_freshness block contains hud_fmr_fy, geo_db_vintage, rentcast_mode, wprdc_tax_year, wprdc_as_of_date, allegheny_millage_tax_year, report_generated_at.
**Why**: Per Sean's spec — cache timestamps and source refresh dates should
appear in the audit so a reviewer can see how stale each input is.
**Spec**:
- For each Provenance entry, capture (when applicable):
  - `cache_age_seconds` (if value came from cache)
  - `source_fy` for HUD FMR (e.g., 2026)
  - `tax_year` for WPRDC (from the assessment record)
  - `as_of_date` for WPRDC
- Add to `sources.json` under each provenance entry.
**Acceptance**:
- `sources.json` for a 1417 S Canal St run contains `tax_year: 2026` for
  the WPRDC tax attempt.
- `sources.json` contains `source_fy: 2026` for the HUD SAFMR attempt.
**Verification**:
```bash
python -m scripts.property_analysis.analyze \
  --address "1417 S Canal St, Pittsburgh, PA 15215" \
  --price 130000 --arv 255000 --rehab 55000 --beds 3 \
  --rate 0.095 --down 1.0 --output-dir c:/tmp/pa_t105
python -c "import json,glob; p=sorted(glob.glob('c:/tmp/pa_t105/**/*sources.json', recursive=True))[-1]; d=json.load(open(p)); print('tax_year' in str(d), 'source_fy' in str(d))"
```

### T-106 — Regression cases: Arkansas + ambiguous ZIP
**Status**: done (2026-05-17). Cases 13 (AR resolves), 14 (KY ambiguous disambiguated), 15 (source_freshness in sources.json).
**Why**: Phase 1 acceptance requires AR coverage and ambiguity coverage.
**Spec**:
- Add `case_13_arkansas_resolves` — uses ZIP 72202 (Little Rock), expects
  SAFMR `confidence=high`, no blocker.
- Add `case_14_ambiguous_zip_blocks_without_geo` — uses ZIP 40601, expects
  blocker when county not resolvable; expects `confidence=high` when geo.db
  resolves county.
**Acceptance**:
- Both cases pass under `WPRDC_MODE=off` (regression runs without network).
**Verification**:
```bash
WPRDC_MODE=off python -m scripts.property_analysis.regression
```

### T-107 — Phase 1 gate
**Status**: done (2026-05-17). Regression 17 cases / 70+ checks PASS. agent_check 7/7 PASS. CLI smoke on 1417 S Canal St produces clean packet.
**Spec**: Run full regression, `agent_check.py`, smoke test the CLI on
1417 S Canal St and an AR address. All three must pass.
**Acceptance**: zero failures.

---

## PHASE 2 — Intake

### T-201 — Design `deal_intake.db` schema
**Status**: done (2026-05-17)
**Spec**: SQLite at `templates/property_analysis/data/deal_intake.db`.
Columns per Sean's `DB_AND_UI_REVIEW.md` Deal Intake table (22 columns).
Add `created_at`, `updated_at`, `last_analyzed_run_id`.
**Acceptance**: schema migration script exists; SQLite file created
empty on first run; schema documented.

### T-202 — `intake.py` module: create / list / read / update
**Status**: done (2026-05-17)
**Spec**: `scripts/property_analysis/intake.py` with CRUD functions.
Pydantic-like dataclass `Deal`. Generate `deal_id` as
`<slug>_<YYYYMMDD>_<n>`.

### T-203 — CLI flags: `--create-deal`, `--from-intake`, `--list-deals`, `--analyze-ready`
**Status**: done (2026-05-17)
**Acceptance**: Round-trip works: create deal → list it → analyze from it
→ packet folder is linked back to deal_id in `deal_intake.db`.

### T-204 — Per-deal packet links back to `deal_id`
**Status**: done (2026-05-17)
**Spec**: `sources.json` includes `deal_id`. `deal_intake.db` stores the
packet path of the last analysis per deal.

### T-205 — Regression cases for create/list/analyze-ready
**Status**: done (2026-05-17)
**Acceptance**: case_15, case_16, case_17 cover the intake flows.

### T-206 — Phase 2 gate
**Status**: done (2026-05-17)

---

## PHASE 3 — Forward-Looking Underwriting

### T-301 — `sensitivity.py` module — 7 perturbations
**Status**: done (2026-05-17)
**Spec**: Each perturbation runs `compute_returns` with one input changed.
Output: rows of (perturbation_name, new_irr, new_dscr, new_verdict, delta).

### T-302 — Binding constraint reason text in report
**Status**: done (2026-05-17)
**Spec**: When verdict is CONSIDER or PASS, list which thresholds failed
and the magnitude. E.g., "DSCR 1.18x is 7 bps below 1.25 threshold."

### T-303 — Deterministic diligence questions module
**Status**: done (2026-05-17)
**Spec**: `scripts/property_analysis/diligence.py` with rule-based question
generator. Rules: high rent variance, low DSCR, high rehab/price ratio,
high price/ARV, UNSOUND condition, nominal-price sales history, etc. Each
rule emits a specific question tied to the trigger.

### T-304 — "What would change this recommendation" block
**Status**: done (2026-05-17)
**Spec**: For each sensitivity perturbation that flips the verdict, list it
as a "deal-flipper" with the threshold it crossed.

### T-305 — Regression cases for sensitivity + diligence
**Status**: done (2026-05-17)

### T-306 — Phase 3 gate
**Status**: done (2026-05-17)

---

## PHASE 4 — UI

### T-401 — FastAPI scaffold + Tailwind base
**Status**: done (2026-05-17)
**Spec**: `webapp/main.py`, routes `/`, `/intake`, `/source-review`,
`/underwrite/<deal_id>`, `/packet/<deal_id>`. Server-rendered templates.

### T-402 — Intake form view
**Status**: done (2026-05-17)
**Acceptance**: form posts to `intake.py`, creates a Deal row, redirects
to source-review.

### T-403 — Source review view
**Status**: done (2026-05-17)
**Acceptance**: shows the Provenance table for the deal, lets the user
override any value, persists overrides to `deal_intake.db`.

### T-404 — Underwriting result view
**Status**: done (2026-05-17)
**Acceptance**: shows verdict / max-bid / sensitivity. When blockers exist,
shows DIAGNOSTIC_ONLY banner; never shows BUY/CONSIDER/PASS.

### T-405 — Packet view
**Status**: done (2026-05-17)
**Acceptance**: download links to Excel + markdown + sources.json.

### T-406 — Route smoke tests
**Status**: done (2026-05-17)
**Acceptance**: pytest covers each route returning 200 with expected text.

### T-407 — Phase 4 gate
**Status**: done (2026-05-17)

---

## PHASE 5 — Productization

### T-501 — Regression expansion + case map
**Status**: done (2026-05-17 / V1 ship)
**Spec**: Document the case map (1-21) in this file. Add any missing
coverage — minimum bar: Arkansas / ambiguous-ZIP / deal-intake roundtrip /
sensitivity / UI smoke / per-packet integrity / install-check. Phase 4
landed the UI smoke (case 21). Confirm coverage on tax-missing-only
DIAGNOSTIC mode (case 6c), JSON suppression (case 11), and the no-fixture
default check (case 7). Target: 22-25 cases.

### T-502 — Install polish (`requirements.txt`)
**Status**: done (2026-05-17 / V1 ship)
**Spec**: Pin runtime deps used by V1: `fastapi`, `uvicorn[standard]`,
`jinja2`, `python-multipart`, `markdown`, `openpyxl`, `requests`,
`usaddress`. Include `xlsx2html` / `pandas` only if a tab actually needs
them. Lock to versions that match the dev environment.

### T-503 — README + INSTALL + Monday test script
**Status**: done (2026-05-17 / V1 ship)
**Spec**: Update `README.md` to describe the four entry points (CLI single,
CLI from-intake, CLI batch, webapp). Write `INSTALL.md` covering Python
version, OneDrive checkout, `pip install -r requirements.txt`,
`build_geo_db` refresh, `uvicorn webapp.main:app`. Refresh the Monday
test script so Joe can run it without Sean walking him through.

### T-504 — Clean-venv install verification
**Status**: done (2026-05-17 / V1 ship)
**Acceptance**: `python -m venv .venv-test && .venv-test/Scripts/pip install -r requirements.txt` succeeds with no missing-package warnings; `python -m scripts.property_analysis.agent_check` passes inside the new venv.

### T-505 — Final Phase 5 gate
**Status**: done (2026-05-17 / V1 ship)
**Acceptance**: all phases done; all 22+ regression cases pass; webapp
boots via uvicorn; no artifact pollution; README and INSTALL match the
shipped behavior; `agent_check.py` clean.

### Regression case map (current — 21 cases)

| # | Name | Phase | Why it exists |
|---|---|---|---|
| 1 | Known-good control (1417 S Canal St) | baseline | Anchor case. If this breaks, the math engine drifted. |
| 2 | Override-heavy (Property_Overrides wins) | trust | Manual override must beat every external source. |
| 3 | SAFMR lookup chain | trust (Phase 1) | HUD ZIP-level SAFMR + county fallback. |
| 4 | Missing data → blockers surfaced | trust | No silent estimates. |
| 5 | Per-deal packet folder structure | output hygiene | One folder per run; three artifacts each. |
| 6 | Address-only WITH ARV → bid guidance | bid mode | Max-bid solver path. |
| 6b | Address-only WITHOUT ARV → DIAGNOSTIC ONLY | bid mode | Placeholder underwriting must suppress verdict. |
| 6c | Tax-missing → DIAGNOSTIC ONLY | trust | Even with everything else, one missing input means no verdict. |
| 7 | RentCast mode isolation (fixture / off) | trust | Cache namespaces — fixture data cannot leak to live. |
| 8 | Report text contains institutional sections | output | Report structure stable. |
| 9 | HUD label correctness (no "SAHUD" anywhere) | trust | Idempotent label helper. |
| 10 | No artifact pollution in Joe folder | output hygiene | Regression runs in tempdir, never in client folder. |
| 11 | JSON mode suppresses verdict in diagnostic state | trust (P0) | JSON output mirrors markdown suppression. |
| 12 | SAFMR ambiguity surfaced | trust (P0, Phase 1) | Multi-area ZIPs carry alternates + ambiguity flag. |
| 13 | Arkansas resolves via geo.db | trust (Phase 1) | Non-PA addresses get county hint from geo.db. |
| 14 | Ambiguous KY ZIP disambiguated by geo.db | trust (Phase 1) | Dominant-county heuristic resolves to Franklin. |
| 15 | source_freshness in sources.json | audit (Phase 1) | Auditor can see how stale each source is. |
| 16 | Deal intake: create/list/analyze-ready | intake (Phase 2) | DB-backed deal lifecycle. |
| 17 | analyze() from-intake: deal_id flows | intake (Phase 2) | sources.json + intake DB linked. |
| 18 | Sensitivity engine: 9 perturbations + flippers | forward (Phase 3) | Deterministic stress. |
| 19 | Diligence generator: rule-based questions | forward (Phase 3) | Specific must-asks, not generic checklist. |
| 20 | Binding-constraint magnitude column | forward (Phase 3) | "0.4 pp short" — not just PASS/FAIL. |
| 21 | Webapp routes smoke + diagnostic UI suppression | UI (Phase 4) | Verdict cannot leak through UI; path-escape guard. |

---

## PHASE 6 — Project-Centric Workflow (V1.1)

**Theme.** Each address becomes a persistent "project" with editable
assumptions and a run history. The web UI becomes the primary editing
surface, on par with Excel rather than a viewer. Math is unchanged.

**Sean's direction (2026-05-18):**
> "Need a history page. Or, like, a create a file. This file can then be
> edited, assumptions changed. A 'project' for each address. Each file
> could live in a web interface as well, and not have to rely so much on
> excel. Same math."

**Status: DONE (2026-05-18).** All sub-phases shipped and pushed to
`origin/main` (spdilly/Second-Order). Final gate: 27/27 regression cases
pass, agent_check 7/7, clean-venv install with `playwright install
chromium` passes inside a fresh venv, v1.1 zip built (61 files, 8.5 MB).

**Trust bar:** Same as V1 — full regression + agent_check. Every new
feature ships with at least one regression case. Math engine is not
touched in this phase.

### Architectural shift

V1: a Deal is one row with one current set of inputs. Editing creates a
new deal. The file system holds packets but the UI does not surface them
as history.

V1.1: a Deal is a Project. Inputs are editable in-place. Each
"Run analysis" press creates a new packet under the same project. The
packet folder structure (`<slug>/<YYYY-MM-DD_HHMMSS>/`) becomes the
project's history, surfaced in the web UI.

### Sub-phases

#### 6.1 — Project model (foundation)
- `T-601` — Per-deal editable inputs: extend the deal-detail page with an
  edit form covering every input compute.py accepts (purchase_price,
  arv_base, rehab_budget, beds_override, baths_override, prior_year_tax,
  interest_rate, down_payment_pct, refi_year, refi_ltv, hold_period_yrs,
  exit_cap_rate, insurance_annual, owner_utilities_annual, hoa_annual,
  section8_status, notes). Submits to `POST /deals/{id}/edit` which calls
  `intake.update_deal()`.
- `T-602` — "Run analysis" button replaces "Analyze deal". The button is
  always enabled when `deal.is_ready`. Each run creates a fresh packet
  folder; intake.mark_analyzed updates `last_packet_path` and
  `last_analyzed_run_id` (existing behavior); the prior packets remain on
  disk under the same slug folder.
- `T-603` — Run history page: `GET /deals/{id}/history` lists every
  timestamped sub-folder under `<slug>/`, newest first. Each entry shows
  run timestamp, verdict (parsed from sources.json), key metrics, and a
  link to the per-run result page.
- `T-604` — Per-run result viewer: `GET /deals/{id}/runs/{run_id}` opens
  any historical run, not just the latest. Reuses the existing
  result.html template. Diagnostic-only suppression still applies.
- `T-605` — Regression case 22: edit deal → re-run analysis → confirm two
  packets exist, history endpoint returns both, older one still openable.

#### 6.2 — Data-maintenance web UI (Property_Overrides + Thresholds)
- `T-611` — Property_Overrides CRUD UI: `GET /overrides`, `POST
  /overrides`, `POST /overrides/{address}/delete`. Reads/writes the
  existing reference_data.xlsx OR migrates the overrides into the intake
  DB. Decision deferred — see "Open decisions" below.
- `T-612` — Thresholds UI: `GET /thresholds`, `POST /thresholds`. Edits
  cap_rate_min / cash_on_cash_min / dscr_min / irr_min / max_price_to_arv.
- `T-613` — Per-project threshold overrides: a Deal may carry its own
  threshold values that beat the global Thresholds tab. Useful for
  one-off deals (institutional buyer, lender with tighter DSCR floor).
  When unset, fall through to global.
- `T-614` — PDF export: `GET /deals/{id}/runs/{run_id}/pdf` renders the
  markdown report as a clean PDF using Playwright (already a dev dep) or
  WeasyPrint. Branded header, page numbers, the works. Available as a
  "Download PDF" button on every result page.
- `T-615` — Regression case 23: override added via web, analyze for that
  address picks it up.
- `T-616` — Regression case 24: per-project threshold override changes
  verdict for one deal but not for another with the same metrics.
- `T-617` — Regression case 25: PDF export endpoint returns a non-empty
  application/pdf response for a known-good run.

#### 6.3 — Batch + history + search + status
- `T-621` — Batch intake: `POST /deals/batch` accepts a textarea of
  newline-separated addresses. Each address becomes a `new` /
  `needs_inputs` deal. Returns to home page showing the new rows.
- `T-622` — Home-page search/filter: search box filters the deals list
  by address substring; status filter dropdown (all / new / needs_inputs
  / ready / analyzed / offer_made / under_contract / passed / closed /
  archived).
- `T-623` — Extended status enum: add `offer_made`, `under_contract`,
  `passed`, `closed` to the VALID_STATUS tuple. UI lets Joe change status
  manually via a select on the deal page.
- `T-624` — Regression case 26: batch endpoint accepts 5 addresses,
  creates 5 deals, home page renders them with `needs_inputs` status.
- `T-625` — Regression case 27: search query filters list by substring
  match.

#### 6.4 — Productization
- `T-631` — Update `JOE_USER_MANUAL.md` Section 6 with the new project
  workflow (edit assumptions in place, re-run, view history). Add
  Section 14 covering PDF export.
- `T-632` — Update `README.md` in the Joe Berlin folder to reflect the
  project-centric model.
- `T-633` — Update `MONDAY_TEST_SCRIPT.md` with new manual steps:
  edit-and-rerun, batch entry, override via UI, PDF download.
- `T-634` — Clean-venv install verification (same as T-504) with the new
  feature surface.
- `T-635` — Rebuild `joe-property-analysis-v1.1.zip` from the allow-list.
  Verify blank-slate + clean-extract agent_check still passes.
- `T-636` — Final Phase 6 gate: all regression cases pass (target 27+),
  agent_check passes, clean-venv install passes, webapp boots, every
  new endpoint covered by at least one test case.

### Architecture decisions (resolved 2026-05-18)

All three open decisions resolved by Sean. Captured here so the loop
proceeds without ambiguity.

1. **Override storage:** Migrate `Property_Overrides` from
   `reference_data.xlsx` into a new table in `deal_intake.db`. One-time
   import from the xlsx on first run; xlsx becomes a download-as-backup
   link in the UI. T-611 implements this.
2. **PDF library:** Playwright / headless Chromium. Renders the existing
   markdown→HTML through Chromium. Add Playwright (with `playwright
   install chromium`) to `requirements.txt`. T-614 implements this.
3. **Per-project threshold storage:** 5 nullable columns on the deal
   table — `override_cap_rate_min`, `override_cash_on_cash_min`,
   `override_dscr_min`, `override_irr_min`, `override_max_price_to_arv`.
   Default NULL → fall through to global Thresholds. T-613 implements
   this.

### Phase 6 acceptance gate

All of:
- `python -m scripts.property_analysis.regression` passes (27+ cases).
- `python -m scripts.property_analysis.agent_check` passes (7/7).
- Webapp boots and renders the new endpoints.
- Clean-venv install from `requirements.txt` passes agent_check.
- Diagnostic-only suppression still survives in the UI for every result
  view (latest and historical).
- No regression in V1 behavior (every prior case still passes).

---

## Deferred (not in V1.1)

- D-001 — RentCast live mode wiring (waits on Joe's API key — separate
  pass per the handoff note)
- D-002 — Realtor.com / Homes.com web fetchers (out of scope per Sean)
- D-003 — Additional county parcel APIs beyond Allegheny
- D-004 — Portfolio memory / cross-deal learning
- D-005 — Photo / listing-image review (multimodal)
- D-006 — Public SaaS deployment
- D-007 — Mobile-responsive layout (Phase 7 candidate if Joe asks)
- D-008 — Side-by-side deal comparison view (Phase 7 candidate)
- D-009 — Map view of Allegheny parcels (Phase 7 candidate)

# Property Analysis (Joe Berlin)

Run Joe Berlin's single-family rental underwriting assistant from Claude Code.
The command uses the deterministic Python engine in `scripts/property_analysis/`
and returns the generated markdown report inline.

## Usage

`/property-analysis <address> [, $<price>] [, beds=X] [, arv=Y] [, rehab=Z]`

Examples:

- `/property-analysis 1417 S Canal St Pittsburgh PA 15215`
- `/property-analysis 1417 S Canal St Pittsburgh PA 15215, $130K, 3 beds, ARV $255K, rehab $55K`
- `/property-analysis Analyze 456 Oak Ave Pittsburgh PA at $185K, ARV $240K, rehab $35K`

## Execution Contract

1. Parse the user's request into:
   - `address` (required)
   - `price` / purchase price (optional)
   - `arv` (strongly recommended)
   - `rehab` (strongly recommended)
   - `beds`, `baths`, `down`, `rate`, `no-refi` if supplied

2. Run the CLI from the project root:

```bash
python -m scripts.property_analysis.analyze \
  --address "<full address>" \
  [--price <purchase_price>] \
  [--arv <arv>] \
  [--rehab <rehab_cost>] \
  [--beds <count>] \
  [--baths <count>] \
  [--down <fraction>] \
  [--rate <annual_rate>] \
  [--no-refi]
```

3. Return the markdown report printed by the CLI. Also include the packet
folder path at the end.

## Trust Rules

- Do not invent underwriting inputs.
- Do not browse the web and substitute unsourced values.
- Every load-bearing number must come from user input, reference data, bundled
database, deterministic fetcher/API, or an explicit default shown as default.
- If the CLI reports blockers, surface the DIAGNOSTIC ONLY warning first.
- If blockers exist, do not describe the deal as BUY, CONSIDER, or PASS.

## Data Sources

- **HUD rent**: bundled FY2026 HUD FMR/SAFMR SQLite database.
- **Geography**: bundled `geo.db` for ZIP/ZCTA-to-county context and SAFMR
  disambiguation.
- **Allegheny property facts/tax**: WPRDC/Allegheny adapter when available.
- **Market rent sanity check**: optional RentCast adapter. Default is `off`.
- **Overrides and thresholds**: `output/projects/Joe Berlin/reference_data.xlsx`.
- **Deal intake**: `templates/property_analysis/data/deal_intake.db`.

## Outputs

Each analysis writes a packet:

`output/projects/Joe Berlin/<address_slug>/<YYYY-MM-DD_HHMMSS>/`

Packet files:

- `<slug>_proforma.xlsx`
- `<slug>_report.md`
- `<slug>_sources.json`

## Other Useful Commands

```bash
# Health check before client handoff
python -m scripts.property_analysis.agent_check

# Full regression
python -m scripts.property_analysis.regression

# Launch local UI
python -m uvicorn webapp.main:app --port 8000

# List intake deals
python -m scripts.property_analysis.analyze --list-deals

# Analyze one intake deal
python -m scripts.property_analysis.analyze --from-intake <deal_id>

# Analyze all ready intake deals
python -m scripts.property_analysis.analyze --analyze-ready
```

## Current Release Gate

V1 is considered healthy when:

- `python -m scripts.property_analysis.regression` passes.
- `python -m scripts.property_analysis.agent_check` passes.
- The web app boots and `/healthz` returns 200.
- Diagnostic mode suppresses actionable verdicts in markdown, JSON, and UI.

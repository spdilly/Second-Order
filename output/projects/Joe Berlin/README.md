# Property Analysis Skill - Joe Berlin

Built by Sean Dillard, May 2026. **V1 - production**.

A local underwriting tool for Section 8 single-family rentals. Give it an
address (and a price if you have one), and it returns a populated
proforma, a markdown decision summary, and a JSON audit trail. No LLM in
the decision path. No cloud. No data leaves your machine.

---

## Four ways to run it

### 1. The local webapp (easiest)

```
cd C:/Users/<your-name>/Documents/joe-property-analysis
python -m uvicorn webapp.main:app --port 8000
```

Then open <http://127.0.0.1:8000/> in your browser. Create a deal,
populate the inputs, click **Analyze**. The result page shows the verdict,
every input with its source, and download links for the proforma /
markdown / sources JSON.

### 2. The Claude Code skill

```
/property-analysis 1417 S Canal St Pittsburgh PA 15215, $130K, 3 beds, ARV $255K, rehab $55K
```

Same engine, run through Claude Code. Useful when you're already in a chat.

### 3. CLI - single address

```
python -m scripts.property_analysis.analyze \
  --address "1417 S Canal St, Pittsburgh, PA 15215" \
  --price 130000 --arv 255000 --rehab 55000 --beds 3
```

Useful for quick command-line runs and shell scripting.

### 4. CLI - from intake DB

```
python -m scripts.property_analysis.analyze --create-deal       # interactive
python -m scripts.property_analysis.analyze --list-deals
python -m scripts.property_analysis.analyze --from-intake <deal_id>
python -m scripts.property_analysis.analyze --analyze-ready     # batch all ready deals
```

The intake DB (`templates/property_analysis/data/deal_intake.db`) is the
same one the webapp uses. Anything you create through the webapp shows up
here, and vice versa.

---

## What you get per run

Every analysis lands in its own folder:

```
output/projects/Joe Berlin/<address-slug>/<YYYY-MM-DD_HHMMSS>/
    <slug>_proforma.xlsx        - populated Excel proforma
    <slug>_report.md            - markdown decision summary
    <slug>_sources.json         - full provenance for every input
```

Three artifacts. One folder. Never overwrites. The intake DB tracks the
latest packet path per deal so the webapp can re-open it.

---

## The decision logic

The markdown report includes:

- **Verdict block** - BUY / CONSIDER / PASS, or **DIAGNOSTIC ONLY** when
  blockers exist (see below)
- **Inputs and provenance** - every input shows its value, source,
  confidence level, and the attempts that ran before it landed
- **Decision breakdown** - each threshold pass/fail with the **magnitude**
  short ("DSCR 1.23x is 0.02x short of the 1.25x floor")
- **Sensitivity table** - 9 deterministic perturbations (rent -10%,
  rehab +20%, tax +20%, exit cap +100bps, rate +100bps, price +/-5%, +/-10%)
- **What would change the recommendation** - every perturbation that
  flips BUY to PASS or vice versa
- **Diligence questions** - rule-based, deal-specific. If rent variance
  is high, the memo asks about Section 8 voucher status. If DSCR is near
  threshold, the memo asks about the lender's actual covenant.

Thresholds default to Joe's underwriting standards:

| Metric | Default | What it means |
|---|---|---|
| Cap Rate (Y1 / Cash Invested) | >= 8% | Year-1 NOI / cash invested |
| Cash-on-Cash (Y1) | >= 10% | Year-1 levered cash flow / equity |
| DSCR (min observed) | >= 1.25x | Lender comfort |
| Levered IRR (10-year) | >= 15% | Total return including refi |
| Price / ARV | <= 75% | Acquisition discount |

Verdict logic: **BUY** = all 5 pass; **CONSIDER** = 1-2 fail; **PASS** =
3+ fail.

---

## DIAGNOSTIC ONLY - the safety mode

If the model cannot resolve a load-bearing input - property tax, ZIP, or
rent - it does **not** produce a verdict. The markdown, the JSON, and the
webapp all show a **DIAGNOSTIC ONLY** banner with the blockers listed.
Common triggers:

- **AMBIGUOUS_ZIP** - the ZIP spans multiple HUD areas and no county
  could be resolved (rare; geo.db covers 33,000+ ZIPs)
- **NO_TAX_DATA** - outside Allegheny County and no override entered
- **PLACEHOLDER_UNDERWRITING** - ran address-only without an ARV

When you see DIAGNOSTIC ONLY, fix the blocker (usually by adding a
Property_Overrides row) and re-run. Do not treat numbers shown in
diagnostic mode as actionable.

---

## Data sources

| Input | Source | Notes |
|---|---|---|
| Rent (Section 8) | HUD FY26 SAFMR + county FMR | ZIP-level for Pittsburgh + 23 other metros; county-level elsewhere. 51,895 SAFMR rows + 4,764 county rows. |
| Property tax (Allegheny) | WPRDC parcel API + Allegheny millage table | No auth required. Live. |
| Property tax (other) | Property_Overrides | Manual entry; the override carries `prior_year_tax x 1.10` automatically. |
| Market rent | RentCast (off by default) | Optional, opt-in. Defaults to off so canned data cannot leak. |
| Beds / baths / property facts | RentCast (when on) to Property_Overrides | |
| ZIP to county | Census 2020 ZCTA-county (geo.db) | 46,960 rows. Disambiguates multi-area ZIPs by land area. |

Every value carries a full Provenance record: the value, the source it
came from, every attempt that ran (succeeded or not), and a
reconciliation note when multiple sources disagreed. The audit JSON is
saved alongside the proforma so you can always trace a number back.

---

## Reference data

`reference_data.xlsx` in this folder has the override and threshold
tables. The intake DB has subsumed deal-by-deal entry, but
`Property_Overrides` is still the place to record:

- Tax overrides for properties outside Allegheny
- Rent overrides when you have a confirmed Section 8 voucher amount
- Beds/baths overrides when listings are wrong

The override always wins.

---

## Annual refresh

HUD publishes new FMRs each October. To refresh:

1. Download from <https://www.huduser.gov/portal/datasets/fmr.html>:
   - `FY27_FMRs.xlsx`
   - `FY2027_SAFMRs.xlsx`
2. Save to `templates/property_analysis/data/`
3. Run: `python -m scripts.property_analysis.build_fmr_db`

The geo.db (Census ZCTA-county) refreshes via:

```
python -m scripts.property_analysis.build_geo_db
```

You only need to do this when the Census releases new ZCTAs (roughly
once per decade).

---

## Troubleshooting

**"DIAGNOSTIC ONLY: NO_TAX_DATA"** - outside Allegheny County. Add a
row to `Property_Overrides` with `annual_tax` or `prior_year_tax`.

**"DIAGNOSTIC ONLY: PLACEHOLDER_UNDERWRITING"** - you ran address-only
without an ARV. Either pass `--arv` or rely on the webapp form which
requires ARV before enabling the Analyze button.

**Excel opens with `#NAME?` errors** - your Excel is older than the
FV/PMT/IRR built-ins. Update Excel.

**Webapp won't start** - `pip install -r requirements.txt` from the
project root (see `INSTALL.md`).

---

## What's next (Phase 2 ideas)

- **RentCast live mode** - wire your API key when you're ready
- **Realtor.com auto-fetch** for non-Allegheny tax history
- **PDF output** alongside the Excel and markdown
- **Additional county parcel APIs** as you expand markets
- **Photo / listing-image review** (multimodal) once Phase 1 is bedded in

Ask Sean.

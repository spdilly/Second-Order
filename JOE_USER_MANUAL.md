# Joe Berlin Property Analysis V1

Brief setup and use guide for the zip package.

## 1. Unzip The Package

Unzip the folder somewhere simple, for example:

```powershell
C:\Users\<your-name>\Documents\joe-property-analysis
```

Keep the folder structure intact. The package needs these folders to stay
together:

- `.claude`
- `scripts`
- `templates`
- `webapp`
- `output`

## 2. Install Python (Skip If Already Installed)

If `python --version` in PowerShell already shows 3.11 or higher, skip this
section.

1. Go to <https://www.python.org/downloads/windows/> and download the latest
   Python 3.11 or newer installer for Windows (64-bit).
2. Run the installer. On the first screen, **check the box that says
   "Add python.exe to PATH"** before clicking Install. This is the single
   most common setup mistake.
3. Close PowerShell and reopen it after the install finishes so the PATH
   refreshes.
4. Confirm it works:

```powershell
python --version
```

You should see something like `Python 3.12.4`.

## 3. One-Time PowerShell Setting

Windows PowerShell blocks the script that activates the venv by default.
Run this once per machine, then never again:

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

Answer `Y` when prompted. This only affects your own user account.

## 4. Install The Tool

Open PowerShell in the unzipped folder and run:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m scripts.property_analysis.agent_check
```

The install is ready when `agent_check` says:

```text
ALL AGENT CHECKS PASSED
```

## 5. Launch The Web App

From the same PowerShell window:

```powershell
python -m uvicorn webapp.main:app --port 8000
```

Open this address in a browser:

```text
http://127.0.0.1:8000/
```

Leave the PowerShell window open while using the app. Press `Ctrl+C` to stop it.

## 6. Analyze A Deal In The Web App

Use the home page to create a deal.

Recommended first test:

```text
Address: 1417 S Canal St, Pittsburgh, PA 15215
Purchase price: 130000
ARV: 255000
Rehab: 55000
Bedrooms: 3
```

After the run, the app links to the deal packet:

- Excel proforma
- Markdown investment memo
- `sources.json` source audit

## 7. Use It With Claude Code Or Claude CoWork

The zip already contains the slash-command definition at:

```text
.claude/commands/property-analysis.md
```

Any Claude tool that reads a workspace folder will pick this up
automatically. There are two ways your team can use it.

### Solo / single-user (Claude Code)

Open Claude Code in the unzipped project folder. The slash command appears
on its own:

```text
/property-analysis 1417 S Canal St Pittsburgh PA 15215, $130K, 3 beds, ARV $255K, rehab $55K
```

Claude runs the same Python engine and returns the markdown report.

### Team / employees (Claude CoWork)

Claude CoWork is the same product packaged for a team. To let employees use
the tool, you have two practical options.

**Option A - Per-machine install (simplest, fully offline).** Each employee
gets their own copy of the unzipped folder on their workstation and runs
steps 2 through 4 once. After that, they open Claude CoWork in the project
folder and the `/property-analysis` slash command works for them the same
way it works for you.

**Option B - Run the web app as a small internal server.** Install the tool
once on a single machine that stays on, then bind the web app to your local
network so employees can reach it from a browser:

```powershell
python -m uvicorn webapp.main:app --host 0.0.0.0 --port 8000
```

Share the host machine's local IP (for example `http://192.168.1.50:8000/`)
with your team. Anyone on the same office network can run deals through it,
and all packets land in one place. This is local-network only and not
internet-facing.

If the slash command does not appear in Claude CoWork on a given machine,
tell Claude:

```text
Read .claude/commands/property-analysis.md and run the property analysis
command for: 1417 S Canal St Pittsburgh PA 15215, $130K, 3 beds, ARV $255K,
rehab $55K
```

## 8. Command Line Examples

Single deal:

```powershell
python -m scripts.property_analysis.analyze --address "1417 S Canal St, Pittsburgh, PA 15215" --price 130000 --arv 255000 --rehab 55000 --beds 3
```

List intake deals:

```powershell
python -m scripts.property_analysis.analyze --list-deals
```

Analyze all ready intake deals:

```powershell
python -m scripts.property_analysis.analyze --analyze-ready
```

## 9. What DIAGNOSTIC ONLY Means

If a critical input is missing, the tool will not produce a buy/no-buy verdict.
It will show:

```text
DIAGNOSTIC ONLY - NO RECOMMENDATION PRODUCED
```

Fix the blockers listed in the report, then re-run. Common blockers:

- missing ARV
- missing rehab budget
- missing property tax
- unsupported county tax source
- ambiguous geography

## 10. Data Sources

Bundled in the zip:

- HUD FY2026 FMR/SAFMR rent database
- ZIP/county geography database
- Excel proforma template
- reference data template

Live or optional:

- Allegheny/WPRDC property facts and tax for Pittsburgh-area properties
- RentCast market-rent sanity check, only if you wire in an API key later

RentCast is off by default. No API key is required for basic use. The
Allegheny/WPRDC adapter is live by default for Pittsburgh-area parcel facts; if
there is no internet connection, the tool still runs but may mark tax/property
facts as missing or use bundled cache when available.

## 11. How It Works Behind The Scenes

The tool is three pieces stitched together: a small set of pre-built
databases that ship inside the zip, a deterministic Python engine that
reads them, and a thin web app and slash command on top so you do not have
to look at the engine. Nothing in the decision path uses an LLM. Every
number that ends up in the report came from one of the sources listed below.

### The bundled databases

The zip carries three SQLite databases plus a few support files. They are
all built ahead of time so the tool works without internet at runtime.

- **`hud_fmr.db`** - HUD's published Fair Market Rents for fiscal year 2026.
  Two tables. The Small-Area FMR table (SAFMR) covers 51,895 ZIP codes
  inside HUD's 24 designated metro areas, including Pittsburgh and
  Philadelphia. The county-level FMR table covers the remaining 4,764
  counties nationwide. Both came from the official HUD release at
  huduser.gov. I downloaded the Excel files HUD publishes each October and
  loaded them into the database with the `build_fmr_db` script. When you
  analyze a deal, the engine looks up the ZIP first; if that ZIP is not in
  SAFMR, it falls back to the county FMR for the surrounding county.

- **`geo.db`** - the U.S. Census 2020 ZIP-to-county relationship file.
  46,960 rows covering 33,791 unique ZIP codes. Some ZIPs span more than
  one county; the engine picks the dominant county by land-area overlap and
  flags the ZIP as ambiguous if it cannot. This is what lets the tool
  analyze a property anywhere in the U.S., not just Pittsburgh. Source is
  Census.gov; refresh roughly once per decade when the Census publishes new
  ZCTAs.

- **`deal_intake.db`** - your own working list of deals. Empty at install.
  Every deal you create in the web app or with `--create-deal` lands here.
  The intake row links to the latest packet folder so the web app can
  re-open the result later.

### The live sources

Two sources call out to the internet only when you ask for them.

- **Allegheny / WPRDC.** For properties in Allegheny County, the engine
  pulls the parcel record from the Western Pennsylvania Regional Data
  Center (free, no key). It returns assessed value, building class, and
  recent sale history. Property tax is computed by applying the local
  millage rate to assessed value. This adapter is on by default. To turn it
  off for a session, set this before running the web app or CLI:

```powershell
$env:WPRDC_MODE = "off"
```

- **RentCast.** A paid market-rent comparison API. Off by default. If you
  ever want a second opinion on rent, ask me to wire your API key in and
  switch the mode to `live`. Until then, the engine never calls it.

### How a deal flows through the system

1. You enter address, ARV, rehab, and optionally price + beds + Section 8
   status.
2. The engine resolves the ZIP, looks up the county in `geo.db`, and
   resolves rent against `hud_fmr.db`.
3. If the property is in Allegheny, the engine pulls the WPRDC parcel and
   computes tax. If not, the engine looks for a manual tax override in
   `reference_data.xlsx`. Anywhere else, you supply the tax yourself.
4. The engine builds the year-by-year cashflow, runs sensitivity against
   nine perturbations (rent down 10%, rehab up 20%, tax up 20%, exit cap
   up 100 bp, rate up 100 bp, price +/-5%, price +/-10%), and scores the deal
   against your buy thresholds.
5. The engine writes the proforma, the markdown memo, and the source audit
   to a fresh timestamped folder under
   `output/projects/Joe Berlin/<address>/`.

Every load-bearing number carries provenance: where the value came from,
how confident the engine is, what other sources it tried, and how it
reconciled disagreements. You can open `sources.json` in any text editor
and trace any input back to its origin.

## 12. What You Maintain Yourself

There are four kinds of inputs the tool cannot get on its own. They live in
your reference workbook at:

```text
output/projects/Joe Berlin/reference_data.xlsx
```

You should fill these in as you go so the tool gets smarter over time.

- **Property-specific overrides.** When you know the actual rent, taxes, or
  beds for a specific address (because you own it, you have the voucher, or
  you've just done diligence), put them in `Property_Overrides`. An entry
  there beats every public source.
- **Tax for non-Allegheny counties.** If you start looking at deals outside
  Allegheny County, add the prior year's tax (from the county assessor's
  website or Realtor.com) and the engine will scale it +10% the way you
  already do mentally.
- **Rent overrides for confirmed Section 8 voucher amounts.** HUD FMR is a
  ceiling, not a guarantee. When a tenant's actual voucher pays less or
  more, override it.
- **Your buy thresholds.** The `Thresholds` tab carries your defaults for
  minimum cap rate, cash-on-cash, DSCR, IRR, and max price/ARV. Edit them
  any time. The engine reads them on every run.

Every run still writes a source audit so the report shows where each number
came from.

## 13. Updating The Bundled Data

The HUD FMR file changes once a year (each October). When the FY27 file is
out, tell me and I will rebuild `hud_fmr.db` from the new release and send
you a replacement zip. The geography database refreshes once per decade and
is not on your radar.

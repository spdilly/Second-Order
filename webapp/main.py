"""
Property Analysis — local FastAPI web app.

Server-rendered (Jinja2). No SPA. First screen is the working app.

Routes:
  GET  /                  -> deal intake form + list of deals
  POST /deals             -> create a deal (form post)
  GET  /deals/<deal_id>   -> source review for one deal
  POST /deals/<deal_id>/analyze -> run analyze pipeline, redirect to result
  GET  /results/<run_id>  -> underwriting result + sensitivity + diligence
  GET  /packets/<run_id>  -> packet view with download links
  GET  /healthz           -> {"ok": True}

Trust posture:
  - Diagnostic-only blockers suppress verdict/max-bid in EVERY view.
  - Source provenance is always rendered alongside any numeric input.
  - No JS framework. No external CDN. Renders without internet.

Launch:
  uvicorn webapp.main:app --reload --port 8000
  then visit http://127.0.0.1:8000/
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import markdown as _markdown

from scripts.property_analysis import intake
from scripts.property_analysis.analyze import analyze, _run_intake_deal

WEBAPP_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = WEBAPP_DIR / "templates"
STATIC_DIR = WEBAPP_DIR / "static"
# Empty directories are dropped by zip; recreate on startup so a fresh
# unzip + uvicorn boot does not fail on the StaticFiles mount.
STATIC_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Property Analysis", docs_url=None, redoc_url=None)
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Ensure intake DB is set up at import time
intake.schema_init()


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def _money(v) -> str:
    if v is None:
        return "—"
    return f"${v:,.0f}"


def _pct(v, d: int = 1) -> str:
    if v is None or v != v:
        return "—"
    return f"{v * 100:.{d}f}%"


def _ratio(v) -> str:
    if v is None or v != v or v == float("inf"):
        return "n/a"
    return f"{v:.2f}x"


templates.env.filters["money"] = _money
templates.env.filters["pct"] = _pct
templates.env.filters["ratio"] = _ratio


# ──────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────

@app.get("/healthz")
def healthz():
    return {"ok": True, "ts": datetime.now().isoformat(timespec="seconds")}


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    deals = intake.list_deals()
    # New Starlette signature: request first. The old (name, ctx) form
    # raises in starlette >= 0.32 because the dict is interpreted as a
    # request object.
    return templates.TemplateResponse(
        request,
        "home.html",
        {"deals": deals, "format_short": intake.format_deal_short},
    )


@app.post("/deals")
def create_deal(
    address: str = Form(...),
    arv_base: Optional[float] = Form(None),
    rehab_budget: Optional[float] = Form(None),
    section8_status: str = Form("assumed"),
    purchase_price: Optional[float] = Form(None),
    beds_override: Optional[int] = Form(None),
    prior_year_tax: Optional[float] = Form(None),
    interest_rate: Optional[float] = Form(None),
    down_payment_pct: Optional[float] = Form(None),
    notes: str = Form(""),
):
    fields = {
        "address": address,
        "section8_status": section8_status,
        "notes": notes,
    }
    # Only include numerics if non-None and non-zero (form sends 0 sometimes)
    for k, v in [
        ("arv_base", arv_base), ("rehab_budget", rehab_budget),
        ("purchase_price", purchase_price), ("beds_override", beds_override),
        ("prior_year_tax", prior_year_tax),
        ("interest_rate", interest_rate),
        ("down_payment_pct", down_payment_pct),
    ]:
        if v is not None and v != "" and v != 0:
            fields[k] = v
    deal = intake.create_deal(**fields)
    return RedirectResponse(url=f"/deals/{deal.deal_id}", status_code=303)


@app.get("/deals/{deal_id}", response_class=HTMLResponse)
def deal_detail(request: Request, deal_id: str, saved: int = 0):
    deal = intake.get_deal(deal_id)
    if deal is None:
        raise HTTPException(404, f"Deal {deal_id} not found")
    return templates.TemplateResponse(
        request, "deal.html",
        {"deal": deal,
         "section8_options": list(intake.VALID_SECTION8),
         "saved": bool(saved)},
    )


# ── T-601: per-deal editable inputs ────────────────────────────
# Every input compute.py touches is bound here. Empty fields are stored as
# NULL so the Deal dataclass reverts to its dataclass default and the
# resolver chain (HUD FMR, WPRDC, overrides table) decides the value at
# analyze time. Inputs are EDITED, not auto-analyzed; user clicks
# "Run analysis" on the deal page when ready.

def _form_float(raw: Optional[str]) -> Optional[float]:
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _form_int(raw: Optional[str]) -> Optional[int]:
    if raw is None or raw == "":
        return None
    try:
        return int(float(raw))  # tolerate "3.0" from number inputs
    except (TypeError, ValueError):
        return None


@app.post("/deals/{deal_id}/edit")
def deal_edit(
    deal_id: str,
    address: str = Form(...),
    arv_base: Optional[str] = Form(None),
    rehab_budget: Optional[str] = Form(None),
    section8_status: str = Form("assumed"),
    purchase_price: Optional[str] = Form(None),
    beds_override: Optional[str] = Form(None),
    baths_override: Optional[str] = Form(None),
    sqft_override: Optional[str] = Form(None),
    annual_tax_override: Optional[str] = Form(None),
    prior_year_tax: Optional[str] = Form(None),
    monthly_rent_override: Optional[str] = Form(None),
    insurance_annual: Optional[str] = Form(None),
    owner_utilities_annual: Optional[str] = Form(None),
    hoa_annual: Optional[str] = Form(None),
    hold_period_years: Optional[str] = Form(None),
    exit_cap_rate: Optional[str] = Form(None),
    down_payment_pct: Optional[str] = Form(None),
    interest_rate: Optional[str] = Form(None),
    refi_year: Optional[str] = Form(None),
    refi_ltv: Optional[str] = Form(None),
    notes: str = Form(""),
):
    if intake.get_deal(deal_id) is None:
        raise HTTPException(404, f"Deal {deal_id} not found")
    if section8_status not in intake.VALID_SECTION8:
        raise HTTPException(400, f"Invalid section8_status: {section8_status}")

    fields = {
        "address": address,
        "section8_status": section8_status,
        "notes": notes,
        "arv_base":                _form_float(arv_base),
        "rehab_budget":            _form_float(rehab_budget),
        "purchase_price":          _form_float(purchase_price),
        "beds_override":           _form_int(beds_override),
        "baths_override":          _form_float(baths_override),
        "sqft_override":           _form_int(sqft_override),
        "annual_tax_override":     _form_float(annual_tax_override),
        "prior_year_tax":          _form_float(prior_year_tax),
        "monthly_rent_override":   _form_float(monthly_rent_override),
        "insurance_annual":        _form_float(insurance_annual),
        "owner_utilities_annual":  _form_float(owner_utilities_annual),
        "hoa_annual":              _form_float(hoa_annual),
        "hold_period_years":       _form_int(hold_period_years),
        "exit_cap_rate":           _form_float(exit_cap_rate),
        "down_payment_pct":        _form_float(down_payment_pct),
        "interest_rate":           _form_float(interest_rate),
        "refi_year":               _form_int(refi_year),
        "refi_ltv":                _form_float(refi_ltv),
    }
    intake.update_deal(deal_id, **fields)
    return RedirectResponse(
        url=f"/deals/{deal_id}?saved=1", status_code=303,
    )


@app.post("/deals/{deal_id}/analyze")
def deal_analyze(deal_id: str):
    deal = intake.get_deal(deal_id)
    if deal is None:
        raise HTTPException(404, f"Deal {deal_id} not found")
    if not deal.is_ready:
        # Render a diagnostic view rather than running the engine; the
        # underwriting endpoint also enforces the diagnostic guard, but a
        # ready-check here saves a useless run.
        return RedirectResponse(url=f"/deals/{deal_id}?missing=1", status_code=303)

    out = _run_intake_deal(deal, output_dir=None)
    run_id = out.get("run_id") or ""
    return RedirectResponse(url=f"/results/{deal_id}/{run_id}", status_code=303)


def _resolve_packet_dir(deal, run_id: str) -> Path:
    """Locate the packet folder for a given run_id under the deal's slug.

    T-604: any historical run is reachable, not just the latest. The slug
    folder is the parent of last_packet_path (the only way we know the slug
    for a deal that's been analyzed). If the requested run_id matches the
    slug-folder name we return it, otherwise 404.

    Security: run_id must not contain path separators or traversal.
    """
    if "/" in run_id or "\\" in run_id or ".." in run_id:
        raise HTTPException(400, "Invalid run_id")
    if not deal.last_packet_path:
        raise HTTPException(404, "No packets for this deal yet")
    slug_dir = Path(deal.last_packet_path).parent
    target = (slug_dir / run_id).resolve()
    # Re-verify the target stays inside the slug dir
    try:
        target.relative_to(slug_dir.resolve())
    except ValueError:
        raise HTTPException(400, "Path escapes packet folder")
    if not target.exists() or not target.is_dir():
        raise HTTPException(404, f"Run not found: {run_id}")
    return target


def _render_result(request: Request, deal, packet: Path, run_id: str):
    """Shared render for /results/... and /deals/.../runs/..."""
    sources_files = list(packet.glob("*_sources.json"))
    report_files = list(packet.glob("*_report.md"))
    excel_files = list(packet.glob("*_proforma.xlsx"))
    sources = {}
    report_md = ""
    import json as _json
    if sources_files:
        sources = _json.loads(sources_files[0].read_text(encoding="utf-8"))
    if report_files:
        report_md = report_files[0].read_text(encoding="utf-8")
    diagnostic = len(sources.get("blockers") or []) > 0
    report_html = _markdown.markdown(
        report_md, extensions=["tables", "fenced_code"]
    ) if report_md else ""
    return templates.TemplateResponse(
        request,
        "result.html",
        {"deal": deal, "sources": sources,
         "report_md": report_md, "report_html": report_html,
         "diagnostic_only": diagnostic,
         "packet_dir": str(packet),
         "excel_name": excel_files[0].name if excel_files else None,
         "report_name": report_files[0].name if report_files else None,
         "sources_name": sources_files[0].name if sources_files else None,
         "run_id": run_id},
    )


@app.get("/results/{deal_id}/{run_id}", response_class=HTMLResponse)
def result_view(request: Request, deal_id: str, run_id: str):
    """Latest-run convention preserved. Same view as /deals/{id}/runs/{run_id}
    but reached via the redirect from POST /deals/{id}/analyze."""
    deal = intake.get_deal(deal_id)
    if deal is None:
        raise HTTPException(404, f"Deal {deal_id} not found")
    packet = _resolve_packet_dir(deal, run_id)
    return _render_result(request, deal, packet, run_id)


@app.get("/deals/{deal_id}/runs/{run_id}", response_class=HTMLResponse)
def deal_run_view(request: Request, deal_id: str, run_id: str):
    """T-604: open ANY historical run, not just the latest. The history
    page links here for every past packet under <slug>/."""
    deal = intake.get_deal(deal_id)
    if deal is None:
        raise HTTPException(404, f"Deal {deal_id} not found")
    packet = _resolve_packet_dir(deal, run_id)
    return _render_result(request, deal, packet, run_id)


# ── T-611: Property_Overrides web CRUD ─────────────────────────
# Joe edits overrides through the browser. The DB is authoritative;
# the xlsx becomes a download-as-backup link.

@app.get("/overrides", response_class=HTMLResponse)
def overrides_list(request: Request, saved: int = 0, deleted: int = 0):
    rows = intake.list_overrides()
    return templates.TemplateResponse(
        request,
        "overrides.html",
        {"rows": rows, "saved": bool(saved), "deleted": bool(deleted)},
    )


@app.post("/overrides")
def overrides_upsert(
    address_normalized: str = Form(...),
    beds: Optional[str] = Form(None),
    baths: Optional[str] = Form(None),
    annual_tax: Optional[str] = Form(None),
    prior_year_tax: Optional[str] = Form(None),
    market_rent: Optional[str] = Form(None),
    notes: str = Form(""),
):
    addr = (address_normalized or "").strip().lower()
    if not addr:
        raise HTTPException(400, "address_normalized is required")
    intake.upsert_override(
        address_normalized=addr,
        beds=_form_int(beds),
        baths=_form_float(baths),
        annual_tax=_form_float(annual_tax),
        prior_year_tax=_form_float(prior_year_tax),
        market_rent=_form_float(market_rent),
        notes=notes,
    )
    return RedirectResponse(url="/overrides?saved=1", status_code=303)


@app.post("/overrides/delete")
def overrides_delete(address_normalized: str = Form(...)):
    intake.delete_override(address_normalized)
    return RedirectResponse(url="/overrides?deleted=1", status_code=303)


@app.get("/deals/{deal_id}/history", response_class=HTMLResponse)
def deal_history(request: Request, deal_id: str):
    """T-603: every timestamped run under <slug>/, newest first.

    Each row carries the run timestamp, parsed verdict from sources.json
    (or DIAGNOSTIC ONLY when blockers existed), and links to the per-run
    result page. Empty list when the deal has never been analyzed.
    """
    deal = intake.get_deal(deal_id)
    if deal is None:
        raise HTTPException(404, f"Deal {deal_id} not found")
    runs: list[dict] = []
    if deal.last_packet_path:
        slug_dir = Path(deal.last_packet_path).parent
        if slug_dir.exists():
            import json as _json
            for run_dir in sorted(
                (p for p in slug_dir.iterdir() if p.is_dir()),
                key=lambda p: p.name, reverse=True,
            ):
                sources_files = list(run_dir.glob("*_sources.json"))
                if not sources_files:
                    continue
                try:
                    src = _json.loads(sources_files[0].read_text(encoding="utf-8"))
                except Exception:
                    continue
                diagnostic = bool(src.get("blockers"))
                verdict_blk = src.get("verdict") or {}
                metrics = verdict_blk.get("metrics") or {}
                runs.append({
                    "run_id": run_dir.name,
                    "run_at": src.get("run_at"),
                    "diagnostic_only": diagnostic,
                    "blockers": src.get("blockers") or [],
                    "recommendation": verdict_blk.get("recommendation"),
                    "metrics": metrics,
                    "rentcast_mode": src.get("rentcast_mode"),
                    "is_latest": (run_dir.name in (deal.last_analyzed_run_id or "")),
                })
    return templates.TemplateResponse(
        request, "history.html",
        {"deal": deal, "runs": runs},
    )


@app.get("/packets/{deal_id}", response_class=HTMLResponse)
def packet_view(request: Request, deal_id: str):
    """Static download links for the latest packet of a deal."""
    deal = intake.get_deal(deal_id)
    if deal is None or not deal.last_packet_path:
        raise HTTPException(404, "No packet for this deal yet")
    packet = Path(deal.last_packet_path)
    if not packet.exists():
        raise HTTPException(404, f"Packet folder missing: {packet}")
    files = sorted(p for p in packet.glob("*") if p.is_file())
    sources_files = [p for p in files if p.name.endswith("_sources.json")]
    diagnostic = False
    if sources_files:
        try:
            import json as _json
            sources = _json.loads(sources_files[0].read_text(encoding="utf-8"))
            diagnostic = len(sources.get("blockers") or []) > 0
        except Exception:
            pass
    return templates.TemplateResponse(
        request,
        "packet.html",
        {"deal": deal, "files": files,
         "packet_dir": str(packet),
         "diagnostic_only": diagnostic},
    )


@app.get("/deals/{deal_id}/runs/{run_id}/files/{filename}")
def packet_run_file(deal_id: str, run_id: str, filename: str):
    """T-604: download a file from any historical run's packet folder.

    Same security guards as /files/{deal_id}/{filename}: filename must
    not contain separators or traversal; resolved path must stay inside
    the run's packet folder.
    """
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(400, "Invalid filename")
    deal = intake.get_deal(deal_id)
    if deal is None:
        raise HTTPException(404, f"Deal {deal_id} not found")
    packet = _resolve_packet_dir(deal, run_id)
    target = (packet / filename).resolve()
    try:
        target.relative_to(packet.resolve())
    except ValueError:
        raise HTTPException(400, "Path escapes packet folder")
    if not target.exists() or not target.is_file():
        raise HTTPException(404, f"File not found: {filename}")
    return FileResponse(str(target), filename=filename)


@app.get("/files/{deal_id}/{filename}")
def packet_file(deal_id: str, filename: str):
    """Download an individual file from a deal's latest packet folder.

    Security: filename must not contain path separators. The file MUST be
    inside the deal's packet folder. Any escape attempt returns 404.
    """
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(400, "Invalid filename")
    deal = intake.get_deal(deal_id)
    if deal is None or not deal.last_packet_path:
        raise HTTPException(404, "No packet for this deal yet")
    packet = Path(deal.last_packet_path).resolve()
    target = (packet / filename).resolve()
    # Reject anything that resolves outside the packet folder
    try:
        target.relative_to(packet)
    except ValueError:
        raise HTTPException(400, "Path escapes packet folder")
    if not target.exists() or not target.is_file():
        raise HTTPException(404, f"File not found: {filename}")
    return FileResponse(str(target), filename=filename)

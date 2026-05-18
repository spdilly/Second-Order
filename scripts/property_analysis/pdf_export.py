"""
PDF export for the deal memo (T-614).

Pipeline: markdown report -> styled HTML -> Chromium print-to-PDF.

The PDF is cached alongside the other packet artifacts so a re-download
does not re-render. Cache key is the run folder + the report file's mtime;
if the markdown is updated post-hoc the PDF will rebuild on next request.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import markdown as _markdown

# ──────────────────────────────────────────────
# Print-friendly HTML wrapper
# ──────────────────────────────────────────────

_CSS = """
@page {
  size: Letter;
  margin: 0.6in 0.7in 0.6in 0.7in;
  @top-left { content: ""; }
  @bottom-right { content: "Page " counter(page) " of " counter(pages); font-size: 9pt; color: #888; }
  @bottom-left { content: "Sean Dillard | seandillard.com"; font-size: 9pt; color: #888; }
}
body {
  font-family: "Aptos", "Calibri", "Segoe UI", -apple-system, sans-serif;
  font-size: 10.5pt;
  color: #141414;
  line-height: 1.45;
  margin: 0;
}
h1 {
  font-size: 16pt;
  margin: 0 0 4pt 0;
  color: #141414;
  border-bottom: 1.5pt solid #141414;
  padding-bottom: 4pt;
}
h2 {
  font-size: 12pt;
  margin: 14pt 0 4pt 0;
  color: #141414;
  border-bottom: 0.5pt solid #d6d4cf;
  padding-bottom: 2pt;
  page-break-after: avoid;
}
h3 {
  font-size: 10pt;
  margin: 10pt 0 3pt 0;
  color: #3d3d3d;
  text-transform: uppercase;
  letter-spacing: 0.4px;
  page-break-after: avoid;
}
p { margin: 4pt 0; }
blockquote {
  border-left: 2pt solid #2a7c7c;
  background: #f6f9f9;
  padding: 6pt 10pt;
  margin: 8pt 0;
  font-size: 9.5pt;
  color: #3d3d3d;
}
table {
  width: 100%;
  border-collapse: collapse;
  margin: 6pt 0;
  font-size: 9.5pt;
  page-break-inside: avoid;
}
th, td {
  text-align: left;
  padding: 3pt 6pt;
  border-bottom: 0.5pt solid #d6d4cf;
}
th {
  background: #f0efeb;
  font-weight: 600;
  border-bottom: 1pt solid #141414;
}
td:has(*), th:has(*) { /* nothing — placeholder */ }
strong { color: #141414; }
code, pre {
  font-family: "Source Code Pro", "Consolas", monospace;
  font-size: 9pt;
}
hr { border: none; border-top: 0.5pt solid #d6d4cf; margin: 10pt 0; }
ul, ol { margin: 4pt 0 4pt 18pt; padding: 0; }
li { margin: 1pt 0; }
.right { text-align: right; }
"""


def _wrap_html(body_html: str, title: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>{title}</title>
<style>{_CSS}</style>
</head>
<body>
{body_html}
</body>
</html>"""


# ──────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────

def render_report_to_pdf(
    report_md_path: Path,
    output_pdf_path: Optional[Path] = None,
    title: Optional[str] = None,
) -> Path:
    """Render a markdown report to a print-ready PDF via Chromium.

    Args:
        report_md_path: path to <slug>_report.md
        output_pdf_path: where to write the PDF. Defaults to the same folder
            with .pdf extension.
        title: PDF document title metadata.

    Returns the path to the generated PDF.
    """
    report_md_path = Path(report_md_path)
    if not report_md_path.exists():
        raise FileNotFoundError(f"Report markdown missing: {report_md_path}")

    if output_pdf_path is None:
        output_pdf_path = report_md_path.with_suffix(".pdf")
    output_pdf_path = Path(output_pdf_path)
    output_pdf_path.parent.mkdir(parents=True, exist_ok=True)

    # Cache check: if the PDF already exists and is newer than the markdown,
    # skip the re-render.
    if (output_pdf_path.exists() and
            output_pdf_path.stat().st_mtime >= report_md_path.stat().st_mtime):
        return output_pdf_path

    md_text = report_md_path.read_text(encoding="utf-8")
    body_html = _markdown.markdown(md_text, extensions=["tables", "fenced_code"])
    doc_title = title or report_md_path.stem
    full_html = _wrap_html(body_html, doc_title)

    # Late import: Playwright is optional. If it's not installed, raise a
    # clear error rather than crashing on import at module load.
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise RuntimeError(
            "PDF export requires Playwright. Install with: "
            "`pip install playwright && playwright install chromium`"
        ) from e

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.set_content(full_html, wait_until="domcontentloaded")
            page.pdf(
                path=str(output_pdf_path),
                format="Letter",
                print_background=True,
                margin={"top": "0.6in", "bottom": "0.6in",
                        "left": "0.7in", "right": "0.7in"},
                display_header_footer=False,
            )
        finally:
            browser.close()

    return output_pdf_path

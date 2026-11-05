"""Render a Markdown report to a print-quality PDF with headless Chrome.

Usage:
    uv run --with markdown python scripts/build_pdf.py docs/report/HINDSIGHT_2PAGE.md

Writes the PDF next to the Markdown file. Figures referenced as relative image paths (the SVGs
in docs/figures/) are embedded inline, so the PDF is self-contained. Needs Google Chrome.
"""

from __future__ import annotations

import base64
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import markdown  # type: ignore[import-untyped]

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

CSS = """
@page { size: A4; margin: 12mm 14mm 12mm 14mm; }
html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body { font-family: "Charter", "Georgia", serif; font-size: 9pt; line-height: 1.3;
       color: #1a1a1a; margin: 0; }
h1 { font-family: "Helvetica Neue", Arial, sans-serif; font-size: 16pt; line-height: 1.2;
     margin: 0 0 4pt 0; }
h2 { font-family: "Helvetica Neue", Arial, sans-serif; font-size: 11pt; margin: 8pt 0 2pt;
     border-bottom: 0.6pt solid #bbb; padding-bottom: 1.5pt; }
h3 { font-family: "Helvetica Neue", Arial, sans-serif; font-size: 10pt; margin: 8pt 0 2pt; }
p { margin: 3pt 0 4pt; text-align: justify; hyphens: auto; }
ul, ol { margin: 2pt 0 4pt 0; padding-left: 15pt; }
li { margin: 1pt 0; }
hr { border: 0; border-top: 0.6pt solid #ccc; margin: 7pt 0; }
a { color: #0b5394; text-decoration: none; }
code { font-size: 8.5pt; }
table { border-collapse: collapse; width: 100%; margin: 4pt 0 6pt; font-size: 8.6pt;
        font-family: "Helvetica Neue", Arial, sans-serif; page-break-inside: avoid; }
th { border-bottom: 1pt solid #333; text-align: left; padding: 2pt 4pt; }
td { border-bottom: 0.4pt solid #ddd; padding: 2pt 4pt; }
img { width: 80%; display: block; margin: 2pt auto 0; page-break-inside: avoid; }
p:has(> img) { margin: 0; page-break-inside: avoid; }
h1 + p { font-family: "Helvetica Neue", Arial, sans-serif; font-size: 8.6pt; color: #555; }
h3:first-of-type { margin-top: 0; }
"""


def inline_images(html: str, base: Path) -> str:
    """Replace relative <img src> paths with data: URIs so the PDF needs no external files."""

    def sub(m: re.Match[str]) -> str:
        src = m.group(1)
        if re.match(r"^[a-z]+:", src):
            return m.group(0)
        path = (base / src).resolve()
        mime = "image/svg+xml" if path.suffix == ".svg" else f"image/{path.suffix.lstrip('.')}"
        data = base64.b64encode(path.read_bytes()).decode()
        return m.group(0).replace(src, f"data:{mime};base64,{data}")

    return re.sub(r'<img [^>]*src="([^"]+)"', sub, html)


def build(md_path: Path) -> Path:
    body = markdown.markdown(md_path.read_text(), extensions=["tables", "sane_lists"])
    body = inline_images(body, md_path.parent)
    title = md_path.stem
    html = (
        f'<!doctype html><html><head><meta charset="utf-8"><title>{title}</title>'
        f"<style>{CSS}</style></head><body>{body}</body></html>"
    )
    out = md_path.with_suffix(".pdf")
    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "report.html"
        page.write_text(html)
        subprocess.run(
            [
                CHROME,
                "--headless",
                "--disable-gpu",
                "--no-pdf-header-footer",
                f"--print-to-pdf={out.resolve()}",
                page.as_uri(),
            ],
            check=True,
            capture_output=True,
        )
    return out


if __name__ == "__main__":
    for arg in sys.argv[1:] or ["docs/report/HINDSIGHT_2PAGE.md"]:
        print(build(Path(arg)))

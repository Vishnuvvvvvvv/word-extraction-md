"""
markdown_renderer.py
────────────────────
Converts the semantic layer (tables + images with OCR/VLM data)
into a rich Markdown appendix that is appended to Docling's own
base Markdown export.
"""
from pathlib import Path
from typing import Any


def _render_table_md(table: dict[str, Any], idx: int) -> list[str]:
    lines: list[str] = []
    caption = table.get("caption")
    rows = table.get("num_rows", "?")
    cols = table.get("num_cols", "?")
    lines.append(f"\n### Table {idx + 1}  ({rows}×{cols})")
    if caption:
        lines.append(f"**Caption:** {caption}")

    records = table.get("flattened_records")
    if records:
        # Header row from keys of first record
        headers = list(records[0].keys())
        lines.append("| " + " | ".join(str(h) for h in headers) + " |")
        lines.append("|" + "|".join(["---"] * len(headers)) + "|")
        for row in records:
            lines.append(
                "| "
                + " | ".join(str(row.get(h, "")) for h in headers)
                + " |"
            )
    return lines


def _render_image_md(rec: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    fname = Path(rec["file"]).name
    lines.append(f"\n### Image {rec['picture_index']}  (`{fname}`)")
    if rec.get("caption"):
        lines.append(f"**Caption:** {rec['caption']}")
    dim = f"{rec.get('width', '?')}×{rec.get('height', '?')} px"
    lines.append(f"**Dimensions:** {dim}")
    if rec.get("ocr_text"):
        lines.append(f"\n**OCR text:**\n```\n{rec['ocr_text']}\n```")
    sem = rec.get("semantic") or {}
    ct = sem.get("chart_type", "")
    if ct and ct not in ("not_evaluated", "not_a_chart", "unavailable", "error", ""):
        lines.append(f"\n**Chart type:** `{ct}`")
        if sem.get("summary"):
            lines.append(f"**Summary:** {sem['summary']}")
        series = sem.get("series", [])
        if series:
            lines.append("\n**Data series:**")
            for pt in series:
                lines.append(f"- {pt.get('label', '')}: {pt.get('value', '')}")
    return lines


def render_markdown(
    base_markdown: str,
    tables: list[dict[str, Any]],
    images: list[dict[str, Any]],
) -> str:
    """
    Returns the full Markdown string:
      1. Docling's base Markdown (structure, headings, inline tables)
      2. Extracted Tables appendix (with Markdown table rendering)
      3. Extracted Images & Charts appendix (OCR + VLM data)
    """
    parts = [base_markdown]

    if tables:
        parts.append("\n\n---\n## Extracted Tables\n")
        for i, tbl in enumerate(tables):
            parts.extend(_render_table_md(tbl, i))

    if images:
        parts.append("\n\n---\n## Extracted Image & Chart Data\n")
        for rec in images:
            parts.extend(_render_image_md(rec))

    return "\n".join(parts)

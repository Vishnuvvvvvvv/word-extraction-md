"""
table_extractor.py
──────────────────
Extracts tables from a Docling document object.
Preserves both a convenient flattened dataframe view and the
raw cell grid (row/col spans) for true merged-cell fidelity.
"""
import logging
from typing import Any

log = logging.getLogger("docx_pipeline.table_extractor")


def _safe(val) -> str | int | float | None:
    """Coerce a cell value to a JSON-safe primitive."""
    if val is None:
        return None
    if isinstance(val, (str, int, float, bool)):
        return val
    return str(val)


def extract_tables(doc) -> list[dict[str, Any]]:
    """
    Returns a list of table dicts, one per table found in *doc*.

    Each dict has:
      table_index        - zero-based index
      caption            - table caption string (or None)
      page               - page number (or None for DOCX)
      num_rows           - row count
      num_cols           - column count
      flattened_records  - list[dict] from pandas (convenient, lossy on merges)
      raw_cells          - list[dict] preserving row/col spans (lossless)
    """
    tables_out: list[dict[str, Any]] = []

    for i, table in enumerate(doc.tables):
        # ── Flattened dataframe (easy to consume but lossy on merges) ────────
        records = None
        try:
            # Pass `doc` to silence the deprecation warning in newer docling versions
            try:
                df = table.export_to_dataframe(doc)
            except TypeError:
                df = table.export_to_dataframe()
            # Coerce all values to JSON-safe primitives
            records = [
                {k: _safe(v) for k, v in row.items()}
                for row in df.to_dict(orient="records")
            ]
        except Exception as exc:
            log.warning("Table %d dataframe export failed: %s", i, exc)

        # ── Raw cell grid (source of truth for merged cells) ─────────────────
        raw_cells: list[dict[str, Any]] = []
        try:
            for cell in table.data.table_cells:
                raw_cells.append(
                    {
                        "text": cell.text,
                        "row": cell.start_row_offset_idx,
                        "col": cell.start_col_offset_idx,
                        "row_span": cell.row_span,
                        "col_span": cell.col_span,
                        "is_header": getattr(cell, "column_header", False),
                    }
                )
        except Exception as exc:
            log.warning("Table %d raw cell export failed: %s", i, exc)

        # ── Caption & provenance ─────────────────────────────────────────────
        caption = None
        try:
            if getattr(table, "captions", None):
                caption = table.caption_text(doc)
        except Exception:
            pass

        prov = table.prov[0] if getattr(table, "prov", None) else None

        tables_out.append(
            {
                "table_index": i,
                "caption": caption,
                "page": getattr(prov, "page_no", None),
                "num_rows": table.data.num_rows if table.data else None,
                "num_cols": table.data.num_cols if table.data else None,
                "flattened_records": records,
                "raw_cells": raw_cells,
            }
        )

    return tables_out

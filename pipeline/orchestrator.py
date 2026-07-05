"""
orchestrator.py
───────────────
Top-level pipeline orchestration: one function per document.
Wires together Docling parsing → OCR → Bedrock VLM → JSON + Markdown output.
"""
import json
import logging
from pathlib import Path
from typing import Any

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PaginatedPipelineOptions
from docling.document_converter import DocumentConverter, WordFormatOption

from pipeline.config import PipelineConfig
from pipeline.image_extractor import extract_images
from pipeline.markdown_renderer import render_markdown
from pipeline.table_extractor import extract_tables

log = logging.getLogger("docx_pipeline.orchestrator")


class _SafeEncoder(json.JSONEncoder):
    """Fallback encoder: converts any non-serialisable object to str."""
    def default(self, obj):
        try:
            return super().default(obj)
        except TypeError:
            return str(obj)


def build_converter(cfg: PipelineConfig) -> DocumentConverter:
    """Build a Docling DocumentConverter configured for DOCX files."""
    try:
        pipeline_opts = PaginatedPipelineOptions()
        pipeline_opts.generate_page_images = True
        pipeline_opts.images_scale = cfg.images_scale

        return DocumentConverter(
            format_options={
                InputFormat.DOCX: WordFormatOption(
                    pipeline_options=pipeline_opts
                ),
            }
        )
    except Exception:
        # Older / newer docling versions may not support PaginatedPipelineOptions
        log.warning(
            "Could not configure PaginatedPipelineOptions — "
            "falling back to default DocumentConverter."
        )
        return DocumentConverter()


def process_document(
    docx_path: Path,
    cfg: PipelineConfig,
    converter: DocumentConverter,
) -> dict[str, Any]:
    """
    Process a single .docx file through the full pipeline.

    Returns a result dict:
      {
        "success": bool,
        "document": filename,
        "markdown": str,           # full rendered markdown
        "dom_json_path": str,
        "semantic_json_path": str,
        "markdown_path": str,
        "tables_count": int,
        "images_count": int,
        "error": str | None,
      }
    """
    name = docx_path.stem
    doc_out_dir = cfg.output_dir / name
    doc_out_dir.mkdir(parents=True, exist_ok=True)

    log.info("▶ Processing: %s", docx_path.name)

    # ── Docling structural parse ─────────────────────────────────────────────
    try:
        result = converter.convert(str(docx_path))
    except Exception as exc:
        log.error("Docling failed for '%s': %s", docx_path.name, exc)
        return {
            "success": False,
            "document": docx_path.name,
            "error": str(exc),
            "markdown": "",
            "tables_count": 0,
            "images_count": 0,
        }

    doc = result.document

    # ── Layer A: near-lossless DOM JSON ──────────────────────────────────────
    dom_path = doc_out_dir / f"{name}.dom.json"
    try:
        dom_dict = doc.export_to_dict()
        dom_path.write_text(
            json.dumps(dom_dict, indent=2, ensure_ascii=False, cls=_SafeEncoder), encoding="utf-8"
        )
    except Exception as exc:
        log.warning("DOM export failed: %s", exc)
        dom_dict = {}

    # ── Extract tables ───────────────────────────────────────────────────────
    tables = extract_tables(doc)

    # ── Extract images (OCR + optional Bedrock VLM) ─────────────────────────
    images = extract_images(doc, doc_out_dir / "images", cfg)

    # ── Layer B: semantic JSON ───────────────────────────────────────────────
    semantic_path = doc_out_dir / f"{name}.semantic.json"
    semantic: dict[str, Any] = {
        "document": docx_path.name,
        "schema_version": "1.0",
        "num_pages": getattr(doc, "num_pages", None),
        "text_blocks_count": len(doc.texts),
        "tables": tables,
        "images": images,
    }
    semantic_path.write_text(
        json.dumps(semantic, indent=2, ensure_ascii=False, cls=_SafeEncoder), encoding="utf-8"
    )

    # ── Markdown ─────────────────────────────────────────────────────────────
    try:
        base_md = doc.export_to_markdown()
    except Exception as exc:
        log.warning("Markdown export failed: %s", exc)
        base_md = f"*Markdown export failed: {exc}*"

    full_md = render_markdown(base_md, tables, images)
    md_path = doc_out_dir / f"{name}.md"
    md_path.write_text(full_md, encoding="utf-8")

    log.info(
        "✔ Done: %s  (tables=%d, images=%d)",
        docx_path.name,
        len(tables),
        len(images),
    )

    return {
        "success": True,
        "document": docx_path.name,
        "error": None,
        "markdown": full_md,
        "dom_json_path": str(dom_path),
        "semantic_json_path": str(semantic_path),
        "markdown_path": str(md_path),
        "tables_count": len(tables),
        "images_count": len(images),
    }

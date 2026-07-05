"""
image_extractor.py
──────────────────
Extracts every embedded image/picture from a Docling document,
runs local OCR on it, and (optionally) calls AWS Bedrock for
chart-semantic extraction on images that look chart-like.
"""
import logging
from pathlib import Path
from typing import Any

from PIL import Image

from pipeline.bedrock_vlm import describe_chart, looks_like_chart
from pipeline.config import PipelineConfig
from pipeline.ocr import run_ocr

log = logging.getLogger("docx_pipeline.image_extractor")

_NOT_EVALUATED = {"chart_type": "not_evaluated", "series": [], "summary": ""}


def _get_picture_image(picture, doc) -> Image.Image | None:
    """
    Try multiple docling API shapes to retrieve the PIL image for a picture.
    Docling's API has shifted across versions; this wrapper handles both.
    """
    # Preferred: newer docling API
    try:
        img = picture.get_image(doc)
        if img is not None:
            return img
    except Exception:
        pass

    # Fallback: image stored directly on the node
    try:
        img = getattr(picture, "image", None)
        if img is not None:
            if isinstance(img, Image.Image):
                return img
            pil = getattr(img, "pil_image", None)
            if pil is not None:
                return pil
    except Exception:
        pass

    return None


def extract_images(
    doc, image_dir: Path, cfg: PipelineConfig
) -> list[dict[str, Any]]:
    """
    Returns a list of image dicts, one per embedded picture in *doc*.

    Each dict has:
      picture_index  - zero-based index
      file           - absolute path to the saved PNG
      caption        - picture caption (or None)
      page           - page number (or None for DOCX)
      bbox           - bounding box {l, t, r, b} (or None)
      width / height - pixel dimensions
      ocr_text       - Tesseract verbatim text (always local)
      semantic       - Bedrock chart description (or not_evaluated sentinel)
      resolution_method - always "docling_dom"
    """
    image_dir.mkdir(parents=True, exist_ok=True)
    images_out: list[dict[str, Any]] = []

    for i, picture in enumerate(doc.pictures):
        img = _get_picture_image(picture, doc)
        if img is None:
            log.info("Picture %d: no image data available — skipping.", i)
            continue

        # ── Save PNG ──────────────────────────────────────────────────────────
        img_path = image_dir / f"picture_{i}.png"
        try:
            img.save(img_path)
        except Exception as exc:
            log.warning("Could not save picture %d: %s", i, exc)
            continue

        # ── Caption & provenance ─────────────────────────────────────────────
        caption = None
        try:
            if getattr(picture, "captions", None):
                caption = picture.caption_text(doc)
        except Exception:
            pass

        prov = picture.prov[0] if getattr(picture, "prov", None) else None
        bbox_obj = getattr(prov, "bbox", None)
        bbox = (
            {
                "l": bbox_obj.l,
                "t": bbox_obj.t,
                "r": bbox_obj.r,
                "b": bbox_obj.b,
            }
            if bbox_obj
            else None
        )

        # ── OCR (always local, always free) ──────────────────────────────────
        ocr_text = run_ocr(img, cfg)

        # ── Chart semantics (Bedrock, only when heuristic fires) ─────────────
        if cfg.use_vlm and looks_like_chart(img, ocr_text, cfg):
            log.info(
                "Picture %d looks chart-like (%dx%d, %d OCR tokens) — "
                "calling Bedrock %s",
                i,
                img.width,
                img.height,
                len(ocr_text.split()),
                cfg.bedrock_model_id,
            )
            semantic = describe_chart(img_path, cfg)
        else:
            semantic = _NOT_EVALUATED.copy()

        images_out.append(
            {
                "picture_index": i,
                "file": str(img_path),
                "caption": caption,
                "page": getattr(prov, "page_no", None),
                "bbox": bbox,
                "width": img.width,
                "height": img.height,
                "ocr_text": ocr_text,
                "semantic": semantic,
                "resolution_method": "docling_dom",
            }
        )

    return images_out

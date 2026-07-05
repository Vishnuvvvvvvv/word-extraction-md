"""
ocr.py
──────
OCR via Amazon Textract (AWS managed service).
No local binary installation required — uses the same AWS credentials
as the Bedrock VLM calls.

Textract's detect_document_text API accepts raw image bytes (PNG/JPEG)
up to 5 MB and returns LINE-level text blocks, which we join into a
single string — the same shape that the rest of the pipeline expects.

Pricing (as of 2025): ~$1.50 per 1,000 images.
Only images that pass the chart-detection heuristic get a Bedrock call
on top; all images always go through Textract for text extraction.

If use_textract is False in config, OCR is skipped entirely (returns '').
"""
import io
import logging
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from PIL import Image

from pipeline.config import PipelineConfig

log = logging.getLogger("docx_pipeline.ocr")


def _get_textract_client(cfg: PipelineConfig):
    kwargs: dict = {"region_name": cfg.aws_region}
    if cfg.aws_access_key_id and cfg.aws_secret_access_key:
        kwargs["aws_access_key_id"] = cfg.aws_access_key_id
        kwargs["aws_secret_access_key"] = cfg.aws_secret_access_key
    return boto3.client("textract", **kwargs)


def _image_to_bytes(img: Image.Image, max_bytes: int) -> bytes:
    """
    Convert a PIL image to PNG bytes.
    If the result exceeds max_bytes (Textract's 5 MB limit),
    re-encode as JPEG with progressive quality reduction.
    """
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    data = buf.getvalue()

    if len(data) <= max_bytes:
        return data

    # Fall back to JPEG compression
    for quality in (90, 75, 60, 45):
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=quality)
        data = buf.getvalue()
        if len(data) <= max_bytes:
            log.debug("Image recompressed to JPEG q=%d (%d bytes)", quality, len(data))
            return data

    log.warning(
        "Image could not be compressed below %d bytes — OCR may fail.", max_bytes
    )
    return data


def run_ocr(img: Image.Image, cfg: PipelineConfig) -> str:
    """
    Run Amazon Textract on a PIL image.
    Returns the extracted text (newline-joined lines), or '' on any failure.

    If cfg.use_textract is False, returns '' immediately (OCR disabled).
    """
    if not cfg.use_textract:
        return ""

    try:
        image_bytes = _image_to_bytes(img, cfg.textract_max_bytes)
        client = _get_textract_client(cfg)

        response = client.detect_document_text(
            Document={"Bytes": image_bytes}
        )

        lines = [
            block["Text"]
            for block in response.get("Blocks", [])
            if block.get("BlockType") == "LINE"
        ]
        return "\n".join(lines).strip()

    except (BotoCoreError, ClientError) as exc:
        log.warning(
            "Textract OCR failed: %s. "
            "Check that your IAM user has 'textract:DetectDocumentText' permission.",
            exc,
        )
        return ""
    except Exception as exc:
        log.warning("Unexpected OCR error: %s", exc)
        return ""

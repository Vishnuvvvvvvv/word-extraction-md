"""
bedrock_vlm.py
──────────────
Vision-language model calls via AWS Bedrock (amazon.nova-lite-v1:0).
Uses the Bedrock Converse API which has a unified interface for all models.

The chart-extraction prompt returns a structured JSON object:
  {
    "chart_type": str,
    "series": [{"label": str, "value": str}],
    "summary": str
  }
If the image is not a chart, chart_type is "not_a_chart".
"""
import io
import json
import logging
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from PIL import Image

from pipeline.config import PipelineConfig

log = logging.getLogger("docx_pipeline.bedrock_vlm")

CHART_PROMPT = (
    "You are analyzing an image extracted from an insurance document "
    "(policy wording, claim form, benefit illustration, or brochure). "
    "Determine whether the image is a chart, graph, diagram, or table of data. "
    "Return ONLY valid JSON — no prose, no markdown fences — matching this exact shape:\n"
    '{"chart_type": "<bar|line|pie|table|diagram|not_a_chart>", '
    '"series": [{"label": "<string>", "value": "<string>"}], '
    '"summary": "<one sentence describing the chart>"}\n'
    "If the image is a photo, logo, signature, or decorative element, "
    'return {"chart_type": "not_a_chart", "series": [], "summary": ""}.'
)

_EMPTY_RESULT = {"chart_type": "not_a_chart", "series": [], "summary": ""}
_ERROR_RESULT = {"chart_type": "error", "series": [], "summary": ""}
_UNAVAILABLE_RESULT = {"chart_type": "unavailable", "series": [], "summary": ""}


def _get_client(cfg: PipelineConfig):
    """Build a Bedrock Runtime client using explicit credentials from config."""
    kwargs: dict[str, Any] = {"region_name": cfg.aws_region}
    if cfg.aws_access_key_id and cfg.aws_secret_access_key:
        kwargs["aws_access_key_id"] = cfg.aws_access_key_id
        kwargs["aws_secret_access_key"] = cfg.aws_secret_access_key
    return boto3.client("bedrock-runtime", **kwargs)


def _image_to_bytes(image_path: Path) -> bytes:
    """Read and re-encode the image as PNG bytes for Bedrock."""
    with Image.open(image_path) as img:
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()


def looks_like_chart(img: Image.Image, ocr_text: str, cfg: PipelineConfig) -> bool:
    """
    Fast, local heuristic: should we pay for a Bedrock VLM call on this image?

    Criteria (tunable via config):
    - Image area must exceed min_chart_area (skips logos/icons)
    - OCR token count must be in [min_ocr_tokens, max_ocr_tokens]
      (charts have labels; photos have none; scanned text has too many)
    """
    area = img.width * img.height
    if area < cfg.min_chart_area:
        return False
    token_count = len(ocr_text.split())
    if token_count < cfg.min_ocr_tokens_for_chart:
        return False
    if token_count > cfg.max_ocr_tokens_for_chart:
        return False
    return True


def describe_chart(image_path: Path, cfg: PipelineConfig) -> dict[str, Any]:
    """
    Call AWS Bedrock Nova Lite with the chart image.
    Returns a structured dict with chart_type / series / summary.
    No data leaves your AWS account.
    """
    try:
        image_bytes = _image_to_bytes(image_path)
        client = _get_client(cfg)

        response = client.converse(
            modelId=cfg.bedrock_model_id,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "image": {
                                "format": "png",
                                "source": {"bytes": image_bytes},
                            }
                        },
                        {"text": CHART_PROMPT},
                    ],
                }
            ],
            inferenceConfig={
                "maxTokens": 512,
                "temperature": 0.1,
            },
        )

        raw_text = response["output"]["message"]["content"][0]["text"].strip()

        # Strip any accidental markdown fences
        if raw_text.startswith("```"):
            raw_text = raw_text.split("```")[1]
            if raw_text.startswith("json"):
                raw_text = raw_text[4:]
            raw_text = raw_text.strip()

        parsed = json.loads(raw_text)
        # Ensure required keys are present
        parsed.setdefault("chart_type", "unknown")
        parsed.setdefault("series", [])
        parsed.setdefault("summary", "")
        return parsed

    except (BotoCoreError, ClientError) as exc:
        log.error(
            "AWS Bedrock call failed for '%s': %s. "
            "Check your credentials and that model access is enabled in the console.",
            image_path.name,
            exc,
        )
        return {**_UNAVAILABLE_RESULT, "summary": str(exc)}
    except json.JSONDecodeError as exc:
        log.warning(
            "Bedrock returned non-JSON for '%s': %s", image_path.name, exc
        )
        return {**_ERROR_RESULT, "summary": f"JSON parse error: {exc}"}
    except Exception as exc:
        log.warning("VLM call failed for '%s': %s", image_path.name, exc)
        return {**_ERROR_RESULT, "summary": str(exc)}

"""
config.py
─────────
Central configuration dataclass for the DOCX pipeline.
All values are loaded from environment variables / .env file.

OCR: Amazon Textract (AWS managed) — no local binary required.
VLM: AWS Bedrock Nova Lite — for chart/diagram semantic extraction.
"""
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass
class PipelineConfig:
    # ── Output ──────────────────────────────────────────────────────────────
    output_dir: Path = field(
        default_factory=lambda: Path(os.getenv("OUTPUT_DIR", "./output"))
    )

    # ── VLM toggle ──────────────────────────────────────────────────────────
    use_vlm: bool = field(
        default_factory=lambda: os.getenv("USE_VLM", "true").lower() == "true"
    )

    # ── AWS Bedrock ─────────────────────────────────────────────────────────
    aws_region: str = field(
        default_factory=lambda: os.getenv("AWS_REGION", "us-east-1")
    )
    aws_access_key_id: str = field(
        default_factory=lambda: os.getenv("AWS_ACCESS_KEY_ID", "")
    )
    aws_secret_access_key: str = field(
        default_factory=lambda: os.getenv("AWS_SECRET_ACCESS_KEY", "")
    )
    bedrock_model_id: str = field(
        default_factory=lambda: os.getenv(
            "BEDROCK_MODEL_ID", "amazon.nova-lite-v1:0"
        )
    )
    bedrock_timeout_s: int = 60

    # ── Docling rendering quality ────────────────────────────────────────────
    images_scale: float = 2.0

    # ── Amazon Textract (OCR) ────────────────────────────────────────────────
    use_textract: bool = field(
        default_factory=lambda: os.getenv("USE_TEXTRACT", "true").lower() == "true"
    )
    # Max image bytes Textract accepts per call (5 MB hard limit).
    # Images larger than this will be JPEG-compressed before upload.
    textract_max_bytes: int = 5 * 1024 * 1024

    # ── Chart-detection heuristics ──────────────────────────────────────────
    min_chart_area: int = 40_000       # skip tiny icons / logos
    min_ocr_tokens_for_chart: int = 2  # must have at least 2 text tokens
    max_ocr_tokens_for_chart: int = 80 # too much text → scanned text block

    def __post_init__(self):
        self.output_dir = Path(self.output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

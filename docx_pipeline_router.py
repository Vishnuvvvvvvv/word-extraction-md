"""
docx_pipeline_router.py
───────────────────────
Self-contained FastAPI router — drop this single file into any FastAPI project.

USAGE in your existing main.py:
    from docx_pipeline_router import router as docx_router
    app.include_router(docx_router, prefix="/docx", tags=["DOCX Pipeline"])

ENDPOINTS (all under whatever prefix you choose):
    POST /upload              → upload .doc/.docx, returns job_id
    GET  /status/{job_id}     → poll processing status
    GET  /download/{job_id}/markdown   → download .md file
    GET  /download/{job_id}/semantic   → download semantic .json file

OUTPUT FOLDER:
    uploads/<file_stem>/
        <file_stem>.md
        <file_stem>.dom.json
        <file_stem>.semantic.json
        images/
            picture_0.png
            ...

REQUIRED ENV VARS (see .env.docx_pipeline):
    AWS_REGION, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY
    BEDROCK_MODEL_ID, USE_VLM, USE_TEXTRACT
    DOCX_OUTPUT_DIR   (default: ./uploads)
"""

from __future__ import annotations

import io
import json
import logging
import os
import shutil
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import load_dotenv
from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from PIL import Image
from pydantic import BaseModel

load_dotenv()

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("docx_pipeline")

# ── Config from environment ────────────────────────────────────────────────────
AWS_REGION             = os.getenv("AWS_REGION", "us-east-1")
AWS_ACCESS_KEY_ID      = os.getenv("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY  = os.getenv("AWS_SECRET_ACCESS_KEY", "")
BEDROCK_MODEL_ID       = os.getenv("BEDROCK_MODEL_ID", "amazon.nova-lite-v1:0")
USE_VLM                = os.getenv("USE_VLM", "true").lower() == "true"
USE_TEXTRACT           = os.getenv("USE_TEXTRACT", "true").lower() == "true"
OUTPUT_DIR             = Path(os.getenv("DOCX_OUTPUT_DIR", "./uploads"))
IMAGES_SCALE           = 2.0
TEXTRACT_MAX_BYTES     = 5 * 1024 * 1024
MIN_CHART_AREA         = 40_000
MIN_OCR_TOKENS         = 2
MAX_OCR_TOKENS         = 80
ALLOWED_EXT            = {".doc", ".docx"}
STAGING_DIR            = OUTPUT_DIR / "_staging"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
STAGING_DIR.mkdir(parents=True, exist_ok=True)

# ── In-memory job store ────────────────────────────────────────────────────────
_jobs: dict[str, dict[str, Any]] = {}
_executor = ThreadPoolExecutor(max_workers=2)

# ── Pydantic schemas ───────────────────────────────────────────────────────────
class UploadResponse(BaseModel):
    job_id: str
    status: str
    message: str

class JobStatus(BaseModel):
    job_id: str
    status: str
    document: Optional[str] = None
    tables_count: Optional[int] = None
    images_count: Optional[int] = None
    markdown_preview: Optional[str] = None
    markdown_path: Optional[str] = None
    semantic_json_path: Optional[str] = None
    dom_json_path: Optional[str] = None
    error: Optional[str] = None

# ── Router ─────────────────────────────────────────────────────────────────────
router = APIRouter()

# ══════════════════════════════════════════════════════════════════════════════
# AWS HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _aws_kwargs() -> dict:
    kw: dict[str, Any] = {"region_name": AWS_REGION}
    if AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY:
        kw["aws_access_key_id"] = AWS_ACCESS_KEY_ID
        kw["aws_secret_access_key"] = AWS_SECRET_ACCESS_KEY
    return kw


# ══════════════════════════════════════════════════════════════════════════════
# OCR — Amazon Textract
# ══════════════════════════════════════════════════════════════════════════════

def _img_to_bytes_for_textract(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    data = buf.getvalue()
    if len(data) <= TEXTRACT_MAX_BYTES:
        return data
    for q in (90, 75, 60, 45):
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=q)
        data = buf.getvalue()
        if len(data) <= TEXTRACT_MAX_BYTES:
            return data
    return data


def run_ocr(img: Image.Image) -> str:
    if not USE_TEXTRACT:
        return ""
    try:
        image_bytes = _img_to_bytes_for_textract(img)
        client = boto3.client("textract", **_aws_kwargs())
        response = client.detect_document_text(Document={"Bytes": image_bytes})
        lines = [
            b["Text"] for b in response.get("Blocks", [])
            if b.get("BlockType") == "LINE"
        ]
        return "\n".join(lines).strip()
    except (BotoCoreError, ClientError) as exc:
        log.warning("Textract OCR failed: %s", exc)
        return ""
    except Exception as exc:
        log.warning("OCR error: %s", exc)
        return ""


# ══════════════════════════════════════════════════════════════════════════════
# CHART DETECTION — AWS Bedrock Nova Lite
# ══════════════════════════════════════════════════════════════════════════════

_CHART_PROMPT = (
    "You are analyzing an image extracted from a business document. "
    "Determine whether the image is a chart, graph, diagram, or table of data. "
    "Return ONLY valid JSON — no prose, no markdown fences — matching this shape:\n"
    '{"chart_type": "<bar|line|pie|table|diagram|not_a_chart>", '
    '"series": [{"label": "<string>", "value": "<string>"}], '
    '"summary": "<one sentence describing the chart>"}\n'
    "If the image is a photo, logo, signature, or decorative element, "
    'return {"chart_type": "not_a_chart", "series": [], "summary": ""}.'
)

_NOT_EVALUATED = {"chart_type": "not_evaluated", "series": [], "summary": ""}
_ERROR_RESULT   = {"chart_type": "error",         "series": [], "summary": ""}


def looks_like_chart(img: Image.Image, ocr_text: str) -> bool:
    area = img.width * img.height
    tokens = len(ocr_text.split())
    return area > MIN_CHART_AREA and MIN_OCR_TOKENS <= tokens <= MAX_OCR_TOKENS


def describe_chart(image_path: Path) -> dict[str, Any]:
    try:
        with Image.open(image_path) as im:
            buf = io.BytesIO()
            im.save(buf, format="PNG")
            image_bytes = buf.getvalue()

        client = boto3.client("bedrock-runtime", **_aws_kwargs())
        response = client.converse(
            modelId=BEDROCK_MODEL_ID,
            messages=[{
                "role": "user",
                "content": [
                    {"image": {"format": "png", "source": {"bytes": image_bytes}}},
                    {"text": _CHART_PROMPT},
                ],
            }],
            inferenceConfig={"maxTokens": 512, "temperature": 0.1},
        )
        raw = response["output"]["message"]["content"][0]["text"].strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        parsed = json.loads(raw)
        parsed.setdefault("chart_type", "unknown")
        parsed.setdefault("series", [])
        parsed.setdefault("summary", "")
        return parsed
    except (BotoCoreError, ClientError) as exc:
        log.error("Bedrock call failed: %s", exc)
        return {**_ERROR_RESULT, "summary": str(exc)}
    except json.JSONDecodeError as exc:
        log.warning("Bedrock non-JSON response: %s", exc)
        return {**_ERROR_RESULT, "summary": f"JSON parse error: {exc}"}
    except Exception as exc:
        log.warning("VLM error: %s", exc)
        return {**_ERROR_RESULT, "summary": str(exc)}


# ══════════════════════════════════════════════════════════════════════════════
# TABLE EXTRACTOR
# ══════════════════════════════════════════════════════════════════════════════

def _safe(val: Any) -> Any:
    if val is None or isinstance(val, (str, int, float, bool)):
        return val
    return str(val)


def extract_tables(doc) -> list[dict[str, Any]]:
    out = []
    for i, table in enumerate(doc.tables):
        records = None
        try:
            try:
                df = table.export_to_dataframe(doc)
            except TypeError:
                df = table.export_to_dataframe()
            records = [{k: _safe(v) for k, v in row.items()} for row in df.to_dict(orient="records")]
        except Exception as exc:
            log.warning("Table %d dataframe export failed: %s", i, exc)

        raw_cells: list[dict] = []
        try:
            for cell in table.data.table_cells:
                raw_cells.append({
                    "text":     cell.text,
                    "row":      cell.start_row_offset_idx,
                    "col":      cell.start_col_offset_idx,
                    "row_span": cell.row_span,
                    "col_span": cell.col_span,
                    "is_header": getattr(cell, "column_header", False),
                })
        except Exception as exc:
            log.warning("Table %d raw cell export failed: %s", i, exc)

        caption = None
        try:
            if getattr(table, "captions", None):
                caption = table.caption_text(doc)
        except Exception:
            pass

        prov = table.prov[0] if getattr(table, "prov", None) else None
        out.append({
            "table_index":       i,
            "caption":           caption,
            "page":              getattr(prov, "page_no", None),
            "num_rows":          table.data.num_rows if table.data else None,
            "num_cols":          table.data.num_cols if table.data else None,
            "flattened_records": records,
            "raw_cells":         raw_cells,
        })
    return out


# ══════════════════════════════════════════════════════════════════════════════
# IMAGE EXTRACTOR
# ══════════════════════════════════════════════════════════════════════════════

def _get_pil_image(picture, doc) -> Optional[Image.Image]:
    try:
        img = picture.get_image(doc)
        if img is not None:
            return img
    except Exception:
        pass
    try:
        img = getattr(picture, "image", None)
        if isinstance(img, Image.Image):
            return img
        pil = getattr(img, "pil_image", None)
        if pil is not None:
            return pil
    except Exception:
        pass
    return None


def extract_images(doc, image_dir: Path) -> list[dict[str, Any]]:
    image_dir.mkdir(parents=True, exist_ok=True)
    out = []
    for i, picture in enumerate(doc.pictures):
        img = _get_pil_image(picture, doc)
        if img is None:
            log.info("Picture %d: no image data — skipping.", i)
            continue

        img_path = image_dir / f"picture_{i}.png"
        try:
            img.save(img_path)
        except Exception as exc:
            log.warning("Could not save picture %d: %s", i, exc)
            continue

        caption = None
        try:
            if getattr(picture, "captions", None):
                caption = picture.caption_text(doc)
        except Exception:
            pass

        prov = picture.prov[0] if getattr(picture, "prov", None) else None
        bbox_obj = getattr(prov, "bbox", None)
        bbox = ({"l": bbox_obj.l, "t": bbox_obj.t, "r": bbox_obj.r, "b": bbox_obj.b}
                if bbox_obj else None)

        ocr_text = run_ocr(img)

        if USE_VLM and looks_like_chart(img, ocr_text):
            log.info("Picture %d looks chart-like — calling Bedrock", i)
            semantic = describe_chart(img_path)
        else:
            semantic = _NOT_EVALUATED.copy()

        out.append({
            "picture_index":     i,
            "file":              str(img_path),
            "caption":           caption,
            "page":              getattr(prov, "page_no", None),
            "bbox":              bbox,
            "width":             img.width,
            "height":            img.height,
            "ocr_text":          ocr_text,
            "semantic":          semantic,
            "resolution_method": "docling_dom",
        })
    return out


# ══════════════════════════════════════════════════════════════════════════════
# MARKDOWN RENDERER
# ══════════════════════════════════════════════════════════════════════════════

def render_markdown(base_md: str, tables: list, images: list) -> str:
    parts = [base_md]

    if tables:
        parts.append("\n\n---\n## Extracted Tables\n")
        for i, tbl in enumerate(tables):
            rows = tbl.get("num_rows", "?")
            cols = tbl.get("num_cols", "?")
            parts.append(f"\n### Table {i + 1}  ({rows}×{cols})")
            if tbl.get("caption"):
                parts.append(f"**Caption:** {tbl['caption']}")
            records = tbl.get("flattened_records")
            if records:
                headers = list(records[0].keys())
                parts.append("| " + " | ".join(str(h) for h in headers) + " |")
                parts.append("|" + "|".join(["---"] * len(headers)) + "|")
                for row in records:
                    parts.append("| " + " | ".join(str(row.get(h, "")) for h in headers) + " |")

    if images:
        parts.append("\n\n---\n## Extracted Image & Chart Data\n")
        for rec in images:
            fname = Path(rec["file"]).name
            parts.append(f"\n### Image {rec['picture_index']}  (`{fname}`)")
            if rec.get("caption"):
                parts.append(f"**Caption:** {rec['caption']}")
            parts.append(f"**Dimensions:** {rec.get('width','?')}×{rec.get('height','?')} px")
            if rec.get("ocr_text"):
                parts.append(f"\n**OCR text:**\n```\n{rec['ocr_text']}\n```")
            sem = rec.get("semantic") or {}
            ct = sem.get("chart_type", "")
            if ct and ct not in ("not_evaluated", "not_a_chart", "unavailable", "error", ""):
                parts.append(f"\n**Chart type:** `{ct}`")
                if sem.get("summary"):
                    parts.append(f"**Summary:** {sem['summary']}")
                for pt in sem.get("series", []):
                    parts.append(f"- {pt.get('label','')}: {pt.get('value','')}")

    return "\n".join(parts)


# ══════════════════════════════════════════════════════════════════════════════
# JSON ENCODER
# ══════════════════════════════════════════════════════════════════════════════

class _SafeEncoder(json.JSONEncoder):
    def default(self, obj):
        try:
            return super().default(obj)
        except TypeError:
            return str(obj)


# ══════════════════════════════════════════════════════════════════════════════
# DOCLING CONVERTER (built once at module import)
# ══════════════════════════════════════════════════════════════════════════════

def _build_docling_converter():
    try:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PaginatedPipelineOptions
        from docling.document_converter import DocumentConverter, WordFormatOption

        opts = PaginatedPipelineOptions()
        opts.generate_page_images = True
        opts.images_scale = IMAGES_SCALE
        return DocumentConverter(
            format_options={InputFormat.DOCX: WordFormatOption(pipeline_options=opts)}
        )
    except Exception:
        log.warning("PaginatedPipelineOptions unavailable — using default DocumentConverter")
        from docling.document_converter import DocumentConverter
        return DocumentConverter()


_converter = _build_docling_converter()


# ══════════════════════════════════════════════════════════════════════════════
# CORE PIPELINE
# ══════════════════════════════════════════════════════════════════════════════

def _process_document(docx_path: Path) -> dict[str, Any]:
    """Run the full pipeline on a single .docx file."""
    name = docx_path.stem
    out_dir = OUTPUT_DIR / name
    out_dir.mkdir(parents=True, exist_ok=True)

    log.info("Processing: %s", docx_path.name)

    # ── Docling parse ──────────────────────────────────────────────────────────
    try:
        result = _converter.convert(str(docx_path))
    except Exception as exc:
        log.error("Docling failed: %s", exc)
        return {"success": False, "document": docx_path.name, "error": str(exc),
                "markdown": "", "tables_count": 0, "images_count": 0}

    doc = result.document

    # ── DOM JSON ───────────────────────────────────────────────────────────────
    dom_path = out_dir / f"{name}.dom.json"
    try:
        dom_path.write_text(
            json.dumps(doc.export_to_dict(), indent=2, ensure_ascii=False, cls=_SafeEncoder),
            encoding="utf-8",
        )
    except Exception as exc:
        log.warning("DOM export failed: %s", exc)

    # ── Tables + Images ────────────────────────────────────────────────────────
    tables = extract_tables(doc)
    images = extract_images(doc, out_dir / "images")

    # ── Semantic JSON ──────────────────────────────────────────────────────────
    semantic_path = out_dir / f"{name}.semantic.json"
    semantic: dict[str, Any] = {
        "document":          docx_path.name,
        "schema_version":    "1.0",
        "num_pages":         getattr(doc, "num_pages", None),
        "text_blocks_count": len(doc.texts),
        "tables":            tables,
        "images":            images,
    }
    semantic_path.write_text(
        json.dumps(semantic, indent=2, ensure_ascii=False, cls=_SafeEncoder),
        encoding="utf-8",
    )

    # ── Markdown ───────────────────────────────────────────────────────────────
    try:
        base_md = doc.export_to_markdown()
    except Exception as exc:
        base_md = f"*Markdown export failed: {exc}*"

    full_md = render_markdown(base_md, tables, images)
    md_path = out_dir / f"{name}.md"
    md_path.write_text(full_md, encoding="utf-8")

    log.info("Done: %s  (tables=%d, images=%d)", docx_path.name, len(tables), len(images))

    return {
        "success":            True,
        "document":           docx_path.name,
        "error":              None,
        "markdown":           full_md,
        "dom_json_path":      str(dom_path),
        "semantic_json_path": str(semantic_path),
        "markdown_path":      str(md_path),
        "tables_count":       len(tables),
        "images_count":       len(images),
    }


def _run_pipeline(job_id: str, docx_path: Path) -> None:
    """Worker executed in the thread pool."""
    _jobs[job_id]["status"] = "processing"
    try:
        result = _process_document(docx_path)
        _jobs[job_id].update({
            "status": "done" if result["success"] else "error",
            "result": result,
        })
    except Exception as exc:
        log.exception("Pipeline crashed for job %s", job_id)
        _jobs[job_id].update({
            "status": "error",
            "result": {"success": False, "error": str(exc),
                       "markdown": "", "tables_count": 0, "images_count": 0},
        })
    finally:
        try:
            docx_path.unlink(missing_ok=True)
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════════════════
# LEGACY .doc CONVERSION
# ══════════════════════════════════════════════════════════════════════════════

def _convert_doc_to_docx(doc_path: Path) -> Optional[Path]:
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if soffice is None:
        log.warning("LibreOffice not found — cannot convert legacy .doc")
        return None
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    import subprocess
    try:
        subprocess.run(
            [soffice, "--headless", "--convert-to", "docx",
             "--outdir", str(STAGING_DIR), str(doc_path)],
            check=True, capture_output=True, timeout=120,
        )
    except Exception as exc:
        log.error("LibreOffice conversion failed: %s", exc)
        return None
    converted = STAGING_DIR / (doc_path.stem + ".docx")
    return converted if converted.exists() else None


# ══════════════════════════════════════════════════════════════════════════════
# ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/upload", response_model=UploadResponse)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
):
    """
    Upload a .doc or .docx file.
    Returns job_id — poll GET /status/{job_id} for progress.
    Output is written to: uploads/<file_stem>/
    """
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_EXT:
        raise HTTPException(
            status_code=400,
            detail=f"Only .doc and .docx accepted. Got: '{suffix}'",
        )

    job_id = str(uuid.uuid4())
    tmp_path = STAGING_DIR / f"{job_id}{suffix}"

    try:
        with tmp_path.open("wb") as fh:
            shutil.copyfileobj(file.file, fh)
    finally:
        await file.close()

    if suffix == ".doc":
        converted = _convert_doc_to_docx(tmp_path)
        tmp_path.unlink(missing_ok=True)
        if converted is None:
            raise HTTPException(
                status_code=422,
                detail="Legacy .doc conversion requires LibreOffice. Convert to .docx first.",
            )
        tmp_path = converted

    _jobs[job_id] = {
        "status":   "queued",
        "result":   None,
        "filename": file.filename,
    }

    background_tasks.add_task(_executor.submit, _run_pipeline, job_id, tmp_path)

    return UploadResponse(
        job_id=job_id,
        status="queued",
        message=f"'{file.filename}' queued. Poll /status/{job_id}",
    )


@router.get("/status/{job_id}", response_model=JobStatus)
async def get_status(job_id: str):
    """Poll processing status for a job."""
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")

    result: dict = job.get("result") or {}
    md = result.get("markdown", "")

    return JobStatus(
        job_id=job_id,
        status=job["status"],
        document=result.get("document") or job.get("filename"),
        tables_count=result.get("tables_count"),
        images_count=result.get("images_count"),
        markdown_preview=md[:2000] if md else None,
        markdown_path=result.get("markdown_path"),
        semantic_json_path=result.get("semantic_json_path"),
        dom_json_path=result.get("dom_json_path"),
        error=result.get("error"),
    )


@router.get("/download/{job_id}/markdown")
async def download_markdown(job_id: str):
    """Download the generated .md file for a completed job."""
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    if job["status"] != "done":
        raise HTTPException(status_code=409, detail=f"Job not complete (status={job['status']}).")
    md_path = job["result"].get("markdown_path")
    if not md_path or not Path(md_path).exists():
        raise HTTPException(status_code=404, detail="Markdown file not found.")
    return FileResponse(md_path, media_type="text/markdown", filename=Path(md_path).name)


@router.get("/download/{job_id}/semantic")
async def download_semantic(job_id: str):
    """Download the semantic JSON for a completed job."""
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    if job["status"] != "done":
        raise HTTPException(status_code=409, detail="Job not complete.")
    path = job["result"].get("semantic_json_path")
    if not path or not Path(path).exists():
        raise HTTPException(status_code=404, detail="File not found.")
    return FileResponse(path, media_type="application/json", filename=Path(path).name)

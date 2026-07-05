"""
main.py
───────
FastAPI application for the DOCX → Markdown pipeline.

Endpoints
─────────
GET  /              → Serve the upload UI (static/index.html)
GET  /health        → Liveness probe
POST /upload        → Accept a .doc/.docx file, queue it, return job_id
GET  /status/{id}   → Poll job status + result
GET  /download/{id} → Download the generated .md file

Processing is done asynchronously in a thread pool (BackgroundTasks +
concurrent.futures) so the browser doesn't time out on large documents.
"""
import logging
import shutil
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from api.schemas import JobStatus, UploadResponse
from pipeline.config import PipelineConfig
from pipeline.converter import convert_doc_to_docx
from pipeline.orchestrator import build_converter, process_document

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("docx_pipeline.api")

# ── Global state ──────────────────────────────────────────────────────────────
cfg = PipelineConfig()
converter = build_converter(cfg)
executor = ThreadPoolExecutor(max_workers=2)

# In-memory job store  { job_id: { status, result } }
_jobs: dict[str, dict[str, Any]] = {}

ALLOWED_EXTENSIONS = {".doc", ".docx"}
STAGING_DIR = cfg.output_dir / "_staging"

app = FastAPI(
    title="DOCX → Markdown Pipeline",
    description="Upload Word documents, get structured Markdown + JSON back.",
    version="1.0.0",
)

# ── Static files (UI) ────────────────────────────────────────────────────────
_static_dir = Path(__file__).parent.parent / "static"
if _static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _run_pipeline(job_id: str, docx_path: Path) -> None:
    """Worker function executed in the thread pool."""
    _jobs[job_id]["status"] = "processing"
    try:
        result = process_document(docx_path, cfg, converter)
        _jobs[job_id].update(
            {
                "status": "done" if result["success"] else "error",
                "result": result,
            }
        )
    except Exception as exc:
        log.exception("Pipeline crashed for job %s", job_id)
        _jobs[job_id].update(
            {
                "status": "error",
                "result": {
                    "success": False,
                    "error": str(exc),
                    "markdown": "",
                    "tables_count": 0,
                    "images_count": 0,
                },
            }
        )
    finally:
        # Clean up the temp upload file
        try:
            docx_path.unlink(missing_ok=True)
        except Exception:
            pass


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def serve_ui():
    """Serve the upload UI."""
    index = _static_dir / "index.html"
    if index.exists():
        return HTMLResponse(index.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>DOCX Pipeline API</h1><p>UI not found. See /docs</p>")


@app.get("/health")
async def health():
    return {"status": "ok", "model": cfg.bedrock_model_id, "region": cfg.aws_region}


@app.post("/upload", response_model=UploadResponse)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
):
    """
    Upload a .doc or .docx file.
    Returns a job_id — poll GET /status/{job_id} for progress.
    """
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Only .doc and .docx files are accepted. Got: '{suffix}'",
        )

    # Save upload to a temp file so the background thread can read it
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    job_id = str(uuid.uuid4())
    tmp_path = STAGING_DIR / f"{job_id}{suffix}"

    try:
        with tmp_path.open("wb") as fh:
            shutil.copyfileobj(file.file, fh)
    finally:
        await file.close()

    # Handle legacy .doc → convert to .docx first
    if suffix == ".doc":
        converted = convert_doc_to_docx(tmp_path, STAGING_DIR)
        if converted is None:
            tmp_path.unlink(missing_ok=True)
            raise HTTPException(
                status_code=422,
                detail=(
                    "Legacy .doc conversion requires LibreOffice on PATH. "
                    "Please convert to .docx manually and re-upload."
                ),
            )
        tmp_path.unlink(missing_ok=True)
        tmp_path = converted

    _jobs[job_id] = {"status": "queued", "result": None, "filename": file.filename}

    # Submit to thread pool (background_tasks just submits; we track via _jobs)
    background_tasks.add_task(executor.submit, _run_pipeline, job_id, tmp_path)

    return UploadResponse(
        job_id=job_id,
        status="queued",
        message=f"File '{file.filename}' queued. Poll /status/{job_id} for progress.",
    )


@app.get("/status/{job_id}", response_model=JobStatus)
async def get_status(job_id: str):
    """Poll processing status for a job."""
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")

    status = job["status"]
    result: dict[str, Any] = job.get("result") or {}

    md = result.get("markdown", "")
    return JobStatus(
        job_id=job_id,
        status=status,
        document=result.get("document") or job.get("filename"),
        tables_count=result.get("tables_count"),
        images_count=result.get("images_count"),
        markdown_preview=md[:2000] if md else None,
        markdown_path=result.get("markdown_path"),
        semantic_json_path=result.get("semantic_json_path"),
        dom_json_path=result.get("dom_json_path"),
        error=result.get("error"),
    )


@app.get("/download/{job_id}/markdown")
async def download_markdown(job_id: str):
    """Download the generated .md file for a completed job."""
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")
    if job["status"] != "done":
        raise HTTPException(
            status_code=409,
            detail=f"Job is not complete yet (status={job['status']}).",
        )
    md_path = job["result"].get("markdown_path")
    if not md_path or not Path(md_path).exists():
        raise HTTPException(status_code=404, detail="Markdown file not found on disk.")
    return FileResponse(
        md_path,
        media_type="text/markdown",
        filename=Path(md_path).name,
    )


@app.get("/download/{job_id}/semantic")
async def download_semantic_json(job_id: str):
    """Download the semantic JSON for a completed job."""
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")
    if job["status"] != "done":
        raise HTTPException(status_code=409, detail="Job not complete.")
    path = job["result"].get("semantic_json_path")
    if not path or not Path(path).exists():
        raise HTTPException(status_code=404, detail="File not found.")
    return FileResponse(path, media_type="application/json", filename=Path(path).name)

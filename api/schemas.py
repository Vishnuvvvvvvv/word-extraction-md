"""
schemas.py
──────────
Pydantic response/request models for the FastAPI layer.
"""
from typing import Any, Optional
from pydantic import BaseModel


class UploadResponse(BaseModel):
    job_id: str
    status: str          # "queued" | "processing" | "done" | "error"
    message: str


class JobStatus(BaseModel):
    job_id: str
    status: str          # "queued" | "processing" | "done" | "error"
    document: Optional[str] = None
    tables_count: Optional[int] = None
    images_count: Optional[int] = None
    markdown_preview: Optional[str] = None   # first 2 000 chars
    markdown_path: Optional[str] = None
    semantic_json_path: Optional[str] = None
    dom_json_path: Optional[str] = None
    error: Optional[str] = None

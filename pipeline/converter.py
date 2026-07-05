"""
converter.py
────────────
Legacy .doc (pre-2007 binary) → .docx conversion via LibreOffice headless.
If LibreOffice is not on PATH the file is skipped and the caller is warned.
"""
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Optional

log = logging.getLogger("docx_pipeline.converter")


def convert_doc_to_docx(doc_path: Path, staging_dir: Path) -> Optional[Path]:
    """
    Convert a legacy .doc file to .docx using LibreOffice.

    Returns the .docx path on success, or None if conversion isn't possible.
    """
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if soffice is None:
        log.warning(
            "Skipping '%s': legacy .doc format requires LibreOffice ('soffice') "
            "on PATH. Install LibreOffice or pre-convert to .docx manually.",
            doc_path.name,
        )
        return None

    staging_dir.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            [
                soffice,
                "--headless",
                "--convert-to",
                "docx",
                "--outdir",
                str(staging_dir),
                str(doc_path),
            ],
            check=True,
            capture_output=True,
            timeout=120,
        )
    except subprocess.CalledProcessError as e:
        log.error(
            "LibreOffice conversion failed for '%s': %s", doc_path.name, e
        )
        return None
    except subprocess.TimeoutExpired:
        log.error(
            "LibreOffice conversion timed out for '%s'.", doc_path.name
        )
        return None

    converted = staging_dir / (doc_path.stem + ".docx")
    return converted if converted.exists() else None

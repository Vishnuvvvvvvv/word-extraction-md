# ─────────────────────────────────────────────────────────────────────────────
# run.ps1  –  Launch the Anti-Gravity DOCX → Markdown API
# ─────────────────────────────────────────────────────────────────────────────
# The .venv here is a target-installed layout (no Scripts/ folder).
# We manually add the site-packages to PYTHONPATH so Python can find
# docling, fastapi, etc., then invoke uvicorn from that same location.
# ─────────────────────────────────────────────────────────────────────────────

$Root    = $PSScriptRoot
$VenvSP  = Join-Path $Root ".venv\Lib\site-packages"
$Uvicorn = Join-Path $VenvSP "bin\uvicorn.exe"

if (-not (Test-Path $Uvicorn)) {
    Write-Error "Cannot find uvicorn at '$Uvicorn'. Make sure you ran: pip install -r requirements.txt --target .venv\Lib\site-packages"
    exit 1
}

# Set PYTHONPATH so the child process sees the venv packages
$env:PYTHONPATH = $VenvSP

# Hot-reload is handy during development; remove --reload for production
& $Uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

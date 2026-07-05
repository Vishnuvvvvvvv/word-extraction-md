# Anti-Gravity — DOCX → Markdown Pipeline

> Local-first, enterprise-grade Word document parser.  
> Converts `.doc` / `.docx` → structured **Markdown + JSON** using Docling structural parsing, Amazon Textract OCR, and AWS Bedrock Nova Lite for chart semantics.

---

## Table of Contents

1. [Quick Start](#quick-start)
2. [Environment Setup](#environment-setup)
3. [Architecture Overview](#architecture-overview)
4. [Processing Flow (Step by Step)](#processing-flow)
5. [Component Reference](#component-reference)
6. [API Endpoints](#api-endpoints)
7. [What Gets Uploaded](#what-gets-uploaded)
8. [What Gets Processed](#what-gets-processed)
9. [Output Structure](#output-structure)
10. [Markdown Output — What You See](#markdown-output)
11. [Multi-Layout & Reading Order](#multi-layout--reading-order)
12. [Tables — How They Are Handled](#tables)
13. [Charts & Images — How They Are Handled](#charts--images)
14. [The Pylance Import Error Explained](#pylance-import-error)
15. [Testing](#testing)
16. [Troubleshooting](#troubleshooting)

---

## Quick Start

```powershell
# 1. Clone / open the project
cd f:\Other\anti-gravity

# 2. Fill in your AWS credentials
#    Edit .env  (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY)

# 3. Start the API  (sets PYTHONPATH to the non-standard .venv)
.\run.ps1

# 4. Open the web UI
#    http://localhost:8000

# 5. Open Swagger docs
#    http://localhost:8000/docs
```

> **Why `run.ps1`?**  
> The `.venv` in this repo is a *target-install* layout — packages live in  
> `.venv\Lib\site-packages\` with no `Scripts\python.exe`.  
> `run.ps1` sets `$env:PYTHONPATH` so the system Python finds all packages,  
> then calls the venv's own `uvicorn.exe`.

### Manual one-liner (PowerShell)

```powershell
$env:PYTHONPATH = "$PWD\.venv\Lib\site-packages"
.\.venv\Lib\site-packages\bin\uvicorn.exe api.main:app --host 0.0.0.0 --port 8000 --reload
```

---

## Environment Setup

### `.env` file

```ini
# AWS credentials (required for OCR + chart VLM)
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=<your-key>
AWS_SECRET_ACCESS_KEY=<your-secret>
BEDROCK_MODEL_ID=amazon.nova-lite-v1:0

# Pipeline settings
OUTPUT_DIR=./output
USE_VLM=true        # set false to skip Bedrock chart calls
USE_TEXTRACT=true   # set false to skip OCR entirely
```

### Install dependencies

```powershell
# Into the non-standard .venv target layout
pip install -r requirements.txt --target .venv\Lib\site-packages
```

`requirements.txt` includes: `docling`, `pillow`, `boto3`, `python-dotenv`, `fastapi`, `uvicorn[standard]`, `python-multipart`, `pandas`, `aiofiles`

### Required AWS IAM Permissions

| Service | Permission | Used For |
|---|---|---|
| Textract | `textract:DetectDocumentText` | OCR on images |
| Bedrock | `bedrock:InvokeModel` | Chart classification |
| Bedrock | Model access for `amazon.nova-lite-v1:0` | Must be enabled in Bedrock console |

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                        CLIENT  (Browser)                         │
│                   Upload UI  /  REST calls                       │
└────────────────────────────┬─────────────────────────────────────┘
                             │ HTTP
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│                  FastAPI  (api/main.py)                          │
│  POST /upload  → ThreadPoolExecutor → background job            │
│  GET  /status/{id}  GET /download/{id}/markdown                  │
└────────────────────────────┬─────────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│              Orchestrator  (pipeline/orchestrator.py)            │
│                                                                  │
│  ┌─────────────┐   ┌─────────────┐   ┌────────────────────┐    │
│  │  converter  │   │   Docling   │   │  table_extractor   │    │
│  │  (.doc→     │──▶│  Document   │──▶│  extract_tables()  │    │
│  │   .docx)    │   │  Converter  │   └────────────────────┘    │
│  └─────────────┘   └──────┬──────┘                             │
│                            │                                    │
│                            ▼                                    │
│                   ┌─────────────────┐                          │
│                   │ image_extractor │                           │
│                   │ extract_images()│                           │
│                   └────┬────────────┘                          │
│                        │                                        │
│              ┌─────────┴──────────┐                            │
│              ▼                    ▼                             │
│        ┌──────────┐        ┌─────────────┐                    │
│        │  ocr.py  │        │bedrock_vlm  │                    │
│        │ Textract │        │Nova Lite VLM│                    │
│        └──────────┘        └─────────────┘                    │
│                                                                 │
│                    ┌────────────────────┐                      │
│                    │ markdown_renderer  │                       │
│                    │  render_markdown() │                       │
│                    └────────────────────┘                      │
└──────────────────────────────────────────────────────────────────┘
                             │
                             ▼
              output/<docname>/
                ├── <name>.md
                ├── <name>.dom.json
                ├── <name>.semantic.json
                └── images/
                    ├── picture_0.png
                    └── picture_N.png
```

---

## Processing Flow

### Step 1 — Upload (API Layer)

- User POSTs a `.doc` or `.docx` to `POST /upload`
- File saved to `output/_staging/<job_id>.docx`
- If `.doc` (legacy binary) → `converter.py` calls LibreOffice headless to convert first
- A **job_id** (UUID) is returned immediately
- Processing handed off to a `ThreadPoolExecutor` (2 workers) — browser never times out

### Step 2 — Docling Structural Parse (`orchestrator.py`)

- `DocumentConverter` (configured for DOCX with `WordFormatOption`) parses the OOXML XML tree
- Produces a Docling `DoclingDocument` object containing:
  - `doc.texts` — all text blocks in reading order
  - `doc.tables` — table objects with cell-level data
  - `doc.pictures` — embedded image objects
- Exports a near-lossless **DOM JSON** (`<name>.dom.json`) via `doc.export_to_dict()`

### Step 3 — Table Extraction (`table_extractor.py`)

For each table in `doc.tables`:
- Exports a **flattened DataFrame** (easy consumption, loses merged-cell info)
- Exports a **raw cell grid** with `row_span` / `col_span` (lossless merged-cell representation)
- Captures caption and page number

### Step 4 — Image Extraction + OCR (`image_extractor.py` + `ocr.py`)

For each picture in `doc.pictures`:
1. Retrieves PIL image via `picture.get_image(doc)` (with API-version fallback)
2. Saves PNG to `output/<name>/images/picture_N.png`
3. Sends image bytes to **Amazon Textract** `detect_document_text` → returns OCR text lines
4. Images > 5 MB are JPEG-recompressed before upload (Textract hard limit)

### Step 5 — Chart Detection Heuristic (`bedrock_vlm.py`)

`looks_like_chart(img, ocr_text, cfg)` fires a Bedrock call only when ALL conditions met:
- Image area > `min_chart_area` (40,000 px²) — skips logos/icons
- OCR token count ≥ `min_ocr_tokens_for_chart` (2) — charts have axis labels
- OCR token count ≤ `max_ocr_tokens_for_chart` (80) — too much text = scanned page, not chart

### Step 6 — Bedrock VLM Chart Semantics (`bedrock_vlm.py`)

If heuristic passes:
- Image PNG bytes sent to `amazon.nova-lite-v1:0` via Bedrock Converse API
- Structured JSON prompt forces output shape:
  ```json
  {
    "chart_type": "bar|line|pie|table|diagram|not_a_chart",
    "series": [{"label": "...", "value": "..."}],
    "summary": "one sentence"
  }
  ```
- Response JSON parsed; malformed responses caught gracefully

### Step 7 — Semantic JSON (`orchestrator.py`)

All extracted data assembled into `<name>.semantic.json`:
```json
{
  "document": "filename.docx",
  "schema_version": "1.0",
  "num_pages": null,
  "text_blocks_count": 42,
  "tables": [...],
  "images": [...]
}
```

### Step 8 — Markdown Rendering (`markdown_renderer.py`)

Final Markdown = 3 concatenated parts:
1. **Docling base Markdown** — `doc.export_to_markdown()` (headings, paragraphs, inline tables)
2. **Extracted Tables appendix** — each table rendered as a GFM markdown table with caption
3. **Extracted Images & Charts appendix** — per image: dimensions, OCR text block, chart type + series data + summary

---

## Component Reference

| File | Responsibility |
|---|---|
| `api/main.py` | FastAPI app, routes, job store, thread pool |
| `api/schemas.py` | Pydantic models: `UploadResponse`, `JobStatus` |
| `pipeline/config.py` | `PipelineConfig` dataclass — loads all settings from `.env` |
| `pipeline/orchestrator.py` | Top-level pipeline: wires all stages together |
| `pipeline/converter.py` | Legacy `.doc` → `.docx` via LibreOffice headless |
| `pipeline/table_extractor.py` | Extracts tables: flattened records + raw merged-cell grid |
| `pipeline/image_extractor.py` | Extracts images, runs OCR, calls chart heuristic |
| `pipeline/ocr.py` | Amazon Textract wrapper — image bytes → text lines |
| `pipeline/bedrock_vlm.py` | AWS Bedrock Nova Lite wrapper — image → chart JSON |
| `pipeline/markdown_renderer.py` | Assembles final Markdown from all extracted data |
| `static/index.html` | Dark-mode upload UI (single HTML file, no build step) |
| `run.ps1` | PowerShell launch script (sets PYTHONPATH for non-standard venv) |
| `create_test_docx.py` | Generates a rich test `.docx` covering all features |

---

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Serve the upload web UI (`static/index.html`) |
| `GET` | `/health` | Liveness probe — returns model + region |
| `GET` | `/docs` | Interactive Swagger UI |
| `POST` | `/upload` | Upload `.doc`/`.docx` → returns `job_id` |
| `GET` | `/status/{job_id}` | Poll job status + result preview |
| `GET` | `/download/{job_id}/markdown` | Download the `.md` output file |
| `GET` | `/download/{job_id}/semantic` | Download the semantic `.json` file |

### `POST /upload` — Request

```
Content-Type: multipart/form-data
Body: file=<binary .docx>
```

### `POST /upload` — Response

```json
{
  "job_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "status": "queued",
  "message": "File 'test.docx' queued. Poll /status/3fa85f64... for progress."
}
```

### `GET /status/{job_id}` — Response (done)

```json
{
  "job_id": "3fa85f64...",
  "status": "done",
  "document": "test.docx",
  "tables_count": 2,
  "images_count": 3,
  "markdown_preview": "# Title\n\nFirst 2000 chars...",
  "markdown_path": "F:\\Other\\anti-gravity\\output\\test\\test.md",
  "semantic_json_path": "F:\\...\\test.semantic.json",
  "dom_json_path": "F:\\...\\test.dom.json",
  "error": null
}
```

Job status lifecycle: `queued` → `processing` → `done` | `error`

---

## What Gets Uploaded

Only `.doc` and `.docx` files are accepted (enforced by extension check). Maximum upload size is governed by the OS / uvicorn defaults (no explicit limit set in the app).

Files are saved to `output/_staging/<job_id>.docx` and **deleted** after processing completes.

---

## What Gets Processed

The pipeline processes every structural element Docling extracts from the OOXML:

| Element | Docling Object | Handler |
|---|---|---|
| Headings (H1–H6) | `doc.texts` | `markdown_renderer` (base MD) |
| Paragraphs | `doc.texts` | `markdown_renderer` (base MD) |
| Bold / Italic / Underline | inline runs | Docling base MD |
| Ordered / Bullet Lists | `doc.texts` | Docling base MD |
| Tables | `doc.tables` | `table_extractor.py` |
| Merged cells (colspan/rowspan) | `table.data.table_cells` | `table_extractor.py` raw_cells |
| Embedded images | `doc.pictures` | `image_extractor.py` |
| Image OCR text | PIL image bytes | `ocr.py` (Textract) |
| Chart semantics | PNG file | `bedrock_vlm.py` (Nova Lite) |
| Captions | `picture.caption_text()` | `image_extractor.py` |
| Page/bbox provenance | `prov[0].page_no` / `bbox` | both extractors |

---

## Output Structure

After processing `MyReport.docx` the output tree is:

```
output/
└── MyReport/
    ├── MyReport.md              ← Full Markdown (base + tables + images)
    ├── MyReport.dom.json        ← Near-lossless Docling DOM dump
    ├── MyReport.semantic.json   ← Structured semantic data
    └── images/
        ├── picture_0.png
        ├── picture_1.png
        └── picture_N.png
```

---

## Markdown Output

The final `.md` file has **three sections**:

### Section 1 — Docling Base Markdown

Everything Docling extracts natively: headings, paragraphs, lists, inline formatting. Example:

```markdown
# Annual Report FY 2025

## Executive Summary

This report covers...

- Revenue grew 12%
- Claims ratio improved

| Col A | Col B |
|-------|-------|
| val1  | val2  |
```

### Section 2 — Extracted Tables Appendix

```markdown
---
## Extracted Tables

### Table 1  (5×4)
**Caption:** Claims Summary Report

| Claim ID | Policy Holder | Type | Amount (USD) |
|---|---|---|---|
| CLM-001 | Alice Johnson | Auto | $4,200 |
| CLM-002 | Bob Smith | Health | $11,500 |
```

Each table entry includes: dimensions, caption, full GFM table from flattened records.

### Section 3 — Extracted Images & Charts Appendix

```markdown
---
## Extracted Image & Chart Data

### Image 0  (`picture_0.png`)
**Caption:** Figure 1: Quarterly Claims Count
**Dimensions:** 600×360 px

**OCR text:**
```
Annual Claims by Quarter
Q1 Claims Q2 Claims Q3 Claims Q4 Claims
320 415 390 510
Claims
```

**Chart type:** `bar`
**Summary:** A bar chart showing quarterly insurance claims with Q4 having the highest count of 510.

**Data series:**
- Q1 Claims: 320
- Q2 Claims: 415
- Q3 Claims: 390
- Q4 Claims: 510
```

Images that are **not charts** (photos, logos) show only OCR text (if any) and dimensions — no chart section is rendered.

---

## Multi-Layout & Reading Order

**Question: Does the pipeline handle multi-layout documents?**

Yes. Docling operates on the **OOXML XML tree** (not the rendered page pixels), so reading order is determined by element position in the XML document model, not visual left-to-right flow.

- **Multi-column layouts**: Docling traverses XML in document order, which matches intended reading order for standard Word multi-column sections.
- **Text boxes**: Captured as separate text elements; position in reading order depends on their XML anchoring.
- **Headers / Footers**: Currently not extracted (Docling DOCX backend focuses on body content).
- **Nested lists**: Preserved as indented list items in base Markdown.
- **Footnotes**: Included as text blocks if Docling captures them.

The `doc.texts` collection is already in reading order when iterated — no post-processing required.

---

## Tables

### Simple Tables
Exported via `table.export_to_dataframe()` → pandas DataFrame → `to_dict(orient="records")` → rendered as GFM markdown table.

### Merged-Cell Tables (colspan / rowspan)
The **raw_cells** array preserves full merge information:

```json
{
  "text": "North America (merged colspan)",
  "row": 1,
  "col": 0,
  "row_span": 1,
  "col_span": 2,
  "is_header": false
}
```

The flattened Markdown table view loses merge info (merged cells repeat their value), but the `semantic.json` `raw_cells` array is the lossless ground truth.

### Table Caption & Page
Captured from `table.caption_text(doc)` and `table.prov[0].page_no` (page numbers are `null` for DOCX since DOCX isn't paginated at the XML level).

---

## Charts & Images

### Detection Heuristic (local, free)

Before calling Bedrock (cost), a fast heuristic runs:

```
area = width × height
tokens = len(ocr_text.split())

is_chart = area > 40,000 AND 2 <= tokens <= 80
```

| Scenario | Area | Tokens | Bedrock Called? |
|---|---|---|---|
| Bar/line/pie chart | Large | 5–50 | **Yes** |
| Logo / icon | Small | 0–3 | No |
| Photograph (no text) | Large | 0–1 | No |
| Scanned text page | Large | 200+ | No |

### Bedrock VLM Output (per chart image)

```json
{
  "chart_type": "bar",
  "series": [
    {"label": "Q1 Claims", "value": "320"},
    {"label": "Q2 Claims", "value": "415"}
  ],
  "summary": "A bar chart showing quarterly insurance claims count."
}
```

`chart_type` values: `bar`, `line`, `pie`, `table`, `diagram`, `not_a_chart`  
Sentinel values (not rendered in MD): `not_evaluated`, `not_a_chart`, `unavailable`, `error`

---

## Pylance Import Error

```
Cannot find module `docling.datamodel.base_models`
```

**Root cause**: VS Code's Pylance extension is using the **system Python** (`C:\Python313`) as the interpreter, which has no `docling` installed. The project's `docling` lives in `.venv\Lib\site-packages\` (a non-standard target-install layout with no `Scripts\` folder).

**Fix — select the right interpreter in VS Code**:

1. `Ctrl+Shift+P` → **Python: Select Interpreter**
2. Choose **Enter interpreter path...**
3. Paste: `C:\Python313\python.exe` *(same Python, but add PYTHONPATH)*

Since the venv has no `python.exe`, the cleanest fix is to add a `.env` file that VS Code reads:

```ini
# .env  (already exists — add this line)
PYTHONPATH=f:/Other/anti-gravity/.venv/Lib/site-packages
```

Then in `.vscode/settings.json`:

```json
{
  "python.envFile": "${workspaceFolder}/.env",
  "python.defaultInterpreterPath": "C:\\Python313\\python.exe"
}
```

This makes Pylance resolve `docling` correctly and eliminates the red squiggle.

**At runtime the error does NOT occur** because `run.ps1` sets `$env:PYTHONPATH` before launching uvicorn.

---

## Testing

### Generate the test document

```powershell
$env:PYTHONPATH = "$PWD\.venv\Lib\site-packages"
python create_test_docx.py
# Output: test_docs/AntiGravity_Test.docx
```

The test document includes:
- H1/H2/H3 headings, paragraphs, bold/italic/underline
- Ordered list + bullet list
- Simple 5×4 data table with header row
- Merged-cell table (colspan + rowspan)
- Bar chart image (PIL-drawn)
- Line chart image (dual-series)
- Pie chart image
- Photograph (gradient — should NOT trigger VLM)
- Multi-section reading-order check

### Test with the provided enterprise sample

```powershell
# Upload via curl (requires curl on PATH)
curl -X POST http://localhost:8000/upload `
  -F "file=@F:\downloads\Enterprise_Document_Parser_Test_Sample.docx"

# Or use the web UI at http://localhost:8000
```

### Check all endpoints

```powershell
# Health
Invoke-RestMethod http://localhost:8000/health

# Upload (PowerShell)
$form = @{ file = Get-Item "test_docs\AntiGravity_Test.docx" }
$r = Invoke-RestMethod -Uri http://localhost:8000/upload -Method POST -Form $form
$jobId = $r.job_id

# Poll status
Invoke-RestMethod "http://localhost:8000/status/$jobId"

# Download markdown
Invoke-RestMethod "http://localhost:8000/download/$jobId/markdown" -OutFile result.md
```

---

## Troubleshooting

| Problem | Cause | Fix |
|---|---|---|
| `ModuleNotFoundError: docling` | Wrong Python / missing PYTHONPATH | Use `run.ps1` or set `$env:PYTHONPATH` manually |
| `Textract OCR failed` | Missing IAM permission | Add `textract:DetectDocumentText` to IAM user |
| `AWS Bedrock call failed` | Model not enabled | Enable `amazon.nova-lite-v1:0` in Bedrock console → Model access |
| `.doc` returns 422 | LibreOffice not on PATH | Install LibreOffice or pre-convert to `.docx` |
| `PaginatedPipelineOptions` warning in log | Docling version mismatch | Non-fatal; falls back to default `DocumentConverter()` |
| Pylance red squiggles | VS Code using system Python | Add `PYTHONPATH` to `.vscode/settings.json` (see above) |
| Empty `images_count: 0` | No embedded pictures in DOCX | Expected for text-only documents |
| Chart `semantic.chart_type = "not_evaluated"` | Image didn't pass heuristic | Normal for logos/photos — lower `min_chart_area` in config if needed |

# MarkItDown Document Extraction Pipeline

This README explains the internal architecture, tools, and workflow of the newly created `markitdown_pipeline_router.py`. This pipeline is an asynchronous, API-first system designed to perform deep data extraction from Word documents (`.doc` and `.docx`), including complex embedded tables, images, and charts.

## 🚀 How to Run It

### 1. Standalone Testing (Recommended)
You can run the router in isolation using the existing test application (`test_router_app.py`):

```bash
# Activate your virtual environment
F:\Other\anti-gravity\.venv\Scripts\Activate

# Run the Uvicorn server
uv run uvicorn test_router_app:app --reload --port 8001
```

Once running, access the interactive API documentation at [http://localhost:8001/docs](http://localhost:8001/docs).

### 2. Integration into your Main App
Drop the router directly into your primary FastAPI application (`main.py`):

```python
from fastapi import FastAPI
from markitdown_pipeline_router import router as markitdown_router

app = FastAPI()
app.include_router(markitdown_router, prefix="/markitdown", tags=["MarkItDown Pipeline"])
```

## 🏗️ Internal Architecture

The pipeline is built as a **Self-Contained FastAPI Router**. It acts as a modular component that maintains its own state (an in-memory job store) and manages background concurrency via a `ThreadPoolExecutor`.

- **Asynchronous Execution:** File uploads happen synchronously, but the heavy extraction processes (OCR, parsing, LLM calls) are offloaded to background threads to prevent blocking the API event loop.
- **Microservice Ready:** By utilizing an in-memory job queue and UUIDs, this router effectively acts as a mini-microservice. You upload a document, get a `job_id`, and poll the status endpoint until processing is complete.
- **Dual-Engine Extraction:** It combines two different parsing paradigms:
  - **MarkItDown:** Used for highly accurate, readable Markdown generation.
  - **PyPandoc:** Used for deep DOM (Document Object Model) analysis and media extraction.

## 🛠️ Tools Used

| Tool | Purpose |
|------|---------|
| **FastAPI** | REST API endpoints, background task orchestration, and routing. |
| **MarkItDown** (Microsoft) | Primary engine for converting the raw `.docx` text into clean Markdown. |
| **PyPandoc** | Extracts the deep Abstract Syntax Tree (AST) as JSON and isolates embedded media (images) from the document. |
| **Amazon Textract** | Cloud OCR engine used to read text baked into images inside the document. |
| **Amazon Bedrock (Nova Lite)** | Vision-Language Model (VLM) used for intelligent chart detection. It determines if an image is a chart, summarizes it, and extracts data series as JSON. |
| **doc2docx / LibreOffice** | Pre-processing fallback to handle legacy `.doc` files by converting them to modern `.docx`. |
| **Pillow (PIL)** | Image processing, resizing, and byte-formatting before sending media to AWS. |

## 🔄 The Workflow

The lifecycle of a document moving through the pipeline is handled in a 5-step process:

1. **Upload & Pre-processing** (`/upload`)
   - The user uploads a document.
   - If the file is a legacy `.doc`, it runs through a pre-processor (`doc2docx` or headless LibreOffice) to upgrade it to a `.docx`.
   - The file is saved to a `_staging` directory and assigned a UUID. A background task is queued.

2. **Core Markdown Extraction** (`MarkItDown`)
   - `MarkItDown` parses the document and generates a highly clean, base Markdown representation of all text and structural elements (like headings and basic tables).

3. **DOM & Media Extraction** (`PyPandoc`)
   - `pypandoc` converts the document into a strict JSON Abstract Syntax Tree (AST).
   - Simultaneously, `pypandoc` extracts all embedded media (images, charts, logos) and saves them to a localized `images/` directory.

4. **Semantic Enrichment & OCR** (`AWS Integration`)
   - The system iterates over every extracted image.
   - **Textract** performs OCR to extract any textual data from the image.
   - The image and OCR text are evaluated heuristically. If they resemble a chart/graph, the image is sent to **Amazon Bedrock**. The LLM analyzes the chart, identifies its type (bar, pie, line), and extracts the plotted data series into a structured JSON schema.

5. **Post-Processing & Output Generation**
   - The pipeline renders a final, composite Markdown document. It appends a special "Extracted Image & Chart Data" appendix to the base Markdown, containing the OCR text and Bedrock chart semantics for all identified images.
   - The system outputs three files:
     - `document.md`: The final enriched Markdown.
     - `document.semantic.json`: A structured summary of the extraction, containing metadata, table indexes, and image semantics.
     - `document.dom.json`: The raw PyPandoc AST for potential downstream layout reconstruction.
   - The user can poll the `/status/{job_id}` endpoint and eventually hit the `/download` endpoints to retrieve the assets.

# Anti-Gravity Pipeline — Complete Beginner's Guide

> You don't need any prior knowledge. Read top to bottom — every "why" is answered.

---

## The Big Picture (One Sentence)

You drop a Word document (`.docx`) into this system, and it spits out a clean, structured text file (Markdown + JSON) that any AI, database, or search engine can actually read and understand — including the text inside images and charts.

---

## Why Does This Even Exist?

Word documents (`.docx`) are **terrible** for machines to read. Here's why:

| Problem | Example |
|---|---|
| Text is buried in XML tags | `<w:t>Hello</w:t>` instead of just `Hello` |
| Tables are complex nested XML | 50 lines of XML for a 3-row table |
| Images are raw binary blobs | A chart image is just pixels — no numbers |
| No semantic meaning | The file doesn't say "this is a heading" in plain text |
| Charts contain data visually | A bar chart's numbers exist only as pixels |

This pipeline solves all of that. It converts the messy Word format into clean, machine-readable Markdown and JSON.

---

## The Three Tools — Why Each One Exists

Before the flow, understand *why* there are three different tools:

### 1. Docling (by IBM)
**What it is:** A Python library that reads Word/PDF/PowerPoint files and understands their structure.

**What it does:** Opens the `.docx` file, reads the internal XML, and says:
- "This is a Heading 1"
- "This is a paragraph of text"
- "This is a table with 3 rows and 4 columns"
- "This is an embedded image"

**Why not just use Python's built-in XML parser?** Because `.docx` XML is incredibly complex. Docling handles all the edge cases (merged cells, multi-column layouts, footnotes, etc.) for you.

**Cannot be replaced by Textract or Nova Lite** — it's the *structural* reader. Without it, you don't even know where the table starts and ends.

---

### 2. Amazon Textract (AWS managed OCR)
**What it is:** A cloud service that reads text from images (OCR = Optical Character Recognition).

**What it does:** You send it a picture (PNG/JPEG), it sends back the text it found in that picture.

**Why is this needed?** When a Word document has an embedded image — like a promotional banner or a chart — the text inside that image is **not text** to the computer. It's just coloured pixels. Docling can extract the image, but it can't read what's written on it. Textract can.

**Example:** The enterprise test doc had this image with text:
```
SPECIAL OFFER
Premium Discount: 15%
Policy Code: LIFE-2026-A
```
Without Textract, that information is completely invisible to any downstream AI system.

**Can it be replaced?**
- Yes — by **Google Cloud Vision OCR**, **Azure Computer Vision**, or local **Tesseract** (free, open-source).
- Tesseract is free but needs a local binary install and is less accurate on complex layouts.
- The code is in `pipeline/ocr.py` — swap the client there.

---

### 3. AWS Bedrock Nova Lite (Vision-Language Model / VLM)
**What it is:** A multimodal AI model (like a "mini GPT-4 Vision") that can look at an image and understand what it *means*.

**What it does:** You send it a chart image, it sends back structured JSON:
```json
{
  "chart_type": "bar",
  "series": [{"label": "Q1", "value": "320"}, ...],
  "summary": "A bar chart showing quarterly claims"
}
```

**Why is Textract not enough for charts?** Textract just reads the raw text pixels:
```
Annual Claims by Quarter
600
510
415
390
320
Q1 Claims Q2 Claims ...
```
That's messy. It doesn't know that `510` is the value for `Q4 Claims`. Nova Lite understands the *relationship* between the numbers and the labels — it reasons like a human would.

**This is where the real AI intelligence lives.**

**Can it be replaced?**
- Yes — by **Claude 3 Haiku/Sonnet** (also on Bedrock), **GPT-4o** (OpenAI), **Gemini Flash** (Google), or any multimodal model.
- The code is in `pipeline/bedrock_vlm.py` — swap the `client.converse()` call.

---

## The Full Step-by-Step Flow

```
Your DOCX file
     │
     ▼ Step 1
┌─────────────────────────────────┐
│  API receives the file          │
│  (FastAPI — api/main.py)        │
│  Saves it, creates a job ID     │
│  Returns job_id immediately     │
└──────────────┬──────────────────┘
               │
               ▼ Step 2 (background thread)
┌─────────────────────────────────┐
│  Docling parses the DOCX        │
│  (orchestrator.py)              │
│                                 │
│  Reads all OOXML structure      │
│  Produces a DoclingDocument     │
│  with .texts, .tables,          │
│  .pictures collections          │
└──────────────┬──────────────────┘
               │
       ┌───────┴───────┐
       │               │
       ▼               ▼
Step 3: Tables    Step 4: Images
       │               │
       ▼               ▼ Step 4a: Save PNG
┌────────────┐   ┌────────────────┐
│table_       │   │image_extractor │
│extractor.py│   │.py             │
│            │   │                │
│2 outputs:  │   │ Every image:   │
│- flattened │   │ → saved as PNG │
│  dataframe │   │ → sent to      │
│- raw cells │   │   Textract OCR │
│  with spans│   └───────┬────────┘
└─────┬──────┘           │
      │           ┌──────┴──────┐
      │           │Step 4b:     │
      │           │heuristic    │
      │           │check        │
      │           │             │
      │           │ Is it a     │
      │           │ chart?      │
      │           └──────┬──────┘
      │                  │
      │           ┌──────┴──────┐
      │        YES│             │NO
      │           ▼             ▼
      │    ┌────────────┐  ┌──────────┐
      │    │Step 4c:    │  │Skip VLM  │
      │    │Bedrock VLM │  │chart_type│
      │    │Nova Lite   │  │="not_    │
      │    │→ structured│  │evaluated"│
      │    │  chart JSON│  └──────────┘
      │    └─────┬──────┘
      │          │
      └────┬─────┘
           │
           ▼ Step 5
┌─────────────────────────────────┐
│  semantic.json assembled        │
│  (orchestrator.py)              │
│  All tables + images + OCR      │
│  + chart semantics in one JSON  │
└──────────────┬──────────────────┘
               │
               ▼ Step 6
┌─────────────────────────────────┐
│  Markdown assembled             │
│  (markdown_renderer.py)         │
│                                 │
│  Part 1: Docling base MD        │
│  Part 2: Tables appendix        │
│  Part 3: Images/charts appendix │
└──────────────┬──────────────────┘
               │
               ▼ Step 7
         .md + .dom.json
         + .semantic.json
         + images/*.png
         written to disk
```

---

## How Tables Are Handled

### What Docling does first (Step 2)
When Docling parses the DOCX, it finds tables in the XML and includes them in the base Markdown it generates. That's the table you see in the **first half** of the output MD file:

```markdown
| Benefit          | Coverage   | Waiting Period |
|------------------|------------|----------------|
| Life Cover       | ₹50,00,000 | 0 days         |
```

### What table_extractor.py does (Step 3)
It then runs a **second, deeper pass** on the same tables with two goals:

**Goal 1 — Flattened records (for easy machine consumption)**
```python
df = table.export_to_dataframe(doc)  # pandas DataFrame
records = df.to_dict(orient="records")
# → [{"Benefit": "Life Cover", "Coverage": "₹50,00,000", ...}, ...]
```
This goes into `semantic.json` and is also rendered as the table in the **appendix section** of the MD file.

**Goal 2 — Raw cell grid (lossless merged-cell data)**
```python
for cell in table.data.table_cells:
    raw_cells.append({
        "text": cell.text,
        "row": cell.start_row_offset_idx,
        "col": cell.start_col_offset_idx,
        "row_span": cell.row_span,   # ← merged rows
        "col_span": cell.col_span,   # ← merged columns
        "is_header": cell.column_header
    })
```
This is critical for **merged cells**. When you merge two cells in Word, the flattened view just duplicates the text. The raw_cells view knows the cell actually spans 2 columns — that's the lossless ground truth.

---

## Why Tables Appear Twice in the MD File — Answered

Yes, the table shows up twice. **It is NOT double processing.** It is intentional:

| Occurrence | Where | Source | Purpose |
|---|---|---|---|
| **First time** | Top of the MD file | Docling's `export_to_markdown()` | The document as written — heading + table in context |
| **Second time** | `## Extracted Tables` appendix | `table_extractor.py` + `markdown_renderer.py` | Enriched version with caption, dimensions, and machine-readable structured data |

Think of it like a book:
- The **first** table is in the main chapter (in context with surrounding text)
- The **second** table is in the appendix (with extra metadata, structured for reference)

The appendix version is what you'd feed to a downstream AI — it has the caption, size, and the raw_cells JSON that the inline version doesn't have.

**If you don't want the duplication:** Remove the tables section from `render_markdown()` in `markdown_renderer.py`. The semantic JSON still captures everything.

---

## How Images Are Handled

### Every image goes through this exact sequence:

```
Embedded image in DOCX
         │
         ▼
   Docling extracts it
   as a PIL Image object
         │
         ▼
   Saved as picture_N.png
   in output/images/
         │
         ▼
   Sent to Amazon Textract
   → returns OCR text lines
   (e.g. "SPECIAL OFFER\nPremium Discount: 15%")
         │
         ▼
   Heuristic check runs:
   area > 40,000 px²?  AND  2 ≤ OCR tokens ≤ 80?
         │
    YES ─┤─ NO
         │       └→ semantic = {chart_type: "not_evaluated"}
         ▼               (stops here — no Bedrock call)
   Bedrock Nova Lite called
   → returns chart JSON
   {chart_type, series, summary}
         │
         ▼
   Everything stored in
   semantic.json + rendered
   in the MD appendix
```

---

## Where Is the Intelligence / AI Used?

There are **two AI layers**:

### Layer 1 — Textract (Narrow AI / ML)
- **Type:** Traditional machine learning OCR model
- **Task:** "Read the pixels in this image and tell me what letters/numbers you see"
- **Output:** Raw text lines — no understanding of meaning
- **Cost:** ~$1.50 per 1,000 images
- **Used on:** Every single image

### Layer 2 — Bedrock Nova Lite (Generative AI / VLM)
- **Type:** Multimodal large language model
- **Task:** "Look at this chart image and tell me: what type is it, what data does it show, and summarise it"
- **Output:** Structured semantic JSON with understanding
- **Cost:** Higher — only called when heuristic says "this looks like a chart"
- **Used on:** Images that pass the heuristic filter only

**The heuristic exists purely to save money.** A bar chart with axis labels has 10–30 OCR tokens. A photograph has 0–2. A scanned text page has 200+. The sweet spot (2–80 tokens) catches charts while skipping photos and text pages.

---

## How Routing / Content Analysis Works

This is the decision tree the pipeline runs for every image:

```
For each picture in the document:
    ┌────────────────────────────────────┐
    │ Is image area > 40,000 px²?        │
    │ (filters out logos, icons, bullets)│
    └──────────────┬─────────────────────┘
                   │ YES
                   ▼
    ┌────────────────────────────────────┐
    │ Does OCR text have ≥ 2 tokens?     │
    │ (filters out blank/photo images)   │
    └──────────────┬─────────────────────┘
                   │ YES
                   ▼
    ┌────────────────────────────────────┐
    │ Does OCR text have ≤ 80 tokens?    │
    │ (filters out scanned text pages)   │
    └──────────────┬─────────────────────┘
                   │ YES
                   ▼
            Call Bedrock VLM
            → Chart semantics
```

For **text content**: Docling handles reading order automatically by traversing the XML tree in document order — not by guessing from pixel positions on a page.

---

## The Tools Compared — Replaceability

```
┌────────────┬──────────────────┬─────────────────────────────────────────────────────┐
│ Tool       │ Role             │ Can be replaced with...                             │
├────────────┼──────────────────┼─────────────────────────────────────────────────────┤
│ Docling    │ Structural parse │ python-docx (DIY, less accurate), Unstructured.io,  │
│            │ of DOCX/PDF      │ Apache Tika — but Docling is the best for DOCX      │
├────────────┼──────────────────┼─────────────────────────────────────────────────────┤
│ Textract   │ OCR on images    │ Tesseract (free, local), Google Vision OCR,         │
│            │                  │ Azure Computer Vision — all return same text output  │
├────────────┼──────────────────┼─────────────────────────────────────────────────────┤
│ Nova Lite  │ Chart semantics  │ Claude 3 Haiku (Bedrock), GPT-4o (OpenAI),          │
│ (Bedrock)  │ (multimodal AI)  │ Gemini Flash (Google) — any VLM with image input    │
├────────────┼──────────────────┼─────────────────────────────────────────────────────┤
│ FastAPI    │ REST API layer   │ Flask, Django — FastAPI chosen for async performance │
├────────────┼──────────────────┼─────────────────────────────────────────────────────┤
│ pandas     │ Table flattening │ polars, plain Python dicts — minor component        │
└────────────┴──────────────────┴─────────────────────────────────────────────────────┘
```

**Key insight:** Docling, Textract, and Nova Lite are not competitors — they each do a fundamentally different job that the others cannot do:
- Docling = **structure** (what is this element?)
- Textract = **text in pixels** (what does this image say?)
- Nova Lite = **meaning of visuals** (what does this chart mean?)

---

## Visual Summary of the Whole System

```
Word Document (.docx)
        │
        │  You upload via browser or curl
        ▼
   ┌─────────┐
   │ FastAPI │  ← Just the door. Receives file, gives you a tracking number (job_id).
   └────┬────┘
        │
        ▼
   ┌─────────┐
   │ Docling │  ← The reader. Understands Word structure: headings, tables, images.
   └────┬────┘
        │
   ┌────┴────────────────────────┐
   │                             │
   ▼                             ▼
Tables (structural)         Images (pixels)
   │                             │
   ▼                             ▼
table_extractor           ┌──────────────┐
- flattened records       │   Textract   │  ← Reads text IN the image pixels
- raw cell grid           │   (OCR)      │
(for merged cells)        └──────┬───────┘
                                 │
                          ┌──────┴───────┐
                          │  Heuristic   │  ← Is this a chart or a photo?
                          └──────┬───────┘
                                 │ (chart)
                          ┌──────┴───────┐
                          │  Nova Lite   │  ← Understands what the chart MEANS
                          │  (VLM AI)    │
                          └──────┬───────┘
                                 │
        ┌────────────────────────┘
        │
        ▼
   markdown_renderer
   assembles everything:
   ┌──────────────────────────────┐
   │ Part 1: Full document text   │
   │ Part 2: Tables (again,       │
   │         but with metadata)   │
   │ Part 3: Images + OCR text    │
   │         + Chart: type/data/  │
   │           summary (if chart) │
   └──────────────────────────────┘
        │
        ▼
   output/
   ├── document.md          ← Human + AI readable text
   ├── document.dom.json    ← Raw Docling structure
   ├── document.semantic.json ← Your structured data
   └── images/*.png         ← Saved image files
```

---

## Why the Table Appears in the MD Twice — One More Way to Think About It

Imagine you scan a medical report. The report has a table of blood test results in the middle of the text. When you get the processed output:

- **First occurrence** = the table as it appeared in the document, in context, surrounded by the doctor's notes
- **Second occurrence (appendix)** = the same table but now tagged with: "this is Table 3, it has 5 rows and 4 columns, here's the caption, here are all values as structured data"

The appendix version is what you'd load into a database or feed to an AI for analysis. The first version is for humans reading the document linearly.

**They serve different audiences.** Remove either one if you don't need it.

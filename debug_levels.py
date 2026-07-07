"""Debug: compare python-docx list items vs markdown list items."""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, r"f:\Other\anti-gravity\.venv\Lib\site-packages")

from pathlib import Path
from docx import Document
from docx.oxml.ns import qn

DOCX = Path(r"f:\Other\anti-gravity\test_docs\sample-files.com-lists.docx")
# Use the most recent MD output
MD   = Path(r"f:\Other\anti-gravity\uploads\00e3ff73-d63f-4617-bf5f-c4ea87f25bd4\00e3ff73-d63f-4617-bf5f-c4ea87f25bd4.md")

import re
item_re = re.compile(r'^\s*([-*+]|\d+\.)\s+(.+)$')

# ── python-docx list items ──────────────────────────────────────────────────
print("=== python-docx list items (text → ilvl) ===")
doc = Document(str(DOCX))
docx_items = {}
for para in doc.paragraphs:
    numPr = para._p.find(qn('w:numPr'))
    if numPr is None:
        continue
    ilvl_el = numPr.find(qn('w:ilvl'))
    if ilvl_el is None:
        continue
    level = int(ilvl_el.get(qn('w:val'), 0))
    text = ' '.join(para.text.split())
    if text:
        docx_items[text] = level
        print(f"  ilvl={level}  repr={repr(text[:60])}")

print(f"\nTotal docx list items: {len(docx_items)}\n")

# ── markdown list items ─────────────────────────────────────────────────────
print("=== Markdown list items ===")
md_text = MD.read_text(encoding="utf-8")
md_items = {}
for line in md_text.splitlines():
    m = item_re.match(line)
    if m:
        text = ' '.join(m.group(2).split())
        md_items[text] = line

print(f"Total md list items: {len(md_items)}\n")

# ── compare ─────────────────────────────────────────────────────────────────
print("=== Items in DOCX but NOT matched in MD ===")
missed = 0
for k, v in docx_items.items():
    if k not in md_items:
        print(f"  MISS ilvl={v}  repr={repr(k[:80])}")
        missed += 1
print(f"Missed: {missed}/{len(docx_items)}\n")

print("=== Items in MD but NOT in DOCX level_map ===")
for k in md_items:
    if k not in docx_items:
        print(f"  UNMATCHED: repr={repr(k[:80])}")

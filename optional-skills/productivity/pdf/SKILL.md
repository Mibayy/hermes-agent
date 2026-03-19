---
name: pdf
description: Extract text from PDFs, read metadata, search content, split and merge PDF files. Requires pypdf (pip install pypdf).
version: 1.0.0
author: Mibayy
license: MIT
metadata:
  hermes:
    tags: [pdf, extract, text, metadata, search, split, merge, documents]
    category: productivity
    requires_toolsets: [terminal]
---

# PDF Skill

Read, search, split, and merge PDF files.
6 commands: extract, metadata, info, search, split, merge.

Requires one dependency: `pip install pypdf`

---

## When to Use
- User wants to extract text from a PDF
- User wants to read PDF metadata (author, title, creation date...)
- User wants to search for a word or pattern inside a PDF
- User wants to split a PDF into specific pages
- User wants to merge multiple PDFs into one

---

## Prerequisites
```bash
pip install pypdf
```
Script path: `~/.hermes/skills/productivity/pdf/scripts/pdf_client.py`

---

## Quick Reference

```
SCRIPT=~/.hermes/skills/productivity/pdf/scripts/pdf_client.py
python3 $SCRIPT extract document.pdf
python3 $SCRIPT extract document.pdf --pages 1-5 --output txt
python3 $SCRIPT extract document.pdf --pages 1,3,7 --output json
python3 $SCRIPT metadata document.pdf
python3 $SCRIPT info document.pdf
python3 $SCRIPT search document.pdf "revenue"
python3 $SCRIPT split document.pdf --pages 1-10 --output-dir ./out
python3 $SCRIPT merge a.pdf b.pdf c.pdf --output combined.pdf
```

---

## Commands

### extract FILE [--pages PAGES] [--output txt|json]
Extract text. Pages: `1-5`, `1,3,7`, or `all`. Output: plain text or JSON per page.

### metadata FILE
Title, author, subject, creator, producer, creation date, page count.

### info FILE
Full info: metadata + page dimensions (mm) + word count + image detection.

### search FILE QUERY
Case-insensitive regex search. Returns {page, line_number, context} per match.

### split FILE [--pages PAGES] [--output-dir DIR]
Extract pages to new PDF. Output: `FILE_pages_1-5.pdf`

### merge FILE1 FILE2 [...] [--output OUTPUT]
Merge PDFs. Default output: `merged.pdf`

---

## Pitfalls
- Scanned PDFs (image-only) produce no extractable text. Use tesseract for OCR.
- Encrypted PDFs: tries empty password first, exits cleanly if password required.
- Page numbers are 1-indexed.

---

## Verification
```bash
pip install pypdf
python3 ~/.hermes/skills/productivity/pdf/scripts/pdf_client.py --help
```

---
name: pdf-processing
description: "Extract text and tables from PDF files, fill forms, merge documents. Use when working with PDF files or when user mentions PDFs, forms, or document extraction."
license: MIT
compatibility: "Requires python3"
metadata:
  author: hpagent
  version: "1.0"
---

# PDF Processing Skill

## Instructions

This skill guides the agent through common PDF-related tasks.

### Step 1: Identify the Task
Determine whether the user needs:
- **Text extraction** — reading text content from PDFs
- **Form filling** — populating PDF form fields
- **Merging** — combining multiple PDFs into one
- **Splitting** — breaking a PDF into separate pages

### Step 2: Choose the Right Tool
- For text extraction: read the file with `fs_read` first, then use Python to parse
- For form filling: identify the form fields, then populate with user-provided data
- For merging/splitting: use `pdftk` or `qpdf` via bash commands

### Step 3: Verify Output
- Check that the output file exists and has non-zero size
- Verify the content matches what was requested

## Edge Cases
- Encrypted PDFs may require password — ask the user
- Scanned/image-based PDFs need OCR — warn the user about limitations
- Large PDFs (>100MB) may be slow — consider streaming approaches

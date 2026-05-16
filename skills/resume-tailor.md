---
name: resume-tailor
description: Generate an ATS-optimized, tailored resume PDF for a specific job using Kimi K2.6 + WeasyPrint. Reads profile.md, calls the LLM to tailor each section, renders HTML → PDF.
---

# Resume Tailor

Generates a tailored, ATS-optimized resume for a specific job.

## What this skill does

1. Reads `profile.md` (candidate profile).
2. Sends profile + job description to Kimi K2.6 via Azure AI Foundry.
3. LLM returns tailored content as JSON: summary, top skills, rewritten experience bullets, relevant projects.
4. Python fills a clean HTML template with the tailored content.
5. WeasyPrint renders HTML → PDF.
6. Saves to `applications/{company}-{role}/resume.pdf` (and `resume.html` as source).

## Usage

```python
from generator import generate_resume

job = {
    "title": "Data Analyst Intern",
    "company": "Razorpay",
    "description": "...",
}
path = generate_resume(job)
# → applications/razorpay-data_analyst_intern/resume.pdf
```

## Output

| File | Description |
|------|-------------|
| `resume.html` | HTML source (always saved) |
| `resume.pdf` | PDF (saved if WeasyPrint is installed) |
| `job-description.txt` | JD saved for reference (via `generate_docs`) |

## Fallback

If WeasyPrint is not installed, only `resume.html` is saved and its path is returned.
Install: `pip install weasyprint` (also requires GTK3 on Windows).

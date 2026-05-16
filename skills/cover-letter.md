---
name: cover-letter
description: Generate a targeted, non-generic cover letter PDF for a specific job using Kimi K2.6 + WeasyPrint. Reads profile.md, calls the LLM, renders HTML → PDF.
---

# Cover Letter Generator

Generates a targeted, role-specific cover letter for a specific job.

## What this skill does

1. Reads `profile.md` (candidate profile).
2. Sends profile + job description to Kimi K2.6 via Azure AI Foundry.
3. LLM writes 3-4 paragraphs (~270 words): company-specific opening, 2 concrete examples from the profile, confident closing.
4. Python wraps the text in a clean HTML template with sender details and date.
5. WeasyPrint renders HTML → PDF.
6. Saves to `applications/{company}-{role}/cover-letter.pdf`.

## Usage

```python
from generator import generate_cover_letter

job = {
    "title": "Data Analyst Intern",
    "company": "Razorpay",
    "description": "...",
}
path = generate_cover_letter(job)
# → applications/razorpay-data_analyst_intern/cover-letter.pdf
```

## To generate both resume + cover letter at once

```python
from generator import generate_docs
result = generate_docs(job)
# result = {
#   "output_dir": "applications/razorpay-data_analyst_intern/",
#   "resume_pdf": "...resume.pdf",
#   "cover_letter_pdf": "...cover-letter.pdf",
# }
```

## Output

| File | Description |
|------|-------------|
| `cover-letter.html` | HTML source (always saved) |
| `cover-letter.pdf` | PDF (saved if WeasyPrint is installed) |

## LLM prompt rules

- References something specific about the company/role (not generic)
- Cites 2 concrete examples with numbers from the candidate's actual experience
- Avoids clichés ("I am writing to express my interest...")
- ~270 words, professional but not stiff

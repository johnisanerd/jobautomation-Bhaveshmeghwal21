---
name: apply-greenhouse
description: Fill a Greenhouse ATS application form via Playwright and submit after Telegram confirmation. Implemented in applicant.py (_fill_greenhouse).
---

# Apply via Greenhouse

> **Status: Implemented — Phase 4 complete**

## What this skill does

1. Opens Greenhouse job URL with async Playwright.
2. Clicks the "Apply for this job" button to open the form.
3. Auto-fills: first name, last name, email, phone, resume upload, cover letter upload, LinkedIn URL.
4. Answers any custom questions via LLM (`_answer_custom_question`).
5. Takes a screenshot of the filled form.
6. Sends screenshot to Telegram with [Submit Application] / [Skip Submission] buttons.
7. Submits only after explicit user approval (5-minute timeout → auto-skip).
8. Logs result via `tracker.log_application`.

## Rule

**Never submits a form without Telegram confirmation.**

## Usage

Invoked automatically by `daily_run()` for approved jobs whose URL matches `greenhouse.io` or `boards.greenhouse`. To trigger manually:

```python
import asyncio, applicant
result = asyncio.run(applicant.apply_to_job(job_dict, docs_dict))
# Returns: "submitted" | "skipped_by_user" | "needs_human" | "failed"
```

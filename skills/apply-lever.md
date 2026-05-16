---
name: apply-lever
description: Fill a Lever ATS application form via Playwright and submit after Telegram confirmation. Implemented in applicant.py (_fill_lever).
---

# Apply via Lever

> **Status: Implemented — Phase 4 complete**

## What this skill does

1. Navigates to `{lever_url}/apply` with async Playwright.
2. Auto-fills: name, email, phone, resume upload, cover letter upload, LinkedIn URL.
3. Answers any custom questions via LLM (`_answer_custom_question`).
4. Takes a screenshot of the filled form.
5. Sends screenshot to Telegram with [Submit Application] / [Skip Submission] buttons.
6. Submits only after explicit user approval (5-minute timeout → auto-skip).
7. Logs result via `tracker.log_application`.

## Rule

**Never submits a form without Telegram confirmation.**

## Usage

This skill is invoked automatically by `daily_run()` for every approved job whose URL matches `lever.co`. To trigger manually:

```python
import asyncio, applicant
result = asyncio.run(applicant.apply_to_job(job_dict, docs_dict))
# Returns: "submitted" | "skipped_by_user" | "needs_human" | "failed"
```

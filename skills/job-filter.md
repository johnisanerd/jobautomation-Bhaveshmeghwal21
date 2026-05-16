---
name: job-filter
description: Score and rank scraped jobs against the candidate profile and preferences using Azure AI. Returns a sorted list with scores and one-line reasons.
---

Score jobs using Azure AI and the candidate's profile.

## What this skill does

1. Loads `profile.md` and `preferences.md`.
2. Sends all jobs + profile + preferences to Azure AI (GPT-4o deployment).
3. Returns a JSON array with `score (0–100)` and a `reason` per job.
4. Results sorted by score descending.

## Usage

```
/job-filter
```

Pass jobs from `/job-scraper` into this skill.

## Implementation

```python
from filter import score_jobs
scored = score_jobs(jobs)
```

## Output schema

```json
[
  {
    "title": "Data Analyst Intern",
    "company": "Acme Corp",
    "url": "https://...",
    "score": 91,
    "reason": "Entry-level data role matching profile stack and fresher preference",
    "source": "greenhouse"
  }
]
```

## Scoring rules

- 80–100: Excellent match (intern/entry-level, correct domain)
- 60–79: Good match (minor gaps)
- 40–59: Partial match
- 0–39: Poor match or disqualified (senior role, blacklisted, wrong domain)

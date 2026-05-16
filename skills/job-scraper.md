---
name: job-scraper
description: Scrape Indeed and LinkedIn for entry-level and internship jobs matching the candidate's target roles. Returns a deduplicated JSON list of job postings.
---

Scrape job boards for entry-level and internship positions.

## What this skill does

1. Reads `preferences.md` to get target keywords and location.
2. Calls `scraper.scrape_all(keyword, location)` for each keyword.
3. Deduplicates results by URL.
4. Returns a JSON array of job dicts: `{ title, company, url, description, source, posted_date }`.

## Sources

- **Indeed** — `misceres/indeed-scraper` (keyword + location + country)
- **LinkedIn** — `curious_coder/linkedin-jobs-scraper` (URL-based search)

## Usage

```
/job-scraper
/job-scraper keyword="data analyst" location="remote"
```

## Implementation

Run the following from the project root:

```bash
python scraper.py
```

Or from Python:

```python
from scraper import scrape_all
jobs = scrape_all("data analyst intern", "remote")
```

## Output schema

```json
[
  {
    "title": "Data Analyst Intern",
    "company": "Acme Corp",
    "url": "https://boards.greenhouse.io/acme/jobs/123",
    "description": "...",
    "source": "greenhouse",
    "posted_date": "2026-05-01"
  }
]
```

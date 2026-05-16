"""LinkedIn hiring-post discovery pipeline.

Scrapes LinkedIn posts (not job listings) where founders/HRs announce openings,
extracts structured job data via LLM, scores with graph+LLM, and auto-generates
resume+cover letter for every job scoring >= 85.

Run standalone:  python main.py explore-posts

Why separate from tier 1/2/3:
  - Posts are unstructured free text → needs LLM extraction before scoring
  - No apply automation (DM-based or external links, not ATS forms)
  - Posts surface roles 2-7 days before they hit job boards
"""

from __future__ import annotations

import json
import re
from typing import Any

from loguru import logger

import config
import llm

# ── Apify actor ───────────────────────────────────────────────────────────────
# Apify store: https://apify.com/apify/linkedin-post-search-scraper
# If this actor ID changes, update it here.
_POSTS_ACTOR_ID = "apify/linkedin-post-search-scraper"

# Search query templates — {keyword} is substituted at runtime.
# Two kinds:
#   keyed  → {keyword} placeholder, run once per keyword from preferences.md
#   fixed  → no placeholder, run once per explore-posts call (hashtag/phrase sweeps)
_QUERY_TEMPLATES: list[str] = [
    # ── Keyword-specific (run per keyword) ───────────────────────────────────
    'hiring "{keyword}" India',
    '"we are hiring" "{keyword}" India',
    '"looking for" "{keyword}" India',
    '"join our team" "{keyword}" India',
    '"open role" "{keyword}" India',
    '"immediate opening" "{keyword}" India',
    '"urgent hiring" "{keyword}" India',
    '"{keyword}" intern India "apply"',
    '"{keyword}" fresher India hiring',
    '"{keyword}" "0-1 years" India',
    '"{keyword}" "0-2 years" India',
    '"{keyword}" "entry level" India',
    '"{keyword}" "DM me" India',
    '"{keyword}" "link in bio" India',
    '"{keyword}" "link in comments" India',
]

# Fixed hashtag/phrase sweeps — run once regardless of keyword.
#
# Strategy (from LinkedIn data):
#   - #Hiring has 2.4M+ followers — always the primary anchor
#   - Combine core recruiter tags with role + India/location tags
#   - Never use candidate tags (#OpenToWork, #JobSearch) — those surface seekers, not postings
#
_FIXED_QUERIES: list[str] = [

    # ── Core: #hiring (highest volume — 2.4M followers) ──────────────────────
    "#hiring #dataanalyst India",
    "#hiring #dataanalytics India",
    "#hiring #dataanalysis India",
    "#hiring #analytics India",
    "#hiring #datascience #India",
    "#hiring #datasciencejobs India",
    "#hiring #dataengineer India",
    "#hiring #dataengineering India",
    "#hiring #productmanager India",
    "#hiring #productmanagement India",
    "#hiring #productanalyst India",
    "#hiring #businessanalyst India",
    "#hiring #businessanalysis India",
    "#hiring #growthanalyst India",
    "#hiring #sql India",
    "#hiring #python India",
    "#hiring #powerbi India",
    "#hiring #tableau India",
    "#hiring #machinelearning India",
    "#hiring #artificialintelligence India",
    "#hiring #apm India",
    "#hiring #bi India",

    # ── Core: #nowhiring ──────────────────────────────────────────────────────
    "#nowhiring #dataanalyst India",
    "#nowhiring #dataanalytics India",
    "#nowhiring #datascience India",
    "#nowhiring #dataengineer India",
    "#nowhiring #productmanager India",
    "#nowhiring #businessanalyst India",
    "#nowhiring #analytics India",
    "#nowhiring #python India",

    # ── Core: #wearehiring ────────────────────────────────────────────────────
    "#wearehiring #dataanalyst India",
    "#wearehiring #dataanalytics India",
    "#wearehiring #datascience India",
    "#wearehiring #dataengineer India",
    "#wearehiring #productmanager India",
    "#wearehiring #businessanalyst India",
    "#wearehiring #analytics India",
    "#wearehiring #python India",
    "#wearehiring #sql India",

    # ── Core: #hiringalert (fast-moving, urgent posts) ────────────────────────
    "#hiringalert #dataanalyst India",
    "#hiringalert #analytics India",
    "#hiringalert #datascience India",
    "#hiringalert #dataengineer India",
    "#hiringalert #productmanager India",
    "#hiringalert #businessanalyst India",

    # ── Core: #jobopening ─────────────────────────────────────────────────────
    "#jobopening #dataanalyst India",
    "#jobopening #analytics India",
    "#jobopening #dataengineer India",
    "#jobopening #productmanager India",
    "#jobopening #businessanalyst India",
    "#jobopening #datascience India",

    # ── Domain: #techjobs / #itjobs ───────────────────────────────────────────
    "#techjobs #dataanalyst India",
    "#techjobs #analytics India",
    "#techjobs #datascience India",
    "#techjobs #dataengineer India",
    "#itjobs #dataanalyst India",
    "#itjobs #analytics India",
    "#itjobs #python India",

    # ── Domain: #startupshiring (startup ecosystem — no formal JDs) ───────────
    "#startupshiring #data India",
    "#startupshiring #analytics India",
    "#startupshiring #productmanager India",
    "#startupshiring #dataengineer India",
    "#startupshiring #businessanalyst India",

    # ── Location: #hiringindia ────────────────────────────────────────────────
    "#hiringindia #dataanalyst",
    "#hiringindia #dataanalytics",
    "#hiringindia #datascience",
    "#hiringindia #dataengineer",
    "#hiringindia #productmanager",
    "#hiringindia #businessanalyst",
    "#hiringindia #analytics",
    "#hiringindia #python",

    # ── Location: #bangalorehiring (India's largest tech hub) ─────────────────
    "#bangalorehiring #dataanalyst",
    "#bangalorehiring #dataanalytics",
    "#bangalorehiring #datascience",
    "#bangalorehiring #dataengineer",
    "#bangalorehiring #productmanager",
    "#bangalorehiring #analytics",

    # ── Modality: #remotework / #wfh ─────────────────────────────────────────
    "#hiring #remotework #dataanalyst India",
    "#hiring #remotework #dataengineer India",
    "#hiring #remotework #analytics India",
    "#hiring #wfh #dataanalyst India",
    "#hiring #wfh #productmanager India",

    # ── Fresher / entry-level ─────────────────────────────────────────────────
    "#hiring #freshers #dataanalyst India",
    "#hiring #freshers #analytics India",
    "#hiring #freshers #datascience India",
    "#hiring #entrylevel #data India",
    "#hiring #entrylevel #analytics India",
    "#hiring #entrylevel #productmanager India",
    "#freshers #dataanalyst #hiring India",
    "#freshersjobs #dataanalytics India",
    "#fresherhiring #data India",
    "#campushiring #data India",
    "#campushiring #analytics India",
    "#campusrecruitment #dataanalyst India",

    # ── Internship ────────────────────────────────────────────────────────────
    "#hiring #internship #dataanalyst India",
    "#hiring #internship #analytics India",
    "#hiring #internship #productmanager India",
    "#hiring #internship #businessanalyst India",
    "#hiring #internship #dataengineer India",
    "#internship #dataanalyst India",
    "#internship #dataanalytics India",
    "#internship #datascience India",
    "#internship #productmanagement India",
    "#internship #businessanalyst India",

    # ── Founder/HR post phrases (combine with core tags) ──────────────────────
    '#hiring "data analyst" India "DM"',
    '#hiring "business analyst" India "DM"',
    '#hiring "product manager" India "DM"',
    '#hiring "data engineer" India "DM"',
    '"link in comments" #hiring data India',
    '"link in bio" #hiring analyst India',
    '"drop your resume" #hiring India',
    '"apply now" #hiring #dataanalyst India',

    # ── Role-specific text + #hiring ──────────────────────────────────────────
    '#hiring "data analyst intern" India',
    '#hiring "business analyst intern" India',
    '#hiring "product intern" India',
    '#hiring "associate product manager" India',
    '#hiring "growth analyst" India',
    '#hiring "analytics engineer" India',
    '#hiring "junior data analyst" India',
]

_MAX_POSTS_PER_QUERY = 20   # Apify credits budget per query (fixed sweeps add volume)


# ── Scraping ──────────────────────────────────────────────────────────────────

def _run_query(client: Any, query: str, seen_urls: set[str]) -> list[dict[str, Any]]:
    """Run a single Apify post-search query and return new (not-yet-seen) results."""
    logger.info(f"[POSTS] Querying: {query!r}")
    try:
        run = client.actor(_POSTS_ACTOR_ID).call(run_input={
            "searchQuery": query,
            "maxResults": _MAX_POSTS_PER_QUERY,
            "proxy": {"useApifyProxy": True, "apifyProxyGroups": ["RESIDENTIAL"]},
        })
        items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
        fresh = []
        for item in items:
            post_url = item.get("url") or item.get("postUrl") or ""
            if post_url and post_url not in seen_urls:
                seen_urls.add(post_url)
                fresh.append(item)
        logger.info(f"[POSTS] {len(fresh)} new posts from: {query!r}")
        return fresh
    except Exception as exc:
        logger.warning(f"[POSTS] Query failed ({query!r}): {exc}")
        return []


def scrape_posts_for_keyword(
    keyword: str,
    client: Any,
    seen_urls: set[str],
) -> list[dict[str, Any]]:
    """Run all keyword-specific query templates for one keyword."""
    results = []
    for template in _QUERY_TEMPLATES:
        query = template.format(keyword=keyword)
        results.extend(_run_query(client, query, seen_urls))
    return results


def scrape_fixed_queries(client: Any, seen_urls: set[str]) -> list[dict[str, Any]]:
    """Run all fixed hashtag/phrase sweeps (once per explore-posts call)."""
    results = []
    logger.info(f"[POSTS] Running {len(_FIXED_QUERIES)} fixed hashtag/phrase queries...")
    for query in _FIXED_QUERIES:
        results.extend(_run_query(client, query, seen_urls))
    return results


def scrape_all_keywords(keywords: list[str]) -> list[dict[str, Any]]:
    """Scrape LinkedIn posts for all keywords + fixed sweeps, dedup by post URL."""
    try:
        from apify_client import ApifyClient
    except ImportError:
        logger.error("[POSTS] apify_client not installed — pip install apify-client")
        return []

    if not getattr(config, "APIFY_API_KEY", ""):
        logger.error("[POSTS] APIFY_API_KEY not set in .env")
        return []

    client = ApifyClient(config.APIFY_API_KEY)
    seen_urls: set[str] = set()
    all_raw: list[dict[str, Any]] = []

    # Keyword-specific queries
    for kw in keywords:
        all_raw.extend(scrape_posts_for_keyword(kw, client, seen_urls))

    # Fixed hashtag/phrase sweeps (run once, not per keyword)
    all_raw.extend(scrape_fixed_queries(client, seen_urls))

    logger.info(f"[POSTS] Total unique posts scraped: {len(all_raw)}")
    return all_raw


# ── LLM extraction — raw posts → structured job dicts ─────────────────────────

_EXTRACT_SYSTEM = (
    "You are a hiring-post parser. Given LinkedIn post texts, identify which are genuine "
    "job/internship postings and extract structured data from them. "
    "Ignore thought-leadership posts, personal updates, and promotional content with no specific role."
)

_EXTRACT_TEMPLATE = """Analyze these LinkedIn posts and extract job data. Return a JSON array — one object per post, in the same order.

For each post return:
{{
  "is_job": true | false,
  "title": "<role title, e.g. Data Analyst Intern>",
  "company": "<company name, empty string if unclear>",
  "location": "<city / Remote / India — default India>",
  "apply_url": "<direct apply URL found in post text, else empty string>",
  "description": "<key requirements extracted from post, max 300 chars>",
  "post_author": "<author name if visible, else empty>"
}}

Set is_job=false for posts that are NOT hiring announcements.
Return ONLY the JSON array, no markdown.

Posts:
{posts_text}
"""


def _extract_jobs_from_posts(
    raw_posts: list[dict[str, Any]],
    batch_size: int = 10,
) -> list[dict[str, Any]]:
    """LLM-extract structured job data from raw post dicts.

    Batches posts to stay within context limits. Returns only posts where
    is_job=true, converted to standard job dict schema.
    """
    structured: list[dict[str, Any]] = []

    for i in range(0, len(raw_posts), batch_size):
        batch = raw_posts[i: i + batch_size]
        batch_num = i // batch_size + 1
        total_batches = (len(raw_posts) + batch_size - 1) // batch_size
        logger.info(f"[POSTS] Extracting batch {batch_num}/{total_batches} ({len(batch)} posts)...")

        # Build numbered post list for the prompt
        posts_text_parts = []
        for idx, post in enumerate(batch):
            text = (
                post.get("text") or post.get("content") or
                post.get("postText") or post.get("description") or ""
            )[:800]
            author = post.get("author") or post.get("authorName") or ""
            posts_text_parts.append(f"[{idx+1}] Author: {author}\n{text}")

        posts_text = "\n\n---\n\n".join(posts_text_parts)
        prompt = _EXTRACT_TEMPLATE.format(posts_text=posts_text)

        try:
            raw_reply = llm.chat_short([
                {"role": "system", "content": _EXTRACT_SYSTEM},
                {"role": "user", "content": prompt},
            ])
            cleaned = re.sub(r"```(?:json)?", "", raw_reply).strip().strip("`").strip()
            parsed: list[dict] = json.loads(cleaned)
        except Exception as exc:
            logger.warning(f"[POSTS] Extraction batch {batch_num} failed: {exc}")
            continue

        for idx, extracted in enumerate(parsed):
            if not extracted.get("is_job"):
                continue

            raw = batch[idx] if idx < len(batch) else {}
            post_url = raw.get("url") or raw.get("postUrl") or ""
            apply_url = extracted.get("apply_url") or ""

            # Prefer an explicit apply URL from post text; fall back to post URL so
            # the user can visit the post and DM/apply from there.
            job_url = apply_url if apply_url.startswith("http") else post_url

            if not job_url:
                continue

            title = extracted.get("title", "").strip()
            company = extracted.get("company", "").strip()
            if not title:
                continue

            structured.append({
                "title": title,
                "company": company or extracted.get("post_author", "Unknown"),
                "location": extracted.get("location") or "India",
                "url": job_url,
                "post_url": post_url,
                "description": extracted.get("description", ""),
                "source": "linkedin_posts",
                "posted_date": raw.get("postedAt") or raw.get("date") or "",
                "apply_note": (
                    "DM author on LinkedIn" if not apply_url else ""
                ),
            })

    logger.info(f"[POSTS] Extracted {len(structured)} job posts from raw posts")
    return structured


# ── Public entry point ────────────────────────────────────────────────────────

def discover(keywords: list[str]) -> list[dict[str, Any]]:
    """Full discovery pipeline: scrape → LLM-extract → return structured job dicts.

    Args:
        keywords: List of role keywords to search for.

    Returns:
        List of structured job dicts ready for scoring.
    """
    raw = scrape_all_keywords(keywords)
    if not raw:
        logger.warning("[POSTS] No raw posts returned from Apify.")
        return []

    jobs = _extract_jobs_from_posts(raw)

    # Dedup by url
    seen: set[str] = set()
    unique = []
    for j in jobs:
        key = j.get("url", "").strip().lower()
        if key and key not in seen:
            seen.add(key)
            unique.append(j)

    logger.info(f"[POSTS] {len(unique)} unique job posts after dedup")
    return unique

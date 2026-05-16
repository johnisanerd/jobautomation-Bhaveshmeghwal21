"""Orchestrator — pipeline runners and CLI entry points."""

import asyncio
import json
import re
import sys
from pathlib import Path
from loguru import logger
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import config
import scraper
import company_careers
import filter as job_filter
import generator
import applicant
import referral
import tracker
import notifier
import db_supabase


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_preferences_keywords() -> list[str]:
    """Extract all keywords from the '## Target Job Keywords' section of preferences.md.

    Returns:
        List of keyword strings, preserving order.
    """
    text = config.PREFERENCES_PATH.read_text(encoding="utf-8")
    keywords: list[str] = []
    in_section = False
    for line in text.splitlines():
        if line.strip() == "## Target Job Keywords":
            in_section = True
            continue
        if in_section:
            if line.startswith("##"):
                break
            stripped = line.strip()
            if stripped.startswith("- "):
                kw = stripped[2:].strip()
                if kw:
                    keywords.append(kw)
    return keywords


def _score_in_batches(
    jobs: list[dict],
    batch_size: int = 30,
    checkpoint_path: Path | None = None,
) -> list[dict]:
    """Score jobs via the LLM in fixed-size batches, return sorted by score.

    Supports resuming an interrupted run: if checkpoint_path exists it loads
    already-scored jobs and skips their URLs, then picks up where it left off.
    Progress is saved after every batch so a crash only loses one batch.

    Args:
        jobs: Full list of job dicts to score.
        batch_size: Number of jobs per LLM call.
        checkpoint_path: Optional path for per-batch progress saves.

    Returns:
        All scored jobs sorted by score descending.
    """
    all_scored: list[dict] = []
    scored_urls: set[str] = set()

    if checkpoint_path and checkpoint_path.exists():
        try:
            saved = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            all_scored = saved
            scored_urls = {j.get("url", "") for j in saved}
            logger.info(f"[MAIN] Checkpoint loaded — {len(all_scored)} jobs already scored, resuming.")
        except Exception as exc:
            logger.warning(f"[MAIN] Could not load checkpoint: {exc}")

    remaining = [j for j in jobs if j.get("url", "") not in scored_urls]
    total_batches = (len(remaining) + batch_size - 1) // batch_size

    for i in range(0, len(remaining), batch_size):
        batch = remaining[i : i + batch_size]
        batch_num = i // batch_size + 1
        logger.info(f"[MAIN] Scoring batch {batch_num}/{total_batches} ({len(batch)} jobs)...")
        all_scored.extend(job_filter.score_jobs(batch))

        if checkpoint_path:
            try:
                checkpoint_path.write_text(
                    json.dumps(all_scored, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except Exception as exc:
                logger.warning(f"[MAIN] Checkpoint save failed: {exc}")

    all_scored.sort(key=lambda x: x.get("score", 0), reverse=True)
    return all_scored


def _merge_dedup(jobs_per_kw: list[list[dict]]) -> list[dict]:
    """Merge per-keyword job lists and deduplicate by URL.

    Args:
        jobs_per_kw: List of per-keyword job lists.

    Returns:
        Flat deduplicated list.
    """
    seen: set[str] = set()
    unique: list[dict] = []
    for jobs in jobs_per_kw:
        for job in jobs:
            key = (job.get("url") or "").strip().lower()
            if key and key not in seen:
                seen.add(key)
                unique.append(job)
    return unique


def _save_results(jobs: list[dict], path: Path) -> None:
    """Save a list of scored jobs to a JSON file."""
    path.write_text(json.dumps(jobs, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"[MAIN] Saved {len(jobs)} jobs to {path}")


# Matches explicit non-India/non-remote geographic mentions in job location fields.
_NON_INDIA_LOC_RE = re.compile(
    r"\b(united states|usa|u\.s\.a?\.?|new york|san francisco|bay area|seattle|"
    r"austin|chicago|boston|denver|los angeles|london|uk|united kingdom|"
    r"australia|canada|germany|france|paris|berlin|singapore|dubai|uae|"
    r"europe|european union|netherlands|amsterdam|sweden|poland|czech)\b",
    re.IGNORECASE,
)


def _is_india_or_remote(job: dict) -> bool:
    """Return True if a job is in India, remote, or has no/ambiguous location."""
    loc = (job.get("location") or "").strip()
    if not loc:
        return True
    return not _NON_INDIA_LOC_RE.search(loc)


async def _autogen_high_score_docs(scored: list[dict]) -> int:
    """Auto-generate resume + cover letter for jobs scoring >= 90 during explore.

    Skips jobs whose docs already exist. Sends a Telegram alert for each newly
    generated set. Returns the count of newly generated doc sets.
    """
    high = [j for j in scored if (j.get("score") or 0) >= 90]
    if not high:
        return 0

    generated = 0
    for job in high:
        out_dir = generator.get_output_dir(job)
        if (out_dir / "resume.pdf").exists():
            logger.info(f"[MAIN] Auto-gen skipped (docs exist): {job.get('title')} @ {job.get('company')}")
            continue
        try:
            logger.info(f"[MAIN] Auto-generating docs: {job.get('title')} @ {job.get('company')} (score {job.get('score')})")
            docs = await asyncio.to_thread(generator.generate_docs, job)
            generated += 1

            # Upload to Supabase Storage if configured
            if db_supabase.is_configured():
                try:
                    supa_job = await asyncio.to_thread(db_supabase.get_job_by_url, job.get("url", ""))
                    if supa_job and supa_job.get("id"):
                        slug = f"{job.get('company', 'company')}-{job.get('title', 'role')}"
                        slug = slug.lower().replace(" ", "-")[:80]
                        resume_url = await asyncio.to_thread(
                            db_supabase.upload_pdf,
                            Path(docs["resume_pdf"]),
                            f"{slug}/resume.pdf",
                        )
                        cover_url = await asyncio.to_thread(
                            db_supabase.upload_pdf,
                            Path(docs["cover_letter_pdf"]),
                            f"{slug}/cover-letter.pdf",
                        )
                        await asyncio.to_thread(
                            db_supabase.save_resume, supa_job["id"], resume_url, cover_url
                        )
                except Exception as sup_exc:
                    logger.warning(f"[MAIN] Supabase upload skipped: {sup_exc}")

            try:
                await notifier.send_message(
                    f"Auto-generated resume for *{job.get('title')}* @ {job.get('company')}\n"
                    f"Score: {job.get('score')}/100\n{job.get('url', '')}"
                )
            except Exception:
                pass
        except Exception as exc:
            logger.error(f"[MAIN] Auto-gen failed for {job.get('title')} @ {job.get('company')}: {exc}")

    return generated


# ── Pipeline runners (async) ──────────────────────────────────────────────────

async def daily_run() -> None:
    """Daily delta pipeline — Tier 1 only, today's postings, India mode.

    Steps:
      1. Scrape Tier 1 boards for each keyword (date filter: today)
      2. Score and rank via Azure AI
      3. Send top-10 digest to Telegram
      4. Send approval requests for top-5
      5. Generate resume + cover letter for approved jobs
      6. Log to Google Sheets (Phase 5 stub)
    """
    logger.info("[MAIN] ===== Daily run started (Tier 1, today) =====")

    followups = tracker.get_followups_due()
    if followups:
        await notifier.send_followup_reminders(followups)
        logger.info(f"[MAIN] Sent {len(followups)} application follow-up reminder(s).")

    referral_followups = tracker.get_referral_followups_due()
    if referral_followups:
        await notifier.send_referral_followup_reminders(referral_followups)
        logger.info(f"[MAIN] Sent {len(referral_followups)} referral follow-up reminder(s).")

    keywords = [
        "data analyst intern",
        "data engineer intern",
        "product manager intern",
        "junior data analyst",
        "entry level data engineer",
    ]

    per_kw: list[list[dict]] = []
    for keyword in keywords:
        jobs = await asyncio.to_thread(
            scraper.scrape_all,
            keyword,
            date_filter="today",
            naukri_limit=30,
            include_tier2=False,
            include_tier3=False,
        )
        per_kw.append(jobs)

    all_jobs = _merge_dedup(per_kw)

    if not all_jobs:
        logger.warning("[MAIN] No jobs found today.")
        await notifier.send_message("No jobs found today. Will try again tomorrow.")
        return

    logger.info(f"[MAIN] Total unique jobs scraped: {len(all_jobs)}")

    scored_jobs = await asyncio.to_thread(job_filter.score_jobs, all_jobs)

    await asyncio.to_thread(tracker.log_scored_jobs, scored_jobs)

    await notifier.send_job_digest(scored_jobs[:10])
    logger.info("[MAIN] Digest sent. Waiting for approvals...")
    for job in scored_jobs[:10]:
        await asyncio.to_thread(tracker.update_job_status, job.get("url", ""), "digest_sent")

    approved_jobs: list[dict] = []
    for job in scored_jobs[:5]:
        await asyncio.to_thread(tracker.update_job_status, job.get("url", ""), "approval_pending")
        decision = await notifier.send_approval_request(job)
        if decision == "approved":
            approved_jobs.append(job)
            await asyncio.to_thread(tracker.update_job_status, job.get("url", ""), "approved")
            logger.info(f"[MAIN] Approved: {job.get('title')} @ {job.get('company')}")
        else:
            await asyncio.to_thread(tracker.update_job_status, job.get("url", ""), "skipped")
            logger.info(f"[MAIN] Skipped: {job.get('title')} @ {job.get('company')}")

    if not approved_jobs:
        await notifier.send_message("No jobs approved. Nothing to apply to today.")
        logger.info("[MAIN] No approvals. Run complete.")
        return

    _APPLY_STATUS_MAP = {
        "submitted":      "applied",
        "filled_skipped": "skipped",
        "login_required": "login_required",
        "job_expired":    "expired",
        "no_form":        "no_form",
        "aggregator":     "aggregator",
        "submit_failed":  "apply_failed",
        "needs_human":    "needs_manual_apply",
        "error":          "apply_failed",
        "failed":         "apply_failed",
    }
    _APPLY_MESSAGES = {
        "filled_skipped": "You skipped submission — apply manually if needed:\n{url}",
        "login_required": "Login required — run `python main.py login` then retry:\n{url}",
        "job_expired":    "Job is closed/expired — no longer accepting applications.",
        "no_form":        "No application form found — check the URL manually:\n{url}",
        "aggregator":     "Aggregator URL — open the job and apply on the company site:\n{url}",
        "submit_failed":  "Form was filled but submit failed — please submit manually:\n{url}",
        "error":          "Unexpected error — check logs and apply manually.",
        "failed":         "Apply failed — check logs and apply manually.",
    }
    submitted_count = skipped_count = login_count = expired_count = 0
    no_form_count = aggregator_count = manual_count = error_count = 0
    for job in approved_jobs:
        title = job.get("title", "?")
        company = job.get("company", "?")
        docs = generator.generate_docs(job)
        apply_result = await applicant.apply_to_job(job, docs)
        sheet_status = _APPLY_STATUS_MAP.get(apply_result, apply_result)
        tracker.log_application(job, {**docs, "apply_result": apply_result})
        tracker.update_job_status(job.get("url", ""), sheet_status)
        msg = _APPLY_MESSAGES.get(apply_result)
        if msg:
            await notifier.send_message(f"*{title}* @ {company}\n{msg.format(url=job.get('url',''))}")
        if apply_result == "submitted":
            submitted_count += 1
        elif apply_result == "filled_skipped":
            skipped_count += 1
        elif apply_result == "login_required":
            login_count += 1
        elif apply_result == "job_expired":
            expired_count += 1
        elif apply_result == "no_form":
            no_form_count += 1
        elif apply_result == "aggregator":
            aggregator_count += 1
        elif apply_result == "needs_human":
            manual_count += 1
        else:
            error_count += 1
        logger.info(f"[MAIN] Apply result={apply_result} ({sheet_status}): {title} @ {company}")

    await notifier.send_message(
        f"*Daily run complete*\n"
        f"Approved: {len(approved_jobs)}\n"
        f"Submitted: {submitted_count} | Skipped by you: {skipped_count}\n"
        f"Needs manual submit: {manual_count} | Login required: {login_count}\n"
        f"Job expired: {expired_count} | No form: {no_form_count} | "
        f"Aggregator: {aggregator_count} | Errors: {error_count}"
    )
    logger.info("[MAIN] ===== Daily run complete =====")


async def explore_run() -> None:
    """Explore crawl — Tier 1 + Tier 2, all keywords, no date filter.

    Reads every keyword from preferences.md, scrapes all Tier 1+2 India boards,
    deduplicates, scores in batches of 30, saves to explore_results.json,
    and sends a Telegram summary + top-20 digest.
    """
    logger.info("[MAIN] ===== Explore run (Tier 1+2) started =====")

    keywords = _parse_preferences_keywords()
    if not keywords:
        await notifier.send_message("Explore aborted: no keywords in preferences.md.")
        return

    logger.info(f"[MAIN] {len(keywords)} keywords — {keywords}")

    per_kw: list[list[dict]] = []
    for keyword in keywords:
        jobs = await asyncio.to_thread(
            scraper.scrape_all, keyword, include_tier2=True, include_tier3=False
        )
        per_kw.append(jobs)

    all_jobs = _merge_dedup(per_kw)
    sources = sorted({j.get("source", "?") for j in all_jobs})
    logger.info(f"[SCRAPER] Explore complete: {len(all_jobs)} unique jobs from {', '.join(sources)}")

    if not all_jobs:
        await notifier.send_message("Explore complete — no jobs found.")
        return

    raw_path = Path(config.BASE_DIR) / "explore_raw.json"
    _save_results(all_jobs, raw_path)

    checkpoint_path = Path(config.BASE_DIR) / "explore_checkpoint.json"
    scored = await asyncio.to_thread(
        _score_in_batches, all_jobs, checkpoint_path=checkpoint_path
    )
    results_path = Path(config.BASE_DIR) / "explore_results.json"
    _save_results(scored, results_path)
    checkpoint_path.unlink(missing_ok=True)

    inserted = await asyncio.to_thread(tracker.log_explore_jobs, scored)
    logger.info(f"[MAIN] Explore Jobs tab: {inserted} new rows logged.")

    await asyncio.to_thread(db_supabase.log_jobs, scored, "tier1")

    autogen_count = await _autogen_high_score_docs(scored)
    top_score = scored[0].get("score", 0) if scored else 0
    await notifier.send_message(
        f"Explore (Tier 1+2) done.\n"
        f"{len(all_jobs)} unique jobs from {len(sources)} sources.\n"
        f"Top score: {top_score}/100. Auto-generated docs for {autogen_count} jobs (score ≥90).\nTop 20 below:"
    )
    await notifier.send_job_digest(scored[:20])
    logger.info("[MAIN] ===== Explore run complete =====")


async def explore_tier2_run() -> None:
    """Explore crawl — Tier 2 only, all keywords, no date filter.

    Reads every keyword from preferences.md, scrapes all 14 Tier 2 India boards,
    deduplicates, scores in batches of 30, saves to explore_tier2_results.json,
    and sends a Telegram summary + top-20 digest.
    """
    logger.info("[MAIN] ===== Explore run (Tier 2 only) started =====")

    keywords = _parse_preferences_keywords()
    if not keywords:
        await notifier.send_message("Explore aborted: no keywords in preferences.md.")
        return

    logger.info(f"[MAIN] {len(keywords)} keywords — {keywords}")

    per_kw: list[list[dict]] = []
    for keyword in keywords:
        jobs = await asyncio.to_thread(
            scraper.scrape_all, keyword, include_tier1=False, include_tier2=True, include_tier3=False
        )
        per_kw.append(jobs)

    all_jobs = _merge_dedup(per_kw)
    sources = sorted({j.get("source", "?") for j in all_jobs})
    logger.info(f"[SCRAPER] Tier 2 explore complete: {len(all_jobs)} unique jobs from {', '.join(sources)}")

    if not all_jobs:
        await notifier.send_message("Tier 2 explore complete — no jobs found.")
        return

    raw_path = Path(config.BASE_DIR) / "explore_tier2_raw.json"
    _save_results(all_jobs, raw_path)

    checkpoint_path = Path(config.BASE_DIR) / "explore_tier2_checkpoint.json"
    scored = await asyncio.to_thread(
        _score_in_batches, all_jobs, checkpoint_path=checkpoint_path
    )
    results_path = Path(config.BASE_DIR) / "explore_tier2_results.json"
    _save_results(scored, results_path)
    checkpoint_path.unlink(missing_ok=True)

    inserted = await asyncio.to_thread(tracker.log_tier2_jobs, scored)
    logger.info(f"[MAIN] Tier 2 Jobs tab: {inserted} new rows logged.")

    await asyncio.to_thread(db_supabase.log_jobs, scored, "tier2")

    autogen_count = await _autogen_high_score_docs(scored)
    top_score = scored[0].get("score", 0) if scored else 0
    await notifier.send_message(
        f"Explore (Tier 2 only) done.\n"
        f"{len(all_jobs)} unique jobs from {len(sources)} sources.\n"
        f"Top score: {top_score}/100. Auto-generated docs for {autogen_count} jobs (score ≥90).\nTop 20 below:"
    )
    await notifier.send_job_digest(scored[:20])
    logger.info("[MAIN] ===== Tier 2 explore complete =====")


async def explore_tier3_run() -> None:
    """Explore Tier 3 only — company career pages from careers_config.json.

    Scrapes all 27 configured company career pages, filters by preferences keywords,
    scores, saves to explore_tier3_results.json, and sends a Telegram digest.
    """
    logger.info("[MAIN] ===== Explore Tier 3 (company career pages) started =====")

    keywords = _parse_preferences_keywords()
    keyword_str = " ".join(keywords)  # broad match for post-filtering

    all_jobs: list[dict] = []
    for keyword in keywords:
        jobs = await asyncio.to_thread(
            company_careers.scrape_all_companies, filter_keyword=keyword
        )
        all_jobs.extend(jobs)

    # Deduplicate across keywords
    seen: set[str] = set()
    unique: list[dict] = []
    for job in all_jobs:
        key = (job.get("url") or "").strip().lower()
        if key and key not in seen:
            seen.add(key)
            unique.append(job)

    companies_hit = sorted({j.get("source", "?") for j in unique})
    logger.info(f"[TIER3] {len(unique)} unique jobs from {len(companies_hit)} companies")

    # Drop jobs with explicitly non-India/non-remote locations (e.g. US/UK MNC listings)
    india_jobs = [j for j in unique if _is_india_or_remote(j)]
    dropped = len(unique) - len(india_jobs)
    if dropped:
        logger.info(f"[TIER3] Dropped {dropped} non-India/non-remote jobs")
    unique = india_jobs

    if not unique:
        await notifier.send_message("Tier 3 explore complete — no India/remote jobs found on career pages.")
        return

    raw_path = Path(config.BASE_DIR) / "explore_tier3_raw.json"
    _save_results(unique, raw_path)

    checkpoint_path = Path(config.BASE_DIR) / "explore_tier3_checkpoint.json"
    scored = await asyncio.to_thread(
        _score_in_batches, unique, checkpoint_path=checkpoint_path
    )
    results_path = Path(config.BASE_DIR) / "explore_tier3_results.json"
    _save_results(scored, results_path)
    checkpoint_path.unlink(missing_ok=True)

    try:
        inserted = await asyncio.to_thread(tracker.log_tier3_jobs, scored)
        logger.info(f"[MAIN] Company Jobs sheet: {inserted} new rows logged.")
        sheet_msg = f"Saved {inserted} new rows to 'Company Jobs' sheet."
    except Exception as exc:
        logger.error(f"[MAIN] Google Sheets save failed: {exc}")
        sheet_msg = f"WARNING: Google Sheets save failed — {exc}"

    await asyncio.to_thread(db_supabase.log_jobs, scored, "tier3")

    autogen_count = await _autogen_high_score_docs(scored)
    top_score = scored[0].get("score", 0) if scored else 0
    await notifier.send_message(
        f"Tier 3 (company pages) explore done.\n"
        f"{len(unique)} India/remote jobs from {len(companies_hit)} companies ({dropped} non-India dropped).\n"
        f"Top score: {top_score}/100. {sheet_msg}\n"
        f"Auto-generated docs for {autogen_count} jobs (score ≥90).\nTop 20 below:"
    )
    await notifier.send_job_digest(scored[:20])
    logger.info("[MAIN] ===== Tier 3 explore complete =====")


async def referral_outreach_run() -> None:
    """LinkedIn referral outreach — search employees at all configured companies and send
    personalized connection requests (max 20 per day, LinkedIn's daily limit).

    Searches three query types per company:
      - Recruiters ("recruiter {company}")
      - Relevant roles ("data analyst {company}")
      - DTU alumni ("Delhi Technological University {company}")

    Deduplicates against the Referrals Google Sheet to avoid re-messaging anyone.
    Sends a Telegram summary when done.
    """
    logger.info("[MAIN] ===== Referral outreach started =====")
    stats = await referral.run_outreach(limit=20)
    await notifier.send_referral_summary(stats)
    logger.info(
        f"[MAIN] ===== Referral outreach complete — "
        f"sent={stats['sent']} skipped={stats['skipped']} failed={stats['failed']} ====="
    )


async def explore_all_run() -> None:
    """Full explore crawl — Tier 1 + Tier 2 + Tier 3, all keywords, no date filter.

    The most comprehensive single run. Takes 30-60 minutes.
    Saves to explore_all_results.json and sends top-25 digest.
    """
    logger.info("[MAIN] ===== Explore ALL (Tier 1+2+3) started =====")

    keywords = _parse_preferences_keywords()
    if not keywords:
        await notifier.send_message("Explore-all aborted: no keywords in preferences.md.")
        return

    logger.info(f"[MAIN] {len(keywords)} keywords — {keywords}")

    per_kw: list[list[dict]] = []
    for keyword in keywords:
        jobs = await asyncio.to_thread(
            scraper.scrape_all, keyword, include_tier2=True, include_tier3=True
        )
        per_kw.append(jobs)

    all_jobs = _merge_dedup(per_kw)
    sources = sorted({j.get("source", "?") for j in all_jobs})
    logger.info(
        f"[SCRAPER] Explore-all complete: {len(all_jobs)} unique jobs "
        f"from {len(sources)} sources: {', '.join(sources)}"
    )

    if not all_jobs:
        await notifier.send_message("Explore-all complete — no jobs found.")
        return

    raw_path = Path(config.BASE_DIR) / "explore_all_raw.json"
    _save_results(all_jobs, raw_path)

    checkpoint_path = Path(config.BASE_DIR) / "explore_all_checkpoint.json"
    scored = await asyncio.to_thread(
        _score_in_batches, all_jobs, checkpoint_path=checkpoint_path
    )
    results_path = Path(config.BASE_DIR) / "explore_all_results.json"
    _save_results(scored, results_path)
    checkpoint_path.unlink(missing_ok=True)

    inserted = await asyncio.to_thread(tracker.log_all_jobs, scored)
    logger.info(f"[MAIN] All Jobs tab: {inserted} new rows logged.")

    await asyncio.to_thread(db_supabase.log_jobs, scored, "tier1")

    autogen_count = await _autogen_high_score_docs(scored)
    top_score = scored[0].get("score", 0) if scored else 0
    await notifier.send_message(
        f"Explore-all (Tier 1+2+3) done.\n"
        f"{len(all_jobs)} unique jobs from {len(sources)} sources.\n"
        f"Top score: {top_score}/100. Auto-generated docs for {autogen_count} jobs (score ≥90).\nTop 25 below:"
    )
    await notifier.send_job_digest(scored[:25])
    logger.info("[MAIN] ===== Explore-all complete =====")


async def explore_posts_run() -> None:
    """LinkedIn hiring-posts pipeline — scrape → extract → score → auto-generate docs.

    Searches LinkedIn *posts* (not job listings) for founders/HRs announcing openings.
    Posts surface roles 2-7 days before they hit job boards.

    - LLM extracts structured job data from unstructured post text
    - Graph + LLM two-layer scoring (same as all explore runs)
    - Auto-generates resume + cover letter for every job scoring >= 85
    - Saves to explore_posts_results.json and 'Post Jobs' Google Sheets tab
    - No apply automation — posts use DM/external links, not ATS forms
    """
    import posts_scraper

    keywords = _parse_preferences_keywords()
    if not keywords:
        await notifier.send_message("[POSTS] No keywords found in preferences.md — aborting.")
        return

    await notifier.send_message(
        f"LinkedIn posts explore started.\n"
        f"Searching {len(keywords)} keywords: {', '.join(keywords[:5])}{'...' if len(keywords) > 5 else ''}"
    )
    logger.info("[POSTS] ===== Explore-posts run started =====")

    # ── Step 1: Scrape + LLM-extract ─────────────────────────────────────────
    jobs = await asyncio.to_thread(posts_scraper.discover, keywords)

    if not jobs:
        await notifier.send_message("LinkedIn posts explore done — no hiring posts found.")
        return

    raw_path = Path(config.BASE_DIR) / "explore_posts_raw.json"
    _save_results(jobs, raw_path)

    # ── Step 2: Score (graph + LLM) ──────────────────────────────────────────
    checkpoint_path = Path(config.BASE_DIR) / "explore_posts_checkpoint.json"
    scored = await asyncio.to_thread(
        _score_in_batches, jobs, checkpoint_path=checkpoint_path
    )
    results_path = Path(config.BASE_DIR) / "explore_posts_results.json"
    _save_results(scored, results_path)
    checkpoint_path.unlink(missing_ok=True)

    # ── Step 3: Save to Google Sheets + Supabase ─────────────────────────────
    try:
        inserted = await asyncio.to_thread(tracker.log_post_jobs, scored)
        logger.info(f"[POSTS] 'Post Jobs' sheet: {inserted} new rows logged.")
        sheet_msg = f"Saved {inserted} new rows to 'Post Jobs' sheet."
    except Exception as exc:
        logger.error(f"[POSTS] Sheet save failed: {exc}")
        sheet_msg = f"WARNING: Sheet save failed — {exc}"

    await asyncio.to_thread(db_supabase.log_jobs, scored, "posts")

    # ── Step 4: Auto-generate docs for score >= 85 ───────────────────────────
    high = [j for j in scored if (j.get("score") or 0) >= 85]
    generated = 0
    for job in high:
        out_dir = generator.get_output_dir(job)
        if (out_dir / "resume.pdf").exists():
            logger.info(f"[POSTS] Docs already exist for {job.get('title')} @ {job.get('company')}")
            continue
        try:
            logger.info(
                f"[POSTS] Generating docs: {job.get('title')} @ {job.get('company')} "
                f"(score {job.get('score')}, graph {job.get('graph_overlap', '?')}%)"
            )
            docs = await asyncio.to_thread(generator.generate_docs, job)
            generated += 1

            if db_supabase.is_configured():
                try:
                    supa_job = await asyncio.to_thread(db_supabase.get_job_by_url, job.get("url", ""))
                    if supa_job and supa_job.get("id"):
                        slug = f"{job.get('company', 'company')}-{job.get('title', 'role')}"
                        slug = slug.lower().replace(" ", "-")[:80]
                        resume_url = await asyncio.to_thread(
                            db_supabase.upload_pdf, Path(docs["resume_pdf"]), f"{slug}/resume.pdf"
                        )
                        cover_url = await asyncio.to_thread(
                            db_supabase.upload_pdf, Path(docs["cover_letter_pdf"]), f"{slug}/cover-letter.pdf"
                        )
                        await asyncio.to_thread(
                            db_supabase.save_resume, supa_job["id"], resume_url, cover_url
                        )
                except Exception as sup_exc:
                    logger.warning(f"[POSTS] Supabase upload skipped: {sup_exc}")

            post_url = job.get("post_url") or job.get("url", "")
            apply_note = job.get("apply_note") or "Apply via URL in job"
            try:
                await notifier.send_message(
                    f"Auto-generated docs for *{job.get('title')}* @ {job.get('company')}\n"
                    f"Score: {job.get('score')}/100 | Graph: {job.get('graph_overlap', '?')}%\n"
                    f"{apply_note}\nPost: {post_url}"
                )
            except Exception:
                pass
        except Exception as exc:
            logger.error(f"[POSTS] Doc gen failed for {job.get('title')}: {exc}")

    # ── Step 5: Summary ───────────────────────────────────────────────────────
    top_score = scored[0].get("score", 0) if scored else 0
    await notifier.send_message(
        f"LinkedIn posts explore done.\n"
        f"{len(jobs)} hiring posts → {len(scored)} scored.\n"
        f"Top score: {top_score}/100. {sheet_msg}\n"
        f"Auto-generated docs for {generated}/{len(high)} jobs (score ≥85).\n"
        f"Top posts below:"
    )
    await notifier.send_job_digest(scored[:15])
    logger.info("[POSTS] ===== Explore-posts complete =====")


# ── Async wrappers with Telegram lifecycle ────────────────────────────────────

async def _run_with_telegram(coro) -> None:
    """Start Telegram polling, run a coroutine, then shut down cleanly."""
    app = notifier.build_application()
    async with app:
        # Kill any stale polling session from a previous run before starting our own.
        # delete_webhook with drop_pending_updates forces the old getUpdates connection closed.
        await app.bot.delete_webhook(drop_pending_updates=True)
        await asyncio.sleep(1)
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
        try:
            await coro
        finally:
            await app.updater.stop()
            await app.stop()


async def _run_scheduled_async() -> None:
    """Start Telegram polling and a daily cron scheduler (08:00 AM)."""
    app = notifier.build_application()
    async with app:
        await app.bot.delete_webhook(drop_pending_updates=True)
        await asyncio.sleep(1)
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)

        scheduler = AsyncIOScheduler()
        scheduler.add_job(daily_run, "cron", hour=8, minute=0)
        scheduler.start()
        logger.info("[MAIN] Scheduler started — daily run at 08:00 AM local time.")

        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            logger.info("[MAIN] Stopping...")
        finally:
            scheduler.shutdown(wait=False)
            await app.updater.stop()
            await app.stop()


# ── Sync entry points ─────────────────────────────────────────────────────────

def run_once() -> None:
    """Run the daily Tier 1 pipeline immediately."""
    config.validate()
    asyncio.run(_run_with_telegram(daily_run()))


def run_explore() -> None:
    """Run the Tier 1+2 explore crawl immediately."""
    config.validate()
    asyncio.run(_run_with_telegram(explore_run()))


def run_explore_tier2() -> None:
    """Run the Tier 2-only explore crawl immediately."""
    config.validate()
    asyncio.run(_run_with_telegram(explore_tier2_run()))


def run_explore_tier3() -> None:
    """Run the Tier 3 company career pages crawl immediately."""
    config.validate()
    asyncio.run(_run_with_telegram(explore_tier3_run()))


def run_explore_all() -> None:
    """Run the full Tier 1+2+3 explore crawl immediately."""
    config.validate()
    asyncio.run(_run_with_telegram(explore_all_run()))


def run_explore_posts() -> None:
    """Scrape LinkedIn hiring posts, extract jobs, score, auto-generate docs for score >= 85."""
    config.validate()
    asyncio.run(_run_with_telegram(explore_posts_run()))


def run_referral() -> None:
    """Run LinkedIn referral outreach immediately (max 20 connection requests)."""
    config.validate()
    asyncio.run(_run_with_telegram(referral_outreach_run()))


def load_results() -> None:
    """Push an existing results JSON to Google Sheets.

    explore_tier3_results.json → 'Company Jobs' tab
    explore_all_results.json / explore_results.json → 'Jobs' tab
    """
    config.validate()
    candidates = [
        ("explore_all_results.json",   "All Jobs",      tracker.log_all_jobs),
        ("explore_results.json",       "Explore Jobs",  tracker.log_explore_jobs),
        ("explore_tier2_results.json", "Tier 2 Jobs",   tracker.log_tier2_jobs),
        ("explore_tier3_results.json", "Company Jobs",  tracker.log_tier3_jobs),
    ]
    for filename, tab, log_fn in candidates:
        path = Path(config.BASE_DIR) / filename
        if path.exists():
            jobs = json.loads(path.read_text(encoding="utf-8"))
            logger.info(f"[MAIN] Loading {len(jobs)} jobs from {filename} → '{tab}' tab...")
            inserted = log_fn(jobs)
            logger.info(f"[MAIN] Done — {inserted} new rows inserted into '{tab}'.")
            return
    logger.error("[MAIN] No explore results file found. Run 'explore' or 'explore-all' first.")


def backfill_dates() -> None:
    """Backfill blank Posted Date cells in the Jobs sheet from local raw JSON files.

    Reads explore_raw.json, explore_all_raw.json, and explore_tier3_raw.json,
    builds a URL→posted_date map, then updates any Jobs sheet row where
    Posted Date is currently blank.
    """
    config.validate()

    raw_files = ["explore_all_raw.json", "explore_raw.json", "explore_tier3_raw.json"]
    url_to_date: dict[str, str] = {}

    for filename in raw_files:
        path = Path(config.BASE_DIR) / filename
        if not path.exists():
            continue
        try:
            jobs = json.loads(path.read_text(encoding="utf-8"))
            before = len(url_to_date)
            for job in jobs:
                url = job.get("url", "")
                posted = job.get("posted_date", "")
                if url and posted and url not in url_to_date:
                    url_to_date[url] = posted
            logger.info(f"[MAIN] {filename}: {len(url_to_date) - before} new URL→date mappings loaded.")
        except Exception as exc:
            logger.warning(f"[MAIN] Could not read {filename}: {exc}")

    if not url_to_date:
        logger.error(
            "[MAIN] No posted_date data found in raw files. "
            "Run 'explore' first to generate explore_raw.json."
        )
        return

    logger.info(f"[MAIN] Backfilling dates for up to {len(url_to_date)} URLs in Jobs sheet...")
    updated = tracker.backfill_posted_dates(url_to_date)
    print(f"Done — {updated} cell(s) updated.")


def browser_login() -> None:
    """Open a persistent Chrome window so you can log into job sites manually.

    Logs into the same profile that Browser-Use uses for applying, so sessions
    are saved and reused on every future apply run. Close the browser when done.

    Sites to log into:
      Internshala, Unstop, Freshersworld, HackerEarth, Analytics Vidhya,
      Wellfound, Apna, Cutshort, Instahyre, and any other Tier 2 boards.
    """
    import subprocess
    profile_dir = Path(config.BASE_DIR) / ".browser_profile"
    profile_dir.mkdir(exist_ok=True)

    sites = [
        "https://internshala.com/login",
        "https://unstop.com/login",
        "https://www.freshersworld.com/login",
        "https://www.hackerearth.com/signin",
        "https://www.analyticsvidhya.com/login",
        "https://wellfound.com/login",
        "https://apna.co/login",
        "https://cutshort.io/login",
        "https://www.instahyre.com/login",
    ]

    print("Opening Chrome with your saved profile...")
    print("Log into each site, then close the browser when done.")
    print("Sessions will be saved and reused by Browser-Use automatically.\n")
    for site in sites:
        print(f"  • {site}")

    # Launch Chrome with the persistent profile dir
    chrome_paths = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    chrome = next((p for p in chrome_paths if Path(p).exists()), "chrome")

    subprocess.Popen([
        chrome,
        f"--user-data-dir={profile_dir}",
        "--disable-blink-features=AutomationControlled",
        *sites,
    ])
    print("\nBrowser opened. Log in, then close it. You're done.")


def run_scheduled() -> None:
    """Start APScheduler and run daily_run() at 08:00 AM local time."""
    config.validate()
    asyncio.run(_run_scheduled_async())


# ── Apply pipeline (from existing explore results) ────────────────────────────

async def apply_run(min_score: int = 90, max_score: int = 100, source: str = "") -> None:
    """Generate docs and apply for already-explored jobs within a score range.

    All approval cards are sent upfront. The user can approve/skip in any order
    at any time — there is no timeout. Approved jobs are processed concurrently
    (resume + apply run one at a time via a semaphore to avoid browser conflicts).

    Args:
        min_score: Lower bound of score range (inclusive).
        max_score: Upper bound of score range (inclusive).
        source: Optional results file prefix, e.g. 'tier2', 'tier3', 'all', 'explore'.
                If omitted, auto-detects in priority order.
    """
    logger.info(f"[MAIN] ===== Apply run (score {min_score}–{max_score}) started =====")

    _results_tab_map = {
        "explore_all_results.json":   "All Jobs",
        "explore_results.json":       "Explore Jobs",
        "explore_tier2_results.json": "Tier 2 Jobs",
        "explore_tier3_results.json": "Company Jobs",
    }

    _source_alias = {
        "all":     "explore_all_results.json",
        "explore": "explore_results.json",
        "tier2":   "explore_tier2_results.json",
        "tier3":   "explore_tier3_results.json",
    }

    jobs: list[dict] = []
    source_file = ""

    if source:
        fname = _source_alias.get(source.lower(), source if source.endswith(".json") else f"{source}_results.json")
        path = Path(config.BASE_DIR) / fname
        if path.exists():
            jobs = json.loads(path.read_text(encoding="utf-8"))
            source_file = fname
            logger.info(f"[MAIN] Loaded {len(jobs)} jobs from {fname}")
        else:
            logger.error(f"[MAIN] Source file not found: {path}")
    else:
        for fname in _results_tab_map:
            path = Path(config.BASE_DIR) / fname
            if path.exists():
                jobs = json.loads(path.read_text(encoding="utf-8"))
                source_file = fname
                logger.info(f"[MAIN] Loaded {len(jobs)} jobs from {fname}")
                break

    jobs_tab = _results_tab_map.get(source_file, "Jobs")

    if not jobs:
        await notifier.send_message(
            "No explore results found. Run `explore` or `explore-all` first."
        )
        return

    filtered = [j for j in jobs if min_score <= j.get("score", 0) <= max_score]

    if not filtered:
        await notifier.send_message(
            f"No jobs found with score {min_score}–{max_score}.\n"
            f"Try a wider range or run `explore` again."
        )
        return

    logger.info(f"[MAIN] {len(filtered)} jobs in score range {min_score}–{max_score}")

    await notifier.send_message(
        f"*Apply run started*\n"
        f"Source: `{source_file}`\n"
        f"Score range: {min_score}–{max_score}\n"
        f"Jobs to review: *{len(filtered)}*\n\n"
        f"Sending digest then all {len(filtered)} approval card(s)..."
    )
    await notifier.send_job_digest(filtered[:20])

    # Send all approval cards upfront — no waiting
    job_futures: list[tuple[dict, asyncio.Future]] = []
    for job in filtered:
        future = await notifier.send_approval_card(job)
        await asyncio.to_thread(
            tracker.update_job_status, job.get("url", ""), "approval_pending", jobs_tab
        )
        job_futures.append((job, future))

    await notifier.send_message(
        f"All {len(filtered)} card(s) sent. Approve or skip at your own pace — no time limit."
    )

    _APPLY_STATUS_MAP = {
        "submitted":      "applied",
        "filled_skipped": "skipped",
        "login_required": "login_required",
        "job_expired":    "expired",
        "no_form":        "no_form",
        "aggregator":     "aggregator",
        "submit_failed":  "apply_failed",
        "needs_human":    "needs_manual_apply",
        "error":          "apply_failed",
        "failed":         "apply_failed",
    }
    _APPLY_MESSAGES = {
        "filled_skipped": "You skipped submission — apply manually if needed:\n{url}",
        "login_required": "Login required — run `python main.py login` then retry:\n{url}",
        "job_expired":    "Job is closed/expired — no longer accepting applications.",
        "no_form":        "No application form found — check the URL manually:\n{url}",
        "aggregator":     "Aggregator URL — open the job and apply on the company site:\n{url}",
        "submit_failed":  "Form was filled but submit failed — please submit manually:\n{url}",
        "error":          "Unexpected error — check logs and apply manually.",
        "failed":         "Apply failed — check logs and apply manually.",
    }

    approved_count = 0
    submitted_count = skipped_count = login_count = expired_count = 0
    no_form_count = aggregator_count = manual_count = error_count = 0
    # Semaphore prevents concurrent browser sessions (apply one job at a time)
    apply_semaphore = asyncio.Semaphore(1)

    async def _process_one(job: dict, future: asyncio.Future) -> None:
        nonlocal approved_count, submitted_count, skipped_count, login_count, expired_count
        nonlocal no_form_count, aggregator_count, manual_count, error_count

        decision = await future  # waits indefinitely for user button press
        title = job.get("title", "?")
        company = job.get("company", "?")

        if decision != "approved":
            await asyncio.to_thread(
                tracker.update_job_status, job.get("url", ""), "skipped", jobs_tab
            )
            logger.info(f"[MAIN] Skipped: {title} @ {company}")
            return

        approved_count += 1
        await asyncio.to_thread(
            tracker.update_job_status, job.get("url", ""), "approved", jobs_tab
        )
        logger.info(f"[MAIN] Approved: {title} @ {company}")

        async with apply_semaphore:
            # Dedup — skip if already applied in a previous run
            already = await asyncio.to_thread(tracker.check_already_applied, job.get("url", ""))
            if already:
                try:
                    await notifier.send_message(f"Already applied to *{title}* @ {company} — skipping.")
                except Exception:
                    pass
                return

            # Reuse existing docs to avoid re-running the LLM
            docs_dir = generator.get_output_dir(job)
            if (docs_dir / "resume.pdf").exists():
                logger.info(f"[MAIN] Reusing existing docs for {title} @ {company}")
                docs = {
                    "output_dir": str(docs_dir),
                    "resume_pdf": str(docs_dir / "resume.pdf"),
                    "cover_letter_pdf": str(docs_dir / "cover-letter.pdf"),
                }
            else:
                docs = await asyncio.to_thread(generator.generate_docs, job)

            apply_result = await applicant.apply_to_job(job, docs)
            sheet_status = _APPLY_STATUS_MAP.get(apply_result, apply_result)
            await asyncio.to_thread(
                tracker.log_application, job, {**docs, "apply_result": apply_result}
            )
            await asyncio.to_thread(
                tracker.update_job_status, job.get("url", ""), sheet_status, jobs_tab
            )
            msg = _APPLY_MESSAGES.get(apply_result)
            if msg:
                try:
                    await notifier.send_message(
                        f"*{title}* @ {company}\n{msg.format(url=job.get('url',''))}"
                    )
                except Exception as _tg_err:
                    logger.warning(f"[MAIN] Telegram notify failed (non-fatal): {_tg_err}")
            if apply_result == "submitted":
                submitted_count += 1
            elif apply_result == "filled_skipped":
                skipped_count += 1
            elif apply_result == "login_required":
                login_count += 1
            elif apply_result == "job_expired":
                expired_count += 1
            elif apply_result == "no_form":
                no_form_count += 1
            elif apply_result == "aggregator":
                aggregator_count += 1
            elif apply_result == "needs_human":
                manual_count += 1
            else:
                error_count += 1
            logger.info(f"[MAIN] Apply result={apply_result} ({sheet_status}): {title} @ {company}")

    await asyncio.gather(*[_process_one(job, future) for job, future in job_futures])

    await notifier.send_message(
        f"*Apply run complete*\n"
        f"Reviewed: {len(filtered)} | Approved: {approved_count}\n"
        f"Submitted: {submitted_count} | Skipped by you: {skipped_count}\n"
        f"Needs manual submit: {manual_count} | Login required: {login_count}\n"
        f"Job expired: {expired_count} | No form: {no_form_count} | "
        f"Aggregator: {aggregator_count} | Errors: {error_count}"
    )
    logger.info(
        f"[MAIN] ===== Apply run complete — "
        f"reviewed={len(filtered)} approved={approved_count} "
        f"submitted={submitted_count} skipped={skipped_count} "
        f"login={login_count} expired={expired_count} "
        f"no_form={no_form_count} aggregator={aggregator_count} "
        f"manual={manual_count} errors={error_count} ====="
    )


# ── CLI ───────────────────────────────────────────────────────────────────────

_COMMANDS = {
    "run": (run_once, "Daily delta — Tier 1, today's jobs only, fast"),
    "explore": (run_explore, "Tier 1+2 full crawl — all keywords, no date filter"),
    "explore-tier2": (run_explore_tier2, "Tier 2 only — 14 niche India boards, no date filter"),
    "explore-tier3": (run_explore_tier3, "Tier 3 only — 100 company career pages"),
    "explore-all": (run_explore_all, "Everything: Tier 1+2+3, full crawl (30-60 min)"),
    "explore-posts": (run_explore_posts, "LinkedIn hiring posts — scrape founder/HR posts, extract+score, auto-gen docs (score ≥85)"),
    "apply": (None, "Generate docs & apply  [source] [min_score] [max_score]  (source: tier2|tier3|explore|all)"),
    "referral": (run_referral, "LinkedIn referral outreach — max 20 connection requests"),
    "login": (browser_login, "Open Chrome to log into job sites — saves session for Browser-Use"),
    "load-results": (load_results, "Push explore_results.json → Google Sheets Jobs tab"),
    "backfill-dates": (backfill_dates, "Fill blank Posted Date cells in Jobs sheet from raw JSON"),
    "schedule": (run_scheduled, "Start the 08:00 AM daily scheduler"),
}

if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "run"

    if command == "apply":
        args = sys.argv[2:]
        # First arg is source if it's not a number
        src = ""
        if args and not args[0].lstrip("-").isdigit():
            src = args[0]
            args = args[1:]
        try:
            min_s = int(args[0]) if len(args) >= 1 else 90
            max_s = int(args[1]) if len(args) >= 2 else 100
        except ValueError:
            print("Usage: python main.py apply [source] [min_score] [max_score]")
            print("  source: tier2 | tier3 | explore | all  (default: auto-detect)")
            print("  Examples:")
            print("    python main.py apply tier2        # Tier 2 jobs, score 90-100")
            print("    python main.py apply tier2 80     # Tier 2 jobs, score 80-100")
            print("    python main.py apply tier3 75 90  # Tier 3 jobs, score 75-90")
            print("    python main.py apply 80           # auto-detect, score 80-100")
            sys.exit(1)
        config.validate()
        asyncio.run(_run_with_telegram(apply_run(min_s, max_s, source=src)))

    elif command in _COMMANDS and _COMMANDS[command][0] is not None:
        fn, _ = _COMMANDS[command]
        fn()

    else:
        print("Usage: python main.py [command]\n")
        print("Commands:")
        for cmd, (_, desc) in _COMMANDS.items():
            print(f"  {cmd:<18} {desc}")
        sys.exit(1)

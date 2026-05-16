"""Job scoring and filtering — two-layer pipeline.

Layer 1 (deterministic): graph_engine computes objective skill-overlap from
  profile.md — same job always gives the same overlap score regardless of LLM.

Layer 2 (LLM): Kimi K2.6 reasons over the job + the graph evidence injected
  into the prompt, making scores more consistent and grounded.
"""

import json
import re
from typing import Any
from loguru import logger
import config
import graph_engine
import llm

_profile: str = ""
_preferences: str = ""


def _load_docs() -> None:
    """Load profile.md and preferences.md into module-level strings."""
    global _profile, _preferences
    if not _profile:
        _profile = config.PROFILE_PATH.read_text(encoding="utf-8")
    if not _preferences:
        _preferences = config.PREFERENCES_PATH.read_text(encoding="utf-8")


def _build_prompt(jobs: list[dict[str, Any]], graph_data: dict[str, dict]) -> str:
    """Build the scoring prompt with graph-overlap evidence injected per job.

    Args:
        jobs: List of normalized job dicts.
        graph_data: {url: graph_match_dict} from graph_engine.match_job().

    Returns:
        Formatted prompt string.
    """
    job_blocks = []
    for i, j in enumerate(jobs):
        gm = graph_data.get(j.get("url", ""), {})
        graph_line = gm.get("summary", "")
        block = (
            f"[{i+1}] title={j['title']!r} company={j['company']!r} "
            f"url={j['url']!r} source={j['source']!r}\n"
            + (f"[Graph] {graph_line}\n" if graph_line else "")
            + j["description"][:500]
        )
        job_blocks.append(block)

    jobs_text = "\n\n".join(job_blocks)

    return f"""You are a job-fit evaluator for a FRESHER (0 years full-time experience) seeking entry-level roles or internships.

## Candidate Profile
{_profile}

## Candidate Preferences
{_preferences}

## Jobs to Score
{jobs_text}

## Instructions
Each job above includes a [Graph] line showing the objective skill-overlap between the candidate's profile and the job's required skills. Use this as grounded evidence — a high overlap means the candidate already has most required skills; low overlap means significant skill gaps.

Score each job 0–100 based on fit with the candidate's profile AND preferences:
- Score 80–100: Excellent match (entry-level/intern role, relevant domain, high skill overlap)
- Score 60–79: Good match (mostly aligned, minor gaps)
- Score 40–59: Partial match (some mismatch in domain, seniority, or skill overlap)
- Score 0–39: Poor match (senior role, wrong domain, very low overlap, blacklisted)

IMPORTANT: Give a score of 0 to any job that:
- Requires 2+ years of experience
- Is not an internship or entry-level/junior position
- Is in a blacklisted industry or company

Return ONLY a valid JSON array, no markdown, no explanation. Format:
[
  {{"title": "...", "company": "...", "url": "...", "score": 85, "reason": "One-line reason", "source": "..."}},
  ...
]
"""


def score_jobs(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Score and rank jobs against the candidate profile using Kimi K2.6.

    Args:
        jobs: List of normalized job dicts from the scraper.

    Returns:
        List of scored job dicts sorted by score descending.
        Each dict has: title, company, url, score, reason, source.
    """
    _load_docs()

    if not jobs:
        logger.warning("[LLM] No jobs to score.")
        return []

    logger.info(f"[LLM] Scoring {len(jobs)} jobs against profile...")

    # ── Layer 1: deterministic graph overlap ──────────────────────────────────
    graph_data: dict[str, dict] = {}
    try:
        graph = graph_engine.get_graph()
        graph_data = {j.get("url", ""): graph.match(j) for j in jobs}
        overlaps = [v["overlap_score"] for v in graph_data.values() if v["overlap_score"] >= 0]
        if overlaps:
            logger.info(f"[GRAPH] Avg skill overlap: {sum(overlaps)//len(overlaps)}% across {len(overlaps)} jobs")
    except Exception as exc:
        logger.warning(f"[GRAPH] Graph matching failed, continuing without it: {exc}")

    # ── Layer 2: LLM semantic scoring with graph evidence injected ────────────
    prompt = _build_prompt(jobs, graph_data)
    messages = [{"role": "user", "content": prompt}]

    raw_reply = llm.chat_long(messages)

    # Strip markdown code fences if Kimi wraps response anyway
    cleaned = re.sub(r"```(?:json)?", "", raw_reply).strip().strip("`").strip()

    url_to_original = {j.get("url", ""): j for j in jobs}

    try:
        scored: list[dict[str, Any]] = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        logger.error(f"[LLM] Failed to parse JSON response: {exc}\nRaw reply:\n{raw_reply[:500]}")
        return [
            {
                "title": j["title"], "company": j["company"], "url": j["url"],
                "score": 0, "reason": "Parse error", "source": j["source"],
                "posted_date": j.get("posted_date", ""),
            }
            for j in jobs
        ]

    # Merge original fields + graph data back — LLM only returns title/company/url/score/reason
    for item in scored:
        url = item.get("url", "")
        original = url_to_original.get(url, {})
        item["posted_date"] = original.get("posted_date", "")
        gm = graph_data.get(url, {})
        item["graph_overlap"] = gm.get("overlap_score", -1)
        item["matched_skills"] = gm.get("matched", [])

    scored.sort(key=lambda x: x.get("score", 0), reverse=True)

    if scored:
        top = scored[0]
        overlap_str = f", graph overlap {top.get('graph_overlap')}%" if top.get("graph_overlap", -1) >= 0 else ""
        logger.info(
            f"[LLM] Done. Top score: {top.get('score')}/100{overlap_str} — "
            f"{top.get('title')} @ {top.get('company')}"
        )

    return scored


if __name__ == "__main__":
    config.validate()
    sample_jobs = [
        {
            "title": "Data Analyst Intern",
            "company": "Acme Corp",
            "url": "https://example.com/job/1",
            "description": "We are looking for a data analyst intern to join our analytics team.",
            "source": "indeed",
            "posted_date": "2026-05-01",
        }
    ]
    results = score_jobs(sample_jobs)
    logger.info(f"[FILTER] Results: {results}")

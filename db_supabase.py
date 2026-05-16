"""Supabase database operations — replaces Google Sheets for the web version.

Uses the service-role key for all writes (backend only).
The frontend uses the anon key with RLS to read-only.

Required env vars (add to .env):
  SUPABASE_URL          https://xxxx.supabase.co
  SUPABASE_SERVICE_KEY  service_role secret key (never expose to frontend)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from loguru import logger

# ── Client ────────────────────────────────────────────────────────────────────

_client = None


def _get_client():
    global _client
    if _client is None:
        try:
            from supabase import create_client
        except ImportError:
            raise RuntimeError("supabase not installed — pip install supabase")
        url = os.environ.get("SUPABASE_URL", "")
        key = os.environ.get("SUPABASE_SERVICE_KEY", "")
        if not url or not key:
            raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set in .env")
        _client = create_client(url, key)
    return _client


# ── Jobs ──────────────────────────────────────────────────────────────────────

def log_jobs(jobs: list[dict[str, Any]], tab: str) -> int:
    """Upsert scored jobs into the jobs table.

    On URL conflict, updates score/reason/graph_overlap so re-runs refresh stale data.

    Args:
        jobs: Scored job dicts (title, company, url, score, reason, graph_overlap, ...).
        tab: Source tier label — 'tier1' | 'tier2' | 'tier3' | 'posts'.

    Returns:
        Number of rows upserted.
    """
    if not jobs:
        return 0

    client = _get_client()
    rows = []
    for job in jobs:
        matched = job.get("matched_skills") or []
        rows.append({
            "title":          job.get("title", ""),
            "company":        job.get("company", ""),
            "url":            job.get("url", ""),
            "source":         job.get("source", ""),
            "location":       job.get("location", ""),
            "description":    (job.get("description") or "")[:2000],
            "score":          job.get("score"),
            "graph_overlap":  job.get("graph_overlap"),
            "matched_skills": matched if isinstance(matched, list) else [],
            "reason":         job.get("reason", ""),
            "posted_date":    job.get("posted_date", ""),
            "tab":            tab,
            "status":         "scored",
        })

    try:
        result = (
            client.table("jobs")
            .upsert(rows, on_conflict="url")
            .execute()
        )
        count = len(result.data) if result.data else 0
        logger.info(f"[SUPABASE] Upserted {count} jobs into tab='{tab}'")
        return count
    except Exception as exc:
        logger.error(f"[SUPABASE] log_jobs failed: {exc}")
        return 0


def get_jobs(
    tab: str | None = None,
    min_score: int = 0,
    source: str | None = None,
    keyword: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Fetch jobs from Supabase with optional filters."""
    client = _get_client()
    try:
        q = client.table("jobs").select("*")
        if tab:
            q = q.eq("tab", tab)
        if source:
            q = q.eq("source", source)
        if min_score:
            q = q.gte("score", min_score)
        if keyword:
            q = q.ilike("title", f"%{keyword}%")
        q = q.order("score", desc=True).range(offset, offset + limit - 1)
        result = q.execute()
        return result.data or []
    except Exception as exc:
        logger.error(f"[SUPABASE] get_jobs failed: {exc}")
        return []


def get_job(job_id: str) -> dict[str, Any] | None:
    """Fetch a single job by ID."""
    client = _get_client()
    try:
        result = client.table("jobs").select("*").eq("id", job_id).single().execute()
        return result.data
    except Exception as exc:
        logger.error(f"[SUPABASE] get_job({job_id}) failed: {exc}")
        return None


def get_job_by_url(url: str) -> dict[str, Any] | None:
    """Fetch a single job by URL (used after upsert to retrieve the generated UUID)."""
    client = _get_client()
    try:
        result = client.table("jobs").select("*").eq("url", url).single().execute()
        return result.data
    except Exception:
        return None


# ── Resumes ───────────────────────────────────────────────────────────────────

def save_resume(
    job_id: str,
    resume_url: str,
    cover_letter_url: str,
) -> None:
    """Insert a resume record after PDFs are uploaded to Supabase Storage."""
    client = _get_client()
    try:
        client.table("resumes").upsert({
            "job_id":           job_id,
            "resume_url":       resume_url,
            "cover_letter_url": cover_letter_url,
        }, on_conflict="job_id").execute()
        logger.info(f"[SUPABASE] Resume saved for job {job_id}")
    except Exception as exc:
        logger.error(f"[SUPABASE] save_resume failed: {exc}")


def get_resumes(limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
    """Fetch resume records joined with job info."""
    client = _get_client()
    try:
        result = (
            client.table("resumes")
            .select("*, jobs(title, company, url, score)")
            .order("generated_at", desc=True)
            .range(offset, offset + limit - 1)
            .execute()
        )
        return result.data or []
    except Exception as exc:
        logger.error(f"[SUPABASE] get_resumes failed: {exc}")
        return []


def get_resume_by_job(job_id: str) -> dict[str, Any] | None:
    """Check if a resume already exists for a job."""
    client = _get_client()
    try:
        result = (
            client.table("resumes")
            .select("*")
            .eq("job_id", job_id)
            .single()
            .execute()
        )
        return result.data
    except Exception:
        return None


# ── Run logs ──────────────────────────────────────────────────────────────────

def log_run(command: str) -> str:
    """Insert a new run_log row and return its ID."""
    client = _get_client()
    try:
        result = (
            client.table("run_logs")
            .insert({"command": command, "status": "running"})
            .execute()
        )
        run_id = result.data[0]["id"]
        logger.info(f"[SUPABASE] Run started: {command} ({run_id})")
        return run_id
    except Exception as exc:
        logger.error(f"[SUPABASE] log_run failed: {exc}")
        return ""


def finish_run(
    run_id: str,
    jobs_found: int = 0,
    status: str = "done",
    message: str = "",
) -> None:
    """Update a run_log row when the pipeline finishes."""
    if not run_id:
        return
    client = _get_client()
    try:
        from datetime import datetime, timezone
        client.table("run_logs").update({
            "status":      status,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "jobs_found":  jobs_found,
            "message":     message,
        }).eq("id", run_id).execute()
        logger.info(f"[SUPABASE] Run {run_id} finished: {status}, {jobs_found} jobs")
    except Exception as exc:
        logger.error(f"[SUPABASE] finish_run failed: {exc}")


def get_runs(limit: int = 20) -> list[dict[str, Any]]:
    """Fetch recent run logs for the UI status display."""
    client = _get_client()
    try:
        result = (
            client.table("run_logs")
            .select("*")
            .order("started_at", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data or []
    except Exception as exc:
        logger.error(f"[SUPABASE] get_runs failed: {exc}")
        return []


# ── Storage (PDF upload) ──────────────────────────────────────────────────────

def upload_pdf(local_path: Path, storage_path: str) -> str:
    """Upload a local PDF to Supabase Storage bucket 'resumes'.

    Args:
        local_path: Local filesystem path to the PDF.
        storage_path: Path inside the bucket, e.g. 'company-role/resume.pdf'.

    Returns:
        Public URL of the uploaded file, or empty string on failure.
    """
    client = _get_client()
    url = os.environ.get("SUPABASE_URL", "")
    try:
        with open(local_path, "rb") as f:
            data = f.read()
        client.storage.from_("resumes").upload(
            storage_path,
            data,
            {"content-type": "application/pdf", "upsert": "true"},
        )
        public_url = f"{url}/storage/v1/object/public/resumes/{storage_path}"
        logger.info(f"[SUPABASE] Uploaded PDF: {public_url}")
        return public_url
    except Exception as exc:
        logger.error(f"[SUPABASE] upload_pdf({storage_path}) failed: {exc}")
        return ""


def is_configured() -> bool:
    """Return True if Supabase env vars are set."""
    return bool(
        os.environ.get("SUPABASE_URL") and
        os.environ.get("SUPABASE_SERVICE_KEY")
    )

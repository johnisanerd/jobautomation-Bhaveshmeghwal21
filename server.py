"""FastAPI server — HTTP wrapper around the OpenClaw pipeline.

Run locally:
    uvicorn server:app --reload --port 8000

On Digital Ocean (via docker-compose):
    uvicorn server:app --host 0.0.0.0 --port 8000 --workers 1
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

import config
import db_supabase

# ── App setup ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    config.validate()
    logger.info("[SERVER] OpenClaw API started")
    yield
    logger.info("[SERVER] OpenClaw API stopped")


app = FastAPI(title="OpenClaw API", version="1.0.0", lifespan=lifespan)

# Allow the Vercel frontend (and localhost dev) to call this API
_ALLOWED_ORIGINS = [
    "http://localhost:3000",
    os.environ.get("FRONTEND_URL", ""),   # set this to your Vercel URL
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o for o in _ALLOWED_ORIGINS if o],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Background task helpers ───────────────────────────────────────────────────

def _run_explore(tier: str, run_id: str) -> None:
    """Synchronous pipeline runner called inside BackgroundTasks thread."""
    import main as pipeline

    runners = {
        "tier1":  pipeline.explore_run,
        "tier2":  pipeline.explore_tier2_run,
        "tier3":  pipeline.explore_tier3_run,
        "posts":  pipeline.explore_posts_run,
        "all":    pipeline.explore_all_run,
    }
    coro_fn = runners.get(tier)
    if coro_fn is None:
        db_supabase.finish_run(run_id, status="failed", message=f"Unknown tier: {tier}")
        return

    try:
        asyncio.run(pipeline._run_with_telegram(coro_fn()))
        db_supabase.finish_run(run_id, status="done", message="Completed successfully")
    except Exception as exc:
        logger.error(f"[SERVER] Explore {tier} failed: {exc}")
        db_supabase.finish_run(run_id, status="failed", message=str(exc))


def _run_generate_resume(job_id: str) -> None:
    """Generate resume + cover letter for a job, upload PDFs to Supabase Storage."""
    import generator

    job = db_supabase.get_job(job_id)
    if not job:
        logger.error(f"[SERVER] Job {job_id} not found")
        return

    existing = db_supabase.get_resume_by_job(job_id)
    if existing and existing.get("resume_url"):
        logger.info(f"[SERVER] Resume already exists for {job_id}")
        return

    try:
        docs = generator.generate_docs(job)

        from pathlib import Path
        slug = f"{job.get('company', 'company')}-{job.get('title', 'role')}"
        slug = slug.lower().replace(" ", "-")[:80]

        resume_url = db_supabase.upload_pdf(
            Path(docs["resume_pdf"]), f"{slug}/resume.pdf"
        )
        cover_url = db_supabase.upload_pdf(
            Path(docs["cover_letter_pdf"]), f"{slug}/cover-letter.pdf"
        )
        db_supabase.save_resume(job_id, resume_url, cover_url)
        logger.info(f"[SERVER] Resume generated and uploaded for {job_id}")
    except Exception as exc:
        logger.error(f"[SERVER] Resume generation failed for {job_id}: {exc}")


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
def health() -> dict:
    return {"status": "ok", "supabase": db_supabase.is_configured()}


@app.get("/jobs")
def list_jobs(
    tab: str | None = Query(None, description="tier1 | tier2 | tier3 | posts"),
    min_score: int = Query(0, ge=0, le=100),
    source: str | None = Query(None),
    keyword: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[dict[str, Any]]:
    return db_supabase.get_jobs(
        tab=tab,
        min_score=min_score,
        source=source,
        keyword=keyword,
        limit=limit,
        offset=offset,
    )


@app.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    job = db_supabase.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.post("/explore/{tier}")
def start_explore(
    tier: str,
    background_tasks: BackgroundTasks,
) -> dict:
    """Fire-and-forget: start an explore run in the background.

    tier: tier1 | tier2 | tier3 | posts | all
    """
    valid = {"tier1", "tier2", "tier3", "posts", "all"}
    if tier not in valid:
        raise HTTPException(status_code=400, detail=f"tier must be one of {valid}")

    run_id = db_supabase.log_run(f"explore-{tier}")
    background_tasks.add_task(_run_explore, tier, run_id)
    return {"run_id": run_id, "status": "started", "tier": tier}


@app.post("/resume/{job_id}")
def generate_resume(
    job_id: str,
    background_tasks: BackgroundTasks,
) -> dict:
    """Generate resume + cover letter for a job and upload to Supabase Storage."""
    job = db_supabase.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    existing = db_supabase.get_resume_by_job(job_id)
    if existing and existing.get("resume_url"):
        return {
            "status":           "already_exists",
            "resume_url":       existing["resume_url"],
            "cover_letter_url": existing.get("cover_letter_url", ""),
        }

    background_tasks.add_task(_run_generate_resume, job_id)
    return {"status": "generating", "job_id": job_id}


@app.get("/resume/{job_id}")
def get_resume(job_id: str) -> dict:
    """Poll resume status — returns URLs once generation is complete."""
    resume = db_supabase.get_resume_by_job(job_id)
    if not resume:
        return {"status": "not_generated"}
    return {
        "status":           "ready",
        "resume_url":       resume.get("resume_url", ""),
        "cover_letter_url": resume.get("cover_letter_url", ""),
        "generated_at":     resume.get("generated_at", ""),
    }


@app.get("/resumes")
def list_resumes(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[dict[str, Any]]:
    return db_supabase.get_resumes(limit=limit, offset=offset)


@app.get("/runs")
def list_runs(limit: int = Query(20, ge=1, le=100)) -> list[dict[str, Any]]:
    return db_supabase.get_runs(limit=limit)

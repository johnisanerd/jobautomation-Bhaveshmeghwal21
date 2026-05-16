"""Resume tailoring and cover letter generation via Azure AI (Phase 3)."""

import json
import re
from datetime import date
from pathlib import Path
from typing import Any

from loguru import logger

import config
import llm

_profile_cache: str = ""


def _load_profile() -> str:
    """Return profile.md contents, cached after first read."""
    global _profile_cache
    if not _profile_cache:
        _profile_cache = config.PROFILE_PATH.read_text(encoding="utf-8")
    return _profile_cache


def _slugify(text: str) -> str:
    """Convert text to a lowercase filesystem-safe slug."""
    return re.sub(r"[^\w\-]", "_", text.strip().lower())[:60]


# ── CSS ───────────────────────────────────────────────────────────────────────

_RESUME_CSS = """
@page { size: A4; margin: 0; }
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: Arial, Helvetica, sans-serif;
  font-size: 9.5pt;
  color: #1a1a1a;
  padding: 24px 32px;
  line-height: 1.45;
  min-height: 277mm;
}
h1 { font-size: 21pt; font-weight: bold; margin-bottom: 4px; text-align: center; }
.contact { font-size: 8.5pt; color: #555; margin-bottom: 16px; text-align: center; }
h2 {
  font-size: 9pt;
  text-transform: uppercase;
  letter-spacing: 0.9px;
  border-bottom: 1.5px solid #222;
  padding-bottom: 2px;
  margin: 14px 0 8px;
}
.entry { margin-bottom: 13px; }
.row { display: flex; justify-content: space-between; align-items: baseline; }
.bold { font-weight: bold; }
.italic { font-style: italic; color: #444; }
.muted { font-size: 8.5pt; color: #555; }
ul { margin: 4px 0 0 16px; }
li { margin-bottom: 3px; }
.skills { margin: 0; padding: 0; list-style: none; display: flex; flex-wrap: wrap; gap: 3px 0; }
.skills li { margin-right: 14px; }
p { margin: 4px 0; }
"""

_COVER_CSS = """
@page { size: A4; margin: 0; }
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: Georgia, 'Times New Roman', serif;
  font-size: 11pt;
  color: #1a1a1a;
  padding: 55px 70px;
  line-height: 1.7;
}
.meta { margin-bottom: 30px; font-size: 10pt; color: #444; }
.recipient { margin-bottom: 22px; }
p { margin-bottom: 14px; }
.closing { margin-top: 32px; }
"""


# ── HTML builders ─────────────────────────────────────────────────────────────

def _build_resume_html(tailored: dict[str, Any]) -> str:
    """Fill the resume HTML template from LLM-tailored content."""
    # Skills row
    skills_html = "\n".join(
        f"<li>{s}</li>" for s in tailored.get("top_skills", [])
    )

    # Experience blocks
    exp_blocks: list[str] = []
    for exp in tailored.get("experience", []):
        bullets = "".join(f"<li>{b}</li>" for b in exp.get("bullets", []))
        exp_blocks.append(
            f'<div class="entry">'
            f'<div class="row"><span class="bold">{exp.get("role", "")}</span>'
            f'<span class="muted">{exp.get("period", "")}</span></div>'
            f'<div class="italic">{exp.get("company", "")}</div>'
            f"<ul>{bullets}</ul></div>"
        )

    # Project blocks (includes any LLM-suggested extra project)
    proj_blocks: list[str] = []
    for proj in tailored.get("projects", []):
        if not isinstance(proj, dict):
            continue
        bullets = "".join(f"<li>{b}</li>" for b in proj.get("bullets", []))
        proj_blocks.append(
            f'<div class="entry">'
            f'<div class="row"><span class="bold">{proj.get("name", "")}</span>'
            f'<span class="muted italic">{proj.get("tech", "")}</span></div>'
            f"<ul>{bullets}</ul></div>"
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><style>{_RESUME_CSS}</style></head>
<body>
  <h1>Namrata Sahu</h1>
  <div class="contact">
    namratasahuwork@gmail.com &nbsp;·&nbsp; +91 9667658190 &nbsp;·&nbsp;
    linkedin.com/in/namratasa1 &nbsp;·&nbsp; Delhi, India
  </div>

  <h2>Summary</h2>
  <p>{tailored.get("summary", "")}</p>

  <h2>Education</h2>
  <div class="entry">
    <div class="row">
      <span class="bold">B.Tech in Environmental Engineering</span>
      <span class="muted">2022 – 2026</span>
    </div>
    <div class="italic">Delhi Technological University (DTU) &nbsp;·&nbsp; CGPA: 7.52</div>
  </div>

  <h2>Skills</h2>
  <ul class="skills">{skills_html}</ul>

  <h2>Experience</h2>
  {"".join(exp_blocks)}

  <h2>Projects</h2>
  {"".join(proj_blocks)}

  <h2>Achievements &amp; Certifications</h2>
  <ul>
    <li>3rd place — Code or Die Hackathon, DTU (1,200+ participants)</li>
    <li>Co-Head, Neural.ai — DTU's AI Society; organized Invictus 2023 (50,000+ footfall)</li>
    <li>Solved 200+ DSA problems (medium/hard) across 3 platforms</li>
    <li>Generative AI Mastery Workshop — NxtWave &amp; OpenAI Academy</li>
  </ul>
</body>
</html>"""


def _build_cover_html(letter_body: str, job: dict[str, Any]) -> str:
    """Wrap cover letter body text in the cover letter HTML template."""
    today = date.today().strftime("%B %d, %Y")
    company = job.get("company", "the company")
    role = job.get("title", "the role")
    paras = "".join(
        f"<p>{p.strip()}</p>"
        for p in letter_body.split("\n")
        if p.strip()
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><style>{_COVER_CSS}</style></head>
<body>
  <div class="meta">
    <strong>Namrata Sahu</strong><br>
    namratasahuwork@gmail.com &nbsp;·&nbsp; +91 9667658190<br>
    linkedin.com/in/namratasa1<br><br>
    {today}
  </div>
  <div class="recipient">
    Hiring Manager<br>
    {company}<br>
    <em>Re: {role}</em>
  </div>
  {paras}
  <div class="closing">
    Sincerely,<br>
    <strong>Namrata Sahu</strong>
  </div>
</body>
</html>"""


# ── LLM prompts ───────────────────────────────────────────────────────────────

def _tailor_resume(profile: str, job: dict[str, Any]) -> dict[str, Any]:
    """Ask the LLM to tailor resume content for a job. Returns parsed JSON dict."""
    jd = (job.get("description") or "")[:2500]

    # Detect sparse profile (≤1 internship) → request an extra suggested project
    exp_count = sum(
        1 for line in profile.splitlines()
        if any(kw in line.lower() for kw in ["intern", "engineer", "analyst", "associate"])
        and "|" in line
    )
    extra_project_note = (
        '\n  NOTE: The candidate has limited work experience. If the content above would not fill '
        'a full A4 page, append one additional project object to the "projects" array with '
        '"suggested": true. It must use only technologies already in the candidate\'s profile, '
        'be directly relevant to this JD, and must NOT duplicate any project already in the profile.'
    ) if exp_count <= 1 else ""

    prompt = f"""You are an expert ATS resume writer for Indian tech companies. Rewrite the candidate's resume to be maximally relevant to the target role. Every fact must come from the Candidate Profile — never invent anything.

## Candidate Profile
{profile}

## Target Job
Title: {job.get("title", "")}
Company: {job.get("company", "")}
Job Description:
{jd}

Return ONLY a valid JSON object — no markdown, no explanation, nothing else.

Schema:
{{
  "summary": "2-3 tight sentences tailored to this exact role and company. Mirror JD keywords. State graduating 2026 from DTU. Quantify where possible.",
  "top_skills": ["skill1", "skill2", "...exactly 10 skills from the profile, ordered by JD relevance"],
  "experience": [
    {{
      "role": "exact role title from profile",
      "company": "exact company name from profile",
      "period": "exact date range from profile",
      "bullets": ["3-4 bullets. Past-tense action verb. JD keywords. Metrics from profile."]
    }}
  ],
  "projects": [
    {{
      "name": "most relevant project from profile",
      "tech": "exact tech stack from profile",
      "bullets": ["2-3 bullets. Action verb + outcome + metric."]
    }},
    {{
      "name": "second most relevant project from profile",
      "tech": "exact tech stack from profile",
      "bullets": ["2-3 bullets."]
    }}
  ]
}}{extra_project_note}

Rules:
1. Include EVERY work experience from the profile — never drop any.
2. Every bullet starts with a past-tense action verb (Built, Designed, Analysed, etc.).
3. Never invent facts, companies, numbers, or technologies not in the profile.
4. One bullet = one line. No run-ons.
5. Skills must exist in the profile.
"""
    for attempt in range(2):
        raw = llm.chat_long([{"role": "user", "content": prompt}])
        cleaned = re.sub(r"```(?:json)?", "", raw).strip().strip("`").strip()
        if not cleaned:
            logger.warning(f"[GENERATOR] LLM returned empty response (attempt {attempt + 1}/2).")
            continue
        try:
            result = json.loads(cleaned)
            break
        except json.JSONDecodeError as exc:
            logger.error(f"[GENERATOR] JSON parse failed (attempt {attempt + 1}/2): {exc}\nSnippet:\n{cleaned[:300]}")
            result = {}
    else:
        result = {}

    # Drop any string placeholders the LLM left inside the experience array
    result["experience"] = [
        e for e in result.get("experience", []) if isinstance(e, dict)
    ]
    result["projects"] = [
        p for p in result.get("projects", []) if isinstance(p, dict)
    ]

    # If content is sparse (≤1 experience + ≤2 projects), generate a suggested extra project
    if len(result.get("experience", [])) <= 1 and len(result.get("projects", [])) <= 2:
        logger.info("[GENERATOR] Sparse content — requesting a suggested extra project to fill page.")
        extra_prompt = f"""The candidate has limited work experience. Suggest ONE additional portfolio project they could realistically build as a final-year student, based on the job description below. It must use technologies from their existing skills and must NOT duplicate any project already in their profile.

Candidate skills (from profile): {', '.join(result.get('top_skills', []))}
Existing projects: {', '.join(p.get('name', '') for p in result.get('projects', []))}

Job Description:
{jd}

Return ONLY a JSON object — no markdown, no explanation:
{{"name": "project title", "tech": "tech stack", "bullets": ["2-3 action-verb bullets with outcomes/metrics"]}}
"""
        raw2 = llm.chat_long([{"role": "user", "content": extra_prompt}])
        cleaned2 = re.sub(r"```(?:json)?", "", raw2).strip().strip("`").strip()
        try:
            extra_proj = json.loads(cleaned2)
            if isinstance(extra_proj, dict) and extra_proj.get("name"):
                extra_proj["suggested"] = True
                result.setdefault("projects", []).append(extra_proj)
                logger.info(f"[GENERATOR] Added suggested project: {extra_proj.get('name')}")
        except json.JSONDecodeError:
            logger.warning("[GENERATOR] Could not parse suggested project — skipping.")

    return result


def _write_cover_letter(profile: str, job: dict[str, Any]) -> str:
    """Ask the LLM to write a cover letter body. Returns plain text paragraphs."""
    jd = (job.get("description") or "")[:2500]
    prompt = f"""You are an expert cover letter writer for Indian tech job applications.

## Candidate Profile
{profile}

## Target Job
Title: {job.get("title", "")}
Company: {job.get("company", "")}
Job Description:
{jd}

## Your task
Write a targeted, non-generic cover letter body for this specific role.

Rules:
- 3-4 paragraphs, ~270 words total
- Opening: reference something specific and real about the company or role from the JD — show you've read it
- Middle paragraphs: cite 2 specific, concrete examples from the candidate's actual experience that directly address the JD requirements — use numbers and outcomes where they exist
- Closing: confident ask for an interview; mention graduating in 2026
- Do NOT use clichés like "I am writing to express my interest" or "I am a passionate..."
- Do NOT repeat the resume bullet points — interpret and contextualise
- Write in first person, professional but conversational
- Return ONLY the body paragraphs as plain text (no salutation, no sign-off — those are added automatically)
- Separate paragraphs with a blank line
"""
    return llm.chat_long([{"role": "user", "content": prompt}]).strip()


# ── PDF rendering ─────────────────────────────────────────────────────────────

def _html_to_pdf(html: str, pdf_path: Path) -> bool:
    """Render HTML to PDF using Chrome or Edge headless via subprocess.

    No Playwright browser download required — uses the system browser directly.
    """
    import subprocess
    import tempfile

    candidates = [
        "/usr/bin/chromium",           # Debian/Ubuntu (Docker)
        "/usr/bin/chromium-browser",   # some Ubuntu variants
        "/usr/bin/google-chrome",      # if Chrome is installed on Linux
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ]
    browser_exe = next((c for c in candidates if Path(c).exists()), None)
    if not browser_exe:
        logger.error(
            "[GENERATOR] Chrome/Edge not found — HTML saved, no PDF. "
            "Install Google Chrome to enable PDF output."
        )
        return False

    tmp_html: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            suffix=".html", delete=False, mode="w", encoding="utf-8"
        ) as f:
            f.write(html)
            tmp_html = Path(f.name)

        subprocess.run(
            [
                browser_exe,
                "--headless=new",
                "--disable-gpu",
                "--no-sandbox",
                "--no-first-run",
                "--no-default-browser-check",
                f"--print-to-pdf={pdf_path}",
                "--print-to-pdf-no-header",
                str(tmp_html),
            ],
            capture_output=True,
            timeout=30,
            check=True,
        )
        return pdf_path.exists()
    except Exception as exc:
        logger.error(f"[GENERATOR] PDF render failed: {exc}")
        return False
    finally:
        if tmp_html:
            tmp_html.unlink(missing_ok=True)


# ── Public API ────────────────────────────────────────────────────────────────

def get_output_dir(job: dict) -> Path:
    """Return the expected output directory path for a job without creating it."""
    return config.APPLICATIONS_DIR / f"{_slugify(job.get('company','company'))}-{_slugify(job.get('title','role'))}"


def generate_resume(job: dict[str, Any]) -> Path:
    """Generate a tailored, ATS-optimized resume for a specific job.

    Calls the LLM to tailor content, builds HTML, renders to PDF via WeasyPrint.
    Falls back to saving the HTML file if WeasyPrint is unavailable.

    Args:
        job: Job dict with at minimum title, company, description.

    Returns:
        Path to the generated resume PDF (or HTML on WeasyPrint failure).
    """
    profile = _load_profile()
    output_dir = config.APPLICATIONS_DIR / f"{_slugify(job.get('company','company'))}-{_slugify(job.get('title','role'))}"
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"[GENERATOR] Tailoring resume — {job.get('title')} @ {job.get('company')}")

    tailored = _tailor_resume(profile, job)
    if not tailored:
        logger.warning("[GENERATOR] LLM tailoring failed — using minimal fallback content")
        tailored = {
            "summary": (
                "Final-year B.Tech student at Delhi Technological University (DTU, 2026) "
                "with internship experience in software development, data analytics, and product operations. "
                "Seeking entry-level opportunities in data and product roles."
            ),
            "top_skills": [
                "Python", "SQL", "PostgreSQL", "Data Analysis", "PowerBI",
                "Node.js", "React.js", "Machine Learning", "REST APIs",
            ],
            "experience": [],
            "projects": [],
        }

    html = _build_resume_html(tailored)
    html_path = output_dir / "resume.html"
    html_path.write_text(html, encoding="utf-8")

    pdf_path = output_dir / "resume.pdf"
    if _html_to_pdf(html, pdf_path):
        logger.info(f"[GENERATOR] Resume PDF saved: {pdf_path}")
        return pdf_path

    logger.info(f"[GENERATOR] Resume HTML saved: {html_path}")
    return html_path


def generate_cover_letter(job: dict[str, Any]) -> Path:
    """Generate a targeted cover letter for a specific job.

    Args:
        job: Job dict with at minimum title, company, description.

    Returns:
        Path to the generated cover letter PDF (or HTML on WeasyPrint failure).
    """
    profile = _load_profile()
    output_dir = config.APPLICATIONS_DIR / f"{_slugify(job.get('company','company'))}-{_slugify(job.get('title','role'))}"
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"[GENERATOR] Writing cover letter — {job.get('title')} @ {job.get('company')}")

    letter_body = _write_cover_letter(profile, job)
    html = _build_cover_html(letter_body, job)

    html_path = output_dir / "cover-letter.html"
    html_path.write_text(html, encoding="utf-8")

    pdf_path = output_dir / "cover-letter.pdf"
    if _html_to_pdf(html, pdf_path):
        logger.info(f"[GENERATOR] Cover letter PDF saved: {pdf_path}")
        return pdf_path

    logger.info(f"[GENERATOR] Cover letter HTML saved: {html_path}")
    return html_path


def generate_docs(job: dict[str, Any]) -> dict[str, Any]:
    """Generate both resume and cover letter for a job, plus save the job description.

    Args:
        job: Scored job dict with title, company, url, description.

    Returns:
        Dict with keys: output_dir, resume_pdf, cover_letter_pdf (string paths).
    """
    output_dir = config.APPLICATIONS_DIR / f"{_slugify(job.get('company','company'))}-{_slugify(job.get('title','role'))}"
    output_dir.mkdir(parents=True, exist_ok=True)

    jd_path = output_dir / "job-description.txt"
    jd_path.write_text(
        f"Title:   {job.get('title', '')}\n"
        f"Company: {job.get('company', '')}\n"
        f"Source:  {job.get('source', '')}\n"
        f"URL:     {job.get('url', '')}\n"
        f"Score:   {job.get('score', '')}/100\n"
        f"Reason:  {job.get('reason', '')}\n\n"
        f"--- Job Description ---\n{job.get('description', '')}",
        encoding="utf-8",
    )

    resume_path = generate_resume(job)
    cover_path = generate_cover_letter(job)

    return {
        "output_dir": str(output_dir),
        "resume_pdf": str(resume_path),
        "cover_letter_pdf": str(cover_path),
    }


if __name__ == "__main__":
    import sys

    config.validate()

    # Load real jobs from the latest explore results file
    results_files = [
        "explore_all_results.json",
        "explore_results.json",
        "explore_tier3_results.json",
    ]
    jobs: list[dict] = []
    for fname in results_files:
        p = Path(config.BASE_DIR) / fname
        if p.exists():
            jobs = json.loads(p.read_text(encoding="utf-8"))
            print(f"Loaded {len(jobs)} jobs from {fname}")
            break

    if not jobs:
        print("No explore results found. Run 'python main.py explore' first.")
        sys.exit(1)

    print("\nTop 10 jobs by score:")
    for i, j in enumerate(jobs[:10], 1):
        print(f"  [{i}] {j.get('title')} @ {j.get('company')} — {j.get('score')}/100")

    raw = input("\nEnter job number [1]: ").strip()
    idx = (int(raw) - 1) if raw.isdigit() and 1 <= int(raw) <= len(jobs) else 0
    chosen = jobs[idx]

    print(f"\nGenerating docs for: {chosen.get('title')} @ {chosen.get('company')}")
    result = generate_docs(chosen)
    print(f"\nDone! Saved to: {result['output_dir']}")
    print(f"  Resume:       {result['resume_pdf']}")
    print(f"  Cover letter: {result['cover_letter_pdf']}")

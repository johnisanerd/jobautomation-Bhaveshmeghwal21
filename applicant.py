"""Phase 4: Application execution via async Playwright for all supported ATS platforms.

Supported ATS (full auto — fill + screenshot + Telegram confirm + submit):
  Lever, Greenhouse, Ashby, SmartRecruiters

Supported ATS (semi-auto — fill + screenshot + notify to submit manually):
  Naukri, Darwinbox, Keka, Zoho Recruit

Fill-only (too bot-resistant for auto-submit):
  Workday, Taleo

Rule: NEVER submit without explicit Telegram confirmation from the user.
"""

import asyncio
import re
from enum import Enum
from pathlib import Path
from typing import Any

from loguru import logger

import config
import llm
import notifier

# ── Candidate constants (from profile.md) ────────────────────────────────────

_FIRST = "Namrata"
_LAST = "Sahu"
_NAME = "Namrata Sahu"
_EMAIL = "namratasahuwork@gmail.com"
_PHONE = "+91 9667658190"
_PHONE_DIGITS = "9667658190"
_LINKEDIN = "https://www.linkedin.com/in/namratasa1/"
_LOCATION = "Delhi, India"
_COLLEGE = "Delhi Technological University"
_DEGREE = "B.Tech in Environmental Engineering"
_GRAD_YEAR = "2026"
_GPA = "7.52"

_CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# ── ATS detection ─────────────────────────────────────────────────────────────


class ATS(str, Enum):
    LEVER = "lever"
    GREENHOUSE = "greenhouse"
    ASHBY = "ashby"
    NAUKRI = "naukri"
    DARWINBOX = "darwinbox"
    KEKA = "keka"
    ZOHO_RECRUIT = "zoho_recruit"
    SMARTRECRUITERS = "smartrecruiters"
    WORKDAY = "workday"
    TALEO = "taleo"
    UNKNOWN = "unknown"


_ATS_PATTERNS: list[tuple[str, ATS]] = [
    ("lever.co", ATS.LEVER),
    ("greenhouse.io", ATS.GREENHOUSE),
    ("ashbyhq.com", ATS.ASHBY),
    ("darwinbox.com", ATS.DARWINBOX),
    (".keka.com", ATS.KEKA),
    ("zohorecruit.com", ATS.ZOHO_RECRUIT),
    ("zoho.com/recruit", ATS.ZOHO_RECRUIT),
    ("smartrecruiters.com", ATS.SMARTRECRUITERS),
    ("myworkdayjobs.com", ATS.WORKDAY),
    ("workday.com", ATS.WORKDAY),
    (".taleo.net", ATS.TALEO),
]

# ATS types that support full auto-submit (fill + confirm + submit)
_FULL_AUTO_ATS = {ATS.LEVER, ATS.GREENHOUSE, ATS.ASHBY, ATS.SMARTRECRUITERS}

# ATS types that are semi-auto (fill + notify to submit manually)
_SEMI_AUTO_ATS = {ATS.NAUKRI, ATS.DARWINBOX, ATS.KEKA, ATS.ZOHO_RECRUIT}

# ATS types that are fill-only (too complex/bot-resistant)
_FILL_ONLY_ATS = {ATS.WORKDAY, ATS.TALEO}

# Aggregator domains that redirect to external ATS — Browser-Use can't apply here
# even when logged in, because the actual form is on a third-party site.
# Platforms that just need a login (Internshala, Unstop, etc.) are NOT listed here
# so Browser-Use can apply using the saved .browser_profile session.
_AGGREGATOR_DOMAINS = {
    "linkedin.com",
    "google.com/about/careers",
    "careers.google.com",
}


def detect_ats(url: str) -> ATS:
    """Detect ATS platform from a job URL.

    Args:
        url: Job posting URL.

    Returns:
        Matched ATS enum value, or ATS.UNKNOWN.
    """
    u = url.lower()
    for pattern, ats in _ATS_PATTERNS:
        if pattern in u:
            return ats
    return ATS.UNKNOWN


# ── LLM helpers ───────────────────────────────────────────────────────────────

_profile_cache: str = ""


def _load_profile() -> str:
    global _profile_cache
    if not _profile_cache:
        _profile_cache = config.PROFILE_PATH.read_text(encoding="utf-8")
    return _profile_cache


def _answer_custom_question(question: str, job: dict[str, Any]) -> str:
    """Use the LLM to answer a custom application question on the candidate's behalf.

    Args:
        question: The question text from the application form.
        job: Job dict with title, company, description.

    Returns:
        Answer string (2-3 sentences max).
    """
    profile = _load_profile()
    prompt = f"""You are answering a job application question on behalf of the candidate.

## Candidate Profile
{profile}

## Target Job
Title: {job.get('title', '')}
Company: {job.get('company', '')}

## Application Question
{question}

## Rules
- Answer in 1-3 concise sentences (match length to question type)
- Be specific and authentic, drawing only from the profile
- For "years of experience" → answer "Fresher" or "0 years full-time, 3 internships"
- For "notice period" → answer "Immediately available"
- For "current CTC / expected CTC" → answer "Fresher — open to market rate"
- For "Why this company?" → reference something specific from the job description
- For Yes/No or checkbox questions → give just the appropriate short answer
- Return ONLY the answer text, no preamble or explanation
"""
    return llm.chat_short([{"role": "user", "content": prompt}]).strip()


# ── Page interaction helpers ──────────────────────────────────────────────────

async def _safe_fill(page: Any, selector: str, value: str) -> bool:
    """Fill a form field if it exists. Returns True on success."""
    try:
        el = await page.query_selector(selector)
        if el and await el.is_visible():
            await el.fill(value)
            return True
    except Exception:
        pass
    return False


async def _safe_click(page: Any, selector: str) -> bool:
    """Click an element if it exists and is visible. Returns True on success."""
    try:
        el = await page.query_selector(selector)
        if el and await el.is_visible():
            await el.click()
            return True
    except Exception:
        pass
    return False


async def _upload_resume(page: Any, resume_path: str | Path) -> bool:
    """Upload resume to the first file input found on the page.

    Tries the PDF first; falls back to HTML if PDF doesn't exist.

    Args:
        page: Playwright page.
        resume_path: Path to resume PDF (or HTML fallback).

    Returns:
        True if upload succeeded.
    """
    path = Path(resume_path) if resume_path else Path("")
    if not path.exists():
        path = path.with_suffix(".html")
    if not path.exists():
        logger.warning("[APPLY] Resume file not found — skipping upload")
        return False

    try:
        file_input = await page.query_selector("input[type='file']")
        if file_input:
            await file_input.set_input_files(str(path))
            logger.info(f"[APPLY] Resume uploaded: {path.name}")
            return True
    except Exception as e:
        logger.warning(f"[APPLY] File upload failed: {e}")
    return False


async def _fill_custom_questions(page: Any, job: dict[str, Any]) -> None:
    """Scan for unfilled text inputs/textareas and answer them via LLM.

    Skips known standard fields (name, email, phone, LinkedIn, etc.).

    Args:
        page: Playwright page.
        job: Job dict for context.
    """
    _STANDARD_NAMES = {
        "name", "first_name", "last_name", "email", "phone", "phone_number",
        "linkedin", "github", "portfolio", "website", "resume", "cover_letter",
    }

    try:
        inputs = await page.query_selector_all(
            "input[type='text']:visible, input[type='number']:visible, textarea:visible"
        )
    except Exception:
        return

    for inp in inputs:
        try:
            name_attr = (await inp.get_attribute("name") or "").lower()
            if any(s in name_attr for s in _STANDARD_NAMES):
                continue

            current = await inp.input_value()
            if current.strip():
                continue  # already filled

            # Find question label
            inp_id = await inp.get_attribute("id") or ""
            label_text = ""

            if inp_id:
                label_el = await page.query_selector(f"label[for='{inp_id}']")
                if label_el:
                    label_text = (await label_el.inner_text()).strip()

            if not label_text:
                placeholder = (await inp.get_attribute("placeholder") or "").strip()
                if len(placeholder) > 8:
                    label_text = placeholder

            if not label_text or len(label_text) < 5:
                continue

            answer = _answer_custom_question(label_text, job)
            await inp.fill(answer)
            logger.info(f"[APPLY] Custom Q answered: {label_text[:50]!r}")

        except Exception:
            continue


async def _take_screenshot(page: Any, output_dir: Path, name: str = "form_filled") -> Path:
    """Take a full-page screenshot and save it to the job output directory.

    Args:
        page: Playwright page.
        output_dir: Directory to save the screenshot.
        name: File name (without extension).

    Returns:
        Path to the saved screenshot.
    """
    path = output_dir / f"{name}.png"
    try:
        await page.screenshot(path=str(path), full_page=True)
        logger.info(f"[APPLY] Screenshot saved: {path.name}")
    except Exception as e:
        logger.warning(f"[APPLY] Screenshot failed: {e}")
    return path


# ── ATS-specific form fillers ─────────────────────────────────────────────────

async def _fill_lever(page: Any, job: dict[str, Any], docs: dict[str, Any]) -> str:
    """Fill a Lever ATS application form.

    Lever apply URL is: jobs.lever.co/{company}/{job-id}/apply
    """
    url = job["url"]
    apply_url = url if url.endswith("/apply") else url.rstrip("/") + "/apply"

    await page.goto(apply_url, timeout=30_000)
    await page.wait_for_load_state("domcontentloaded")

    await _safe_fill(page, "input[name='name']", _NAME)
    await _safe_fill(page, "input[name='email']", _EMAIL)
    await _safe_fill(page, "input[name='phone']", _PHONE)
    await _safe_fill(page, "input[name='urls[LinkedIn]']", _LINKEDIN)

    resume_path = docs.get("resume_pdf", "")
    await _upload_resume(page, resume_path)

    await _fill_custom_questions(page, job)
    return "filled"


async def _fill_greenhouse(page: Any, job: dict[str, Any], docs: dict[str, Any]) -> str:
    """Fill a Greenhouse ATS application form."""
    url = job["url"]

    # Navigate and try to reach the application form
    await page.goto(url, timeout=30_000)
    await page.wait_for_load_state("domcontentloaded")

    # Click "Apply for this job" if present
    apply_clicked = await _safe_click(page, "a.btn-apply, a[href*='/application'], button.apply-button")
    if apply_clicked:
        await page.wait_for_load_state("domcontentloaded")

    await _safe_fill(page, "input#first_name", _FIRST)
    await _safe_fill(page, "input#last_name", _LAST)
    await _safe_fill(page, "input#email", _EMAIL)
    await _safe_fill(page, "input#phone", _PHONE)

    # LinkedIn — Greenhouse uses different selectors across versions
    for sel in [
        "input[name*='linkedin']",
        "input[placeholder*='linkedin' i]",
        "input[id*='linkedin' i]",
    ]:
        if await _safe_fill(page, sel, _LINKEDIN):
            break

    resume_path = docs.get("resume_pdf", "")
    await _upload_resume(page, resume_path)

    await _fill_custom_questions(page, job)
    return "filled"


async def _fill_ashby(page: Any, job: dict[str, Any], docs: dict[str, Any]) -> str:
    """Fill an Ashby ATS application form."""
    await page.goto(job["url"], timeout=30_000)
    await page.wait_for_load_state("domcontentloaded")

    # Ashby often has a modal Apply button
    await _safe_click(page, "button[data-variant='primary'], button:has-text('Apply')")
    await page.wait_for_load_state("domcontentloaded")

    # Ashby form — may be combined name or split
    if not await _safe_fill(page, "input[name='name']", _NAME):
        await _safe_fill(page, "input[name='firstName'], input[placeholder*='First']", _FIRST)
        await _safe_fill(page, "input[name='lastName'], input[placeholder*='Last']", _LAST)

    await _safe_fill(page, "input[type='email'], input[name='email']", _EMAIL)
    await _safe_fill(page, "input[type='tel'], input[name='phone']", _PHONE)
    await _safe_fill(page, "input[name='linkedin'], input[placeholder*='LinkedIn' i]", _LINKEDIN)

    resume_path = docs.get("resume_pdf", "")
    await _upload_resume(page, resume_path)

    await _fill_custom_questions(page, job)
    return "filled"


async def _fill_smartrecruiters(page: Any, job: dict[str, Any], docs: dict[str, Any]) -> str:
    """Fill a SmartRecruiters ATS application form."""
    await page.goto(job["url"], timeout=30_000)
    await page.wait_for_load_state("networkidle")

    # SmartRecruiters step 1: personal info
    await _safe_fill(page, "input[name='firstName'], input#firstName", _FIRST)
    await _safe_fill(page, "input[name='lastName'], input#lastName", _LAST)
    await _safe_fill(page, "input[name='email'], input#email", _EMAIL)
    await _safe_fill(page, "input[name='phone'], input#phone", _PHONE)
    await _safe_fill(page, "input[name='location'], input[placeholder*='location' i]", _LOCATION)

    resume_path = docs.get("resume_pdf", "")
    await _upload_resume(page, resume_path)

    await _fill_custom_questions(page, job)
    return "filled"


async def _fill_naukri(page: Any, job: dict[str, Any], docs: dict[str, Any]) -> str:
    """Navigate to Naukri job and click Apply. Requires prior Naukri login.

    Returns 'needs_manual' since Naukri login state cannot be guaranteed.
    """
    await page.goto(job["url"], timeout=30_000)
    await page.wait_for_load_state("domcontentloaded")

    # Click apply button — opens modal or redirects
    await _safe_click(page, ".apply-button, button.apply, #apply-button, a[class*='apply']")
    await asyncio.sleep(2)

    # Fill what we can if a form appears
    await _safe_fill(page, "input[name='name'], input[placeholder*='Name' i]", _NAME)
    await _safe_fill(page, "input[type='email'], input[placeholder*='Email' i]", _EMAIL)
    await _safe_fill(page, "input[type='tel'], input[placeholder*='Phone' i]", _PHONE)

    return "needs_manual"


async def _fill_darwinbox(page: Any, job: dict[str, Any], docs: dict[str, Any]) -> str:
    """Navigate to Darwinbox job and fill what's accessible."""
    await page.goto(job["url"], timeout=30_000)
    await page.wait_for_load_state("networkidle")

    await _safe_click(page, "button.apply-btn, a.apply-now, [class*='apply']")
    await asyncio.sleep(2)

    await _safe_fill(page, "input[name='name'], input[placeholder*='Name' i]", _NAME)
    await _safe_fill(page, "input[type='email'], input[placeholder*='Email' i]", _EMAIL)
    await _safe_fill(page, "input[type='tel'], input[placeholder*='Phone' i]", _PHONE)

    resume_path = docs.get("resume_pdf", "")
    await _upload_resume(page, resume_path)

    return "needs_manual"


async def _fill_keka(page: Any, job: dict[str, Any], docs: dict[str, Any]) -> str:
    """Navigate to Keka ATS job and fill what's accessible."""
    await page.goto(job["url"], timeout=30_000)
    await page.wait_for_load_state("networkidle")

    await _safe_click(page, "button.apply-btn, a[class*='apply'], button[class*='apply']")
    await asyncio.sleep(2)

    await _safe_fill(page, "input[placeholder*='First Name' i], input[name='firstName']", _FIRST)
    await _safe_fill(page, "input[placeholder*='Last Name' i], input[name='lastName']", _LAST)
    await _safe_fill(page, "input[type='email'], input[placeholder*='Email' i]", _EMAIL)
    await _safe_fill(page, "input[type='tel'], input[placeholder*='Phone' i]", _PHONE)

    resume_path = docs.get("resume_pdf", "")
    await _upload_resume(page, resume_path)

    return "needs_manual"


async def _fill_zoho_recruit(page: Any, job: dict[str, Any], docs: dict[str, Any]) -> str:
    """Navigate to Zoho Recruit job and fill what's accessible."""
    await page.goto(job["url"], timeout=30_000)
    await page.wait_for_load_state("networkidle")

    await _safe_click(page, "a.apply-link, button.applyBtn, [class*='apply']")
    await asyncio.sleep(2)

    await _safe_fill(page, "input[name='First_Name'], input[placeholder*='First' i]", _FIRST)
    await _safe_fill(page, "input[name='Last_Name'], input[placeholder*='Last' i]", _LAST)
    await _safe_fill(page, "input[name='Email'], input[type='email']", _EMAIL)
    await _safe_fill(page, "input[name='Mobile'], input[type='tel']", _PHONE_DIGITS)

    resume_path = docs.get("resume_pdf", "")
    await _upload_resume(page, resume_path)

    await _fill_custom_questions(page, job)
    return "needs_manual"


async def _fill_workday(page: Any, job: dict[str, Any], docs: dict[str, Any]) -> str:
    """Navigate to Workday job and fill basic fields. Requires human submission."""
    await page.goto(job["url"], timeout=30_000)
    await page.wait_for_load_state("networkidle")

    # Workday Apply button
    await _safe_click(page, "a[data-automation-id='applyNowButton'], button[data-automation-id*='apply']")
    await asyncio.sleep(3)

    # Workday step 1 — personal info
    await _safe_fill(page, "input[data-automation-id='legalNameSection_firstName']", _FIRST)
    await _safe_fill(page, "input[data-automation-id='legalNameSection_lastName']", _LAST)
    await _safe_fill(page, "input[data-automation-id='email']", _EMAIL)
    await _safe_fill(page, "input[data-automation-id='phone-number']", _PHONE_DIGITS)

    return "needs_manual"


async def _fill_taleo(page: Any, job: dict[str, Any], docs: dict[str, Any]) -> str:
    """Navigate to Taleo job and fill basic fields. Requires human submission."""
    await page.goto(job["url"], timeout=30_000)
    await page.wait_for_load_state("networkidle")

    await _safe_fill(page, "input[id*='firstName'], input[name*='firstName']", _FIRST)
    await _safe_fill(page, "input[id*='lastName'], input[name*='lastName']", _LAST)
    await _safe_fill(page, "input[id*='email'], input[type='email']", _EMAIL)
    await _safe_fill(page, "input[id*='phone'], input[type='tel']", _PHONE_DIGITS)

    return "needs_manual"


# ── Submit handlers ───────────────────────────────────────────────────────────

async def _submit_form(page: Any, ats: ATS) -> bool:
    """Click the submit/apply button for the given ATS. Returns True on success."""
    _SUBMIT_SELECTORS: dict[ATS, list[str]] = {
        ATS.LEVER: [
            "input[type='submit'].submit-app-btn",
            "button[type='submit']",
            "input[type='submit']",
        ],
        ATS.GREENHOUSE: [
            "input#submit_app",
            "button#submit_app",
            "input[type='submit']",
            "button[type='submit']",
        ],
        ATS.ASHBY: [
            "button[type='submit']",
            "button:has-text('Submit application')",
            "button:has-text('Submit')",
        ],
        ATS.SMARTRECRUITERS: [
            "button[type='submit']",
            "button[data-button-type='submit']",
        ],
    }
    selectors = _SUBMIT_SELECTORS.get(ats, ["button[type='submit']", "input[type='submit']"])

    for sel in selectors:
        if await _safe_click(page, sel):
            try:
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except Exception:
                pass
            logger.info(f"[APPLY] Submit clicked (selector: {sel!r})")
            return True

    logger.warning("[APPLY] Could not find submit button")
    return False


# ── Browser-Use (universal apply engine for unknown ATS) ──────────────────────

def _get_browser_use_llm():
    """Return browser-use's native ChatAzureOpenAI for GPT-5.4.

    Browser-use v0.12+ uses its own LLM protocol (not LangChain). The agent
    calls llm.ainvoke(messages, output_format=AgentOutput) and expects a
    ChatInvokeCompletion back. LangChain LLMs return AIMessage and break this.
    Use browser_use.llm.azure.chat.ChatAzureOpenAI which implements the protocol
    correctly and handles structured output via OpenAI JSON schema mode.

    Falls back to the same class with the Kimi endpoint if GPT-5 creds are absent
    (Kimi still may fail on schema validation but won't crash structurally).
    """
    from browser_use.llm.azure.chat import ChatAzureOpenAI as BrowserUseAzureLLM

    if config.GPT5_AZURE_OPENAI_ENDPOINT and config.GPT5_AZURE_OPENAI_API_KEY:
        return BrowserUseAzureLLM(
            model=config.GPT5_AZURE_OPENAI_DEPLOYMENT,
            api_key=config.GPT5_AZURE_OPENAI_API_KEY,
            azure_endpoint=config.GPT5_AZURE_OPENAI_ENDPOINT,
            azure_deployment=config.GPT5_AZURE_OPENAI_DEPLOYMENT,
            api_version="2024-12-01-preview",
            temperature=0.2,
            max_completion_tokens=4096,
            remove_min_items_from_schema=True,
        )

    logger.warning("[APPLY] GPT5_AZURE_OPENAI_ENDPOINT/KEY not set — falling back to Kimi endpoint")
    base_url = config.AZURE_AI_ENDPOINT
    if "?" in base_url:
        base_url = base_url.split("?")[0]
    if base_url.endswith("/chat/completions"):
        base_url = base_url[: -len("/chat/completions")]
    return BrowserUseAzureLLM(
        model=config.AZURE_AI_DEPLOYMENT,
        api_key=config.AZURE_AI_API_KEY,
        base_url=base_url,
        api_version="2024-05-01-preview",
        temperature=0.2,
        max_completion_tokens=1024,
        remove_min_items_from_schema=True,
    )


async def apply_with_browser_use(job: dict[str, Any], docs: dict[str, Any]) -> str:
    """Apply to any job URL using Browser-Use AI agent — no hardcoded selectors.

    Compatible with browser-use v0.12+. Uses BrowserProfile (new API).
    The LLM navigates the page visually, fills all form fields, and sends a
    screenshot to Telegram for confirmation before submitting.

    Falls back to 'unknown_ats' if browser-use is not installed or the page
    has no application form.

    Args:
        job: Scored job dict (title, company, url, ...).
        docs: Output from generator.generate_docs() with resume_pdf, output_dir.

    Returns:
        Status string: "submitted" | "filled_skipped" | "login_required" | "job_expired" | "no_form" | "submit_failed" | "error"
    """
    try:
        from browser_use import Agent as BrowserAgent
        from browser_use.browser import BrowserProfile
    except ImportError:
        logger.warning("[APPLY] browser-use not installed — pip install browser-use langchain-openai")
        return "error"

    title = job.get("title", "Unknown Role")
    company = job.get("company", "Unknown Company")
    url = job.get("url", "")
    output_dir = Path(docs.get("output_dir", str(config.APPLICATIONS_DIR)))
    output_dir.mkdir(parents=True, exist_ok=True)
    resume_path = docs.get("resume_pdf", "")
    cover_text = (docs.get("cover_letter", "") or "")[:600]

    logger.info(f"[APPLY] Browser-Use: {title} @ {company} — {url}")

    try:
        kimi_llm = _get_browser_use_llm()
    except Exception as e:
        logger.error(f"[APPLY] Browser-Use LLM setup failed: {e}")
        return "error"

    fill_task = (
        f"Go to {url} and apply for the job '{title}' at {company}.\n\n"
        f"Fill every visible application form field using:\n"
        f"  Full Name: {_NAME}\n"
        f"  First Name: {_FIRST}\n"
        f"  Last Name: {_LAST}\n"
        f"  Email: {_EMAIL}\n"
        f"  Phone: {_PHONE}\n"
        f"  LinkedIn URL: {_LINKEDIN}\n"
        f"  Location: {_LOCATION}\n"
        f"  University: {_COLLEGE}\n"
        f"  Degree: {_DEGREE}\n"
        f"  Graduation Year: {_GRAD_YEAR}\n"
        f"  GPA/CGPA: {_GPA}\n"
        f"  Years of Experience: 0 (fresher/intern)\n"
        f"  Notice Period: Immediate\n"
        f"  Current CTC: Fresher\n"
        f"  Expected CTC: Open to market rate\n"
        f"  Resume file to upload: {resume_path}\n"
        + (f"  Cover Letter (use if asked): {cover_text}\n" if cover_text else "")
        + "\nFollow these steps exactly:\n"
        "1. Navigate to the URL.\n"
        "2. If there is an 'Apply' or 'Apply Now' button, click it.\n"
        "3. Fill all visible form fields with the info above.\n"
        "4. Upload the resume file to any file upload input you find.\n"
        "5. Do NOT click the final Submit/Apply button — stop before that.\n"
        "6. Return exactly one of: 'filled' (form filled successfully), "
        "'no_form' (no application form found on this page), "
        "'needs_login' (login wall encountered, cannot proceed), "
        "'expired' (job is closed or no longer accepting applications)."
    )

    # Use a persistent profile dir so login sessions (cookies) survive between runs.
    # First run: log in manually to any site — sessions are saved automatically.
    browser_profile_dir = Path(config.BASE_DIR) / ".browser_profile"
    browser_profile_dir.mkdir(exist_ok=True)

    profile = BrowserProfile(
        headless=False,
        disable_security=True,
        user_data_dir=str(browser_profile_dir),
        extra_chromium_args=["--disable-blink-features=AutomationControlled"],
        keep_alive=True,  # keep browser open after agent.run() for screenshot + submit
    )

    agent = BrowserAgent(
        task=fill_task,
        llm=kimi_llm,
        browser_profile=profile,
        max_failures=3,
        available_file_paths=[resume_path] if resume_path else [],
    )

    try:
        history = await agent.run()
        raw_final = str(history.final_result() or "")
        # Agent is instructed to return exactly one token as the first word; parse only that.
        first_token = raw_final.strip().split()[0].lower().rstrip(".,;:") if raw_final.strip() else ""
        final = raw_final.lower()

        session = agent.browser_session

        if first_token == "no_form" or first_token == "no":
            logger.info(f"[APPLY] Browser-Use: no form found at {url}")
            if session:
                await session.stop()
            return "no_form"

        if first_token == "needs_login":
            logger.info(f"[APPLY] Browser-Use: login required at {url}")
            if session:
                await session.stop()
            return "login_required"

        if first_token == "expired":
            logger.info(f"[APPLY] Browser-Use: job expired at {url}")
            if session:
                await session.stop()
            return "job_expired"

        # Screenshot of the filled form
        screenshot_path = output_dir / "form_filled_bu.png"
        try:
            if session:
                await session.take_screenshot(path=str(screenshot_path), full_page=True)
                logger.info(f"[APPLY] Browser-Use screenshot saved")
        except Exception as e:
            logger.warning(f"[APPLY] Browser-Use screenshot failed: {e}")
            screenshot_path = output_dir / "no_screenshot.png"

        decision = await notifier.send_apply_confirmation(job, screenshot_path)

        if decision == "submit":
            # Run a second agent to click submit — reuses the same browser session
            submit_agent = BrowserAgent(
                task=(
                    "The application form is already filled. "
                    "Find and click the Submit, Apply, or Send Application button to submit the form. "
                    "After clicking, wait for the confirmation page. "
                    "Return 'submitted' if successful, 'not_found' if no submit button found."
                ),
                llm=kimi_llm,
                browser_session=session,
                max_failures=2,
                available_file_paths=[resume_path] if resume_path else [],
            )
            try:
                submit_history = await submit_agent.run()
                submit_result = str(submit_history.final_result() or "").lower()

                if "not_found" not in submit_result:
                    await asyncio.sleep(2)
                    if session:
                        await session.take_screenshot(
                            path=str(output_dir / "form_submitted.png"), full_page=True
                        )
                    await notifier.send_message(
                        f"Application *submitted* for *{title}* @ {company}!"
                    )
                    if session:
                        await session.stop()
                    return "submitted"
                else:
                    if session:
                        await session.stop()
                    return "submit_failed"

            except Exception as e:
                logger.error(f"[APPLY] Browser-Use submit agent failed: {e}")
                if session:
                    await session.stop()
                return "submit_failed"

        else:
            if session:
                await session.stop()
            return "filled_skipped"

    except Exception as exc:
        logger.error(f"[APPLY] Browser-Use failed for {title} @ {company}: {exc}")
        try:
            if agent.browser_session:
                await agent.browser_session.stop()
        except Exception:
            pass
        return "error"


# ── Public API ────────────────────────────────────────────────────────────────

async def apply_to_job(job: dict[str, Any], docs: dict[str, Any]) -> str:
    """Apply to a job using the appropriate ATS strategy.

    Full flow:
      1. Detect ATS from job URL.
      2. Open browser, fill the application form.
      3. Take screenshot of filled form.
      4. For full-auto ATS: send screenshot + Telegram confirm → submit if approved.
      5. For semi-auto/fill-only: send screenshot + manual submission notification.
      6. Close browser and return status string.

    Args:
        job: Scored job dict (title, company, url, description, ...).
        docs: Output of generator.generate_docs() with resume_pdf, cover_letter_pdf, output_dir.

    Returns:
        Status string: "submitted" | "filled_skipped" | "needs_human" | "aggregator" | "submit_failed" | "failed"
    """
    ats = detect_ats(job["url"])
    title = job.get("title", "Unknown Role")
    company = job.get("company", "Unknown Company")
    output_dir = Path(docs.get("output_dir", config.APPLICATIONS_DIR))

    if ats == ATS.UNKNOWN:
        url_lower = job["url"].lower()
        if any(domain in url_lower for domain in _AGGREGATOR_DOMAINS):
            logger.info(f"[APPLY] Aggregator/login-wall URL — skipping: {job['url']}")
            return "aggregator"
        logger.info(f"[APPLY] Unknown ATS for {job['url']} — trying Browser-Use")
        return await apply_with_browser_use(job, docs)

    logger.info(f"[APPLY] Starting apply — {title} @ {company} (ATS: {ats.value})")

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.error("[APPLY] playwright not installed")
        return "failed"

    try:
        async with async_playwright() as pw:
            browser = None
            for channel in ("chrome", "msedge"):
                try:
                    browser = await pw.chromium.launch(headless=True, channel=channel)
                    break
                except Exception:
                    continue
            if browser is None:
                browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context(user_agent=_CHROME_UA)
            page = await context.new_page()

            # Route to ATS-specific filler
            _fillers = {
                ATS.LEVER: _fill_lever,
                ATS.GREENHOUSE: _fill_greenhouse,
                ATS.ASHBY: _fill_ashby,
                ATS.SMARTRECRUITERS: _fill_smartrecruiters,
                ATS.NAUKRI: _fill_naukri,
                ATS.DARWINBOX: _fill_darwinbox,
                ATS.KEKA: _fill_keka,
                ATS.ZOHO_RECRUIT: _fill_zoho_recruit,
                ATS.WORKDAY: _fill_workday,
                ATS.TALEO: _fill_taleo,
            }
            filler = _fillers[ats]
            fill_result = await filler(page, job, docs)

            # Screenshot of the filled form
            screenshot_path = await _take_screenshot(page, output_dir)

            # ── Full-auto ATS: screenshot + confirm + submit ────────────────
            if ats in _FULL_AUTO_ATS:
                decision = await notifier.send_apply_confirmation(job, screenshot_path)

                if decision == "submit":
                    submitted = await _submit_form(page, ats)
                    if submitted:
                        # Final success screenshot
                        await _take_screenshot(page, output_dir, name="form_submitted")
                        await notifier.send_message(
                            f"Application *submitted* for *{title}* @ {company}!"
                        )
                        logger.info(f"[APPLY] Submitted: {title} @ {company}")
                        await browser.close()
                        return "submitted"
                    else:
                        await browser.close()
                        return "submit_failed"
                else:
                    logger.info(f"[APPLY] Submission skipped by user: {title} @ {company}")
                    await browser.close()
                    return "filled_skipped"

            # ── Semi-auto / fill-only ATS: notify and close ────────────────
            else:
                mode_note = (
                    "Form auto-filled — login required or manual submission needed."
                    if ats in _SEMI_AUTO_ATS
                    else "Form partially filled — this ATS requires manual submission."
                )
                await notifier.send_apply_notification(job, screenshot_path, mode_note)
                logger.info(f"[APPLY] Needs manual submission: {title} @ {company}")
                await browser.close()
                return "needs_human"

    except Exception as exc:
        logger.error(f"[APPLY] Failed for {title} @ {company}: {exc}")
        return "failed"


if __name__ == "__main__":
    import asyncio

    config.validate()
    sample_job = {
        "title": "Data Analyst Intern",
        "company": "Acme Corp",
        "url": "https://jobs.lever.co/acme/abc123",
        "description": "Looking for a data analyst intern.",
        "source": "lever",
    }
    sample_docs = {
        "output_dir": str(config.APPLICATIONS_DIR / "acme_corp-data_analyst_intern"),
        "resume_pdf": str(config.APPLICATIONS_DIR / "acme_corp-data_analyst_intern/resume.pdf"),
        "cover_letter_pdf": "",
    }
    print(f"ATS detected: {detect_ats(sample_job['url'])}")

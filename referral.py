"""LinkedIn referral outreach — find employees and send personalized connection requests."""

import asyncio
import json
import random
from pathlib import Path
from typing import Any

from loguru import logger
from playwright.async_api import BrowserContext, Page, async_playwright

import config
import llm
import tracker

_DAILY_LIMIT = 20
_SESSION_FILE = Path(config.BASE_DIR) / "linkedin_session.json"
_CAREERS_CONFIG = Path(config.BASE_DIR) / "careers_config.json"

_CANDIDATE_FIRST = "Namrata"
_CANDIDATE_NAME = "Namrata Sahu"
_CANDIDATE_COLLEGE = "Delhi Technological University"
_CANDIDATE_GRAD = "2026"
_CANDIDATE_ROLES = "data analyst, data engineer, or product manager"

_CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Search query templates — run all three per company, each yields up to 3 profiles
_SEARCH_QUERIES = [
    "recruiter {company}",
    "data analyst {company}",
    "Delhi Technological University {company}",
]


# ── Session management ────────────────────────────────────────────────────────

async def _load_session(context: BrowserContext) -> None:
    """Load persisted LinkedIn cookies into the browser context."""
    if not _SESSION_FILE.exists():
        return
    try:
        cookies = json.loads(_SESSION_FILE.read_text(encoding="utf-8"))
        await context.add_cookies(cookies)
        logger.info("[REFERRAL] Session cookies loaded.")
    except Exception as exc:
        logger.warning(f"[REFERRAL] Could not load session cookies: {exc}")


async def _save_session(context: BrowserContext) -> None:
    """Persist current browser cookies to linkedin_session.json."""
    try:
        cookies = await context.cookies()
        _SESSION_FILE.write_text(
            json.dumps(cookies, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("[REFERRAL] Session saved.")
    except Exception as exc:
        logger.warning(f"[REFERRAL] Could not save session: {exc}")


async def _ensure_logged_in(page: Page) -> bool:
    """Navigate to the LinkedIn feed and log in with credentials if needed.

    Returns:
        True if the session is authenticated, False on failure.
    """
    await page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded")
    await asyncio.sleep(2.0)

    if "feed" in page.url or "mynetwork" in page.url:
        logger.info("[REFERRAL] Already logged in.")
        return True

    email = config.LINKEDIN_EMAIL
    password = config.LINKEDIN_PASSWORD
    if not email or not password:
        logger.error("[REFERRAL] LINKEDIN_EMAIL / LINKEDIN_PASSWORD not set in .env — cannot log in.")
        return False

    logger.info("[REFERRAL] Logging in to LinkedIn...")
    try:
        await page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")
        await page.fill("#username", email)
        await page.fill("#password", password)
        await page.click("button[type='submit']")
        await page.wait_for_url("**/feed/**", timeout=20_000)
        logger.info("[REFERRAL] Login successful.")
        return True
    except Exception as exc:
        logger.error(f"[REFERRAL] Login failed: {exc}")
        return False


# ── Profile search ────────────────────────────────────────────────────────────

async def _search_employees(page: Page, query: str, max_results: int = 3) -> list[dict[str, Any]]:
    """Search LinkedIn people by query string and return up to max_results profiles.

    Args:
        page: Active Playwright page (logged-in LinkedIn session).
        query: Free-text search query, e.g. "recruiter Razorpay".
        max_results: Max profiles to extract from the first result page.

    Returns:
        List of dicts with keys: name, title, url.
    """
    url = (
        "https://www.linkedin.com/search/results/people/"
        f"?keywords={query.replace(' ', '+')}"
        "&origin=GLOBAL_SEARCH_HEADER"
    )
    profiles: list[dict[str, Any]] = []

    try:
        await page.goto(url, wait_until="domcontentloaded")
        await asyncio.sleep(random.uniform(2.5, 4.0))

        cards = await page.query_selector_all(".reusable-search__result-container")
        for card in cards[:max_results]:
            try:
                name_el = await card.query_selector(
                    ".entity-result__title-text a span[aria-hidden='true']"
                )
                title_el = await card.query_selector(".entity-result__primary-subtitle")
                link_el = await card.query_selector(".entity-result__title-text a")

                name = (await name_el.inner_text()).strip() if name_el else ""
                title = (await title_el.inner_text()).strip() if title_el else ""
                href = await link_el.get_attribute("href") if link_el else ""

                if name and href:
                    profile_url = href.split("?")[0].rstrip("/")
                    profiles.append({"name": name, "title": title, "url": profile_url})
            except Exception:
                continue

    except Exception as exc:
        logger.warning(f"[REFERRAL] Search failed for {query!r}: {exc}")

    logger.info(f"[REFERRAL] '{query}' → {len(profiles)} profile(s)")
    return profiles


# ── Message generation ────────────────────────────────────────────────────────

async def _write_outreach_message(person: dict[str, Any], company: str) -> str:
    """Ask Kimi to write a personalized LinkedIn connection note (max 300 chars).

    Args:
        person: Dict with name and title of the recipient.
        company: Company name.

    Returns:
        Connection note string, max 300 characters.
    """
    prompt = (
        f"Write a LinkedIn connection request note FROM {_CANDIDATE_NAME}, "
        f"a final-year B.Tech student at {_CANDIDATE_COLLEGE}, graduating {_CANDIDATE_GRAD}. "
        f"She is looking for {_CANDIDATE_ROLES} roles (internship or entry-level). "
        f"She is reaching out to {person['name']} "
        f"({person['title'] or 'employee'}) who works at {company}. "
        f"The note must be warm, specific, and genuine — NOT generic. "
        f"Mention {company} by name. Do NOT use placeholder text like [Your Name]. "
        f"STRICT hard limit: 300 characters total (LinkedIn enforces this). "
        f"Return ONLY the note text, nothing else — no quotes, no label."
    )
    try:
        message = llm.chat_short([{"role": "user", "content": prompt}]).strip()
        if len(message) > 300:
            message = message[:297] + "..."
        return message
    except Exception as exc:
        logger.error(f"[REFERRAL] Message generation failed: {exc}")
        first_name = person["name"].split()[0] if person["name"] else "there"
        return (
            f"Hi {first_name}, I'm Namrata, a final-year DTU student seeking "
            f"data/PM roles. I admire {company}'s work and would love to connect!"
        )[:300]


# ── Connection request ────────────────────────────────────────────────────────

async def _send_connection_request(page: Page, profile_url: str, message: str) -> bool:
    """Navigate to a LinkedIn profile and send a connection request with a note.

    Args:
        page: Active Playwright page.
        profile_url: Full LinkedIn profile URL.
        message: Personalized connection note (max 300 chars).

    Returns:
        True if the request was sent successfully.
    """
    try:
        await page.goto(profile_url, wait_until="domcontentloaded")
        await asyncio.sleep(random.uniform(3.0, 5.0))

        connect_clicked = False

        # Try direct Connect button first
        for selector in [
            "button[aria-label*='Invite'][aria-label*='connect']",
            "button[aria-label='Connect']",
            "main button:has-text('Connect')",
        ]:
            btn = await page.query_selector(selector)
            if btn and await btn.is_visible():
                await btn.click()
                connect_clicked = True
                break

        # Fallback: More actions dropdown → Connect
        if not connect_clicked:
            more_btn = await page.query_selector(
                "button[aria-label='More actions']"
            )
            if more_btn and await more_btn.is_visible():
                await more_btn.click()
                await asyncio.sleep(1.0)
                for sel in [
                    "div[aria-label*='connect' i]",
                    "span:has-text('Connect')",
                ]:
                    item = await page.query_selector(sel)
                    if item and await item.is_visible():
                        await item.click()
                        connect_clicked = True
                        break

        if not connect_clicked:
            logger.warning(f"[REFERRAL] No Connect button found: {profile_url}")
            return False

        await asyncio.sleep(1.5)

        # Click "Add a note" in the modal
        for sel in [
            "button[aria-label='Add a note']",
            "button:has-text('Add a note')",
        ]:
            add_note = await page.query_selector(sel)
            if add_note and await add_note.is_visible():
                await add_note.click()
                await asyncio.sleep(1.0)
                break

        # Fill the note textarea
        for sel in ["textarea#custom-message", "textarea[name='message']", "textarea"]:
            note_area = await page.query_selector(sel)
            if note_area and await note_area.is_visible():
                await note_area.fill(message)
                await asyncio.sleep(0.5)
                break

        # Send the invitation
        for sel in [
            "button[aria-label='Send invitation']",
            "button[aria-label='Send now']",
            "button:has-text('Send')",
        ]:
            send_btn = await page.query_selector(sel)
            if send_btn and await send_btn.is_visible():
                await send_btn.click()
                # Post-send cooldown — critical for LinkedIn rate limit safety
                await asyncio.sleep(random.uniform(12.0, 20.0))
                logger.info(f"[REFERRAL] Request sent → {profile_url}")
                return True

        logger.warning(f"[REFERRAL] Send button not found: {profile_url}")
        return False

    except Exception as exc:
        logger.error(f"[REFERRAL] Connection request failed for {profile_url}: {exc}")
        return False


# ── Main orchestrator ─────────────────────────────────────────────────────────

async def run_outreach(limit: int = _DAILY_LIMIT) -> dict[str, int]:
    """Run LinkedIn referral outreach for all configured companies.

    For each company, searches for:
      - Recruiters / HR ("recruiter {company}")
      - Relevant team members ("data analyst {company}")
      - DTU alumni ("Delhi Technological University {company}")

    Sends personalized connection requests up to `limit` per session.

    Args:
        limit: Max connection requests this run (LinkedIn hard cap = 20/day).

    Returns:
        Dict with keys "sent", "skipped", "failed".
    """
    if not _CAREERS_CONFIG.exists():
        logger.error("[REFERRAL] careers_config.json not found.")
        return {"sent": 0, "skipped": 0, "failed": 0}

    companies = json.loads(_CAREERS_CONFIG.read_text(encoding="utf-8")).get("companies", [])
    company_names = [c["name"] for c in companies]

    already_sent: set[str] = tracker.get_all_referral_urls()
    stats: dict[str, int] = {"sent": 0, "skipped": 0, "failed": 0}

    async with async_playwright() as pw:
        # headless=False — LinkedIn detects headless browsers and may CAPTCHA
        browser = None
        for channel in ("chrome", "msedge"):
            try:
                browser = await pw.chromium.launch(headless=False, slow_mo=50, channel=channel)
                break
            except Exception:
                continue
        if browser is None:
            browser = await pw.chromium.launch(headless=False, slow_mo=50)
        context = await browser.new_context(
            user_agent=_CHROME_UA,
            viewport={"width": 1280, "height": 800},
        )
        page = await context.new_page()

        await _load_session(context)

        if not await _ensure_logged_in(page):
            await browser.close()
            return stats

        await _save_session(context)

        for company in company_names:
            if stats["sent"] >= limit:
                break

            for query_template in _SEARCH_QUERIES:
                if stats["sent"] >= limit:
                    break

                query = query_template.format(company=company)
                profiles = await _search_employees(page, query, max_results=3)
                await asyncio.sleep(random.uniform(4.0, 7.0))

                for person in profiles:
                    if stats["sent"] >= limit:
                        break

                    profile_url = person["url"]

                    if profile_url in already_sent:
                        logger.info(
                            f"[REFERRAL] Already messaged {person['name']} — skipping."
                        )
                        stats["skipped"] += 1
                        continue

                    message = await _write_outreach_message(person, company)
                    success = await _send_connection_request(page, profile_url, message)

                    if success:
                        stats["sent"] += 1
                        already_sent.add(profile_url)
                        tracker.log_outreach(
                            name=person["name"],
                            title=person["title"],
                            company=company,
                            profile_url=profile_url,
                            message=message,
                            status="pending_reply",
                        )
                        logger.info(
                            f"[REFERRAL] [{stats['sent']}/{limit}] "
                            f"{person['name']} @ {company}"
                        )
                    else:
                        stats["failed"] += 1

        await browser.close()

    logger.info(
        f"[REFERRAL] Done — sent={stats['sent']} "
        f"skipped={stats['skipped']} failed={stats['failed']}"
    )
    return stats


if __name__ == "__main__":
    config.validate()
    result = asyncio.run(run_outreach(limit=5))
    print(f"Test run complete: {result}")

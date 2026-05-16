"""Tier 3 scraper — company career pages via Lever/Greenhouse APIs and Playwright."""

import json
import random
import re
import time
import urllib.parse
from pathlib import Path
from typing import Any

from loguru import logger

_CAREERS_CONFIG = Path(__file__).parent / "careers_config.json"

_CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def _pw_launch(pw: Any):
    """Launch system Chrome/Edge headless — no Playwright binary download required."""
    for channel in ("chrome", "msedge"):
        try:
            return pw.chromium.launch(headless=True, channel=channel)
        except Exception:
            continue
    return pw.chromium.launch(headless=True)  # last resort

_JOB_TITLE_RE = re.compile(
    r"\b(engineer|analyst|developer|scientist|manager|intern|associate|"
    r"lead|designer|product|data|backend|frontend|full.?stack|devops|"
    r"sde|swe|pm|ba|architect|consultant|specialist|coordinator)\b",
    re.IGNORECASE,
)

_JOB_HREF_RE = re.compile(
    r"/(job|career|open|role|position|apply|posting|opportunit|vacancy)",
    re.IGNORECASE,
)


def _load_config() -> list[dict[str, Any]]:
    """Load careers_config.json and return the companies list."""
    return json.loads(_CAREERS_CONFIG.read_text(encoding="utf-8"))["companies"]


def _dedup(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate by lowercase-stripped URL."""
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for job in jobs:
        key = (job.get("url") or "").strip().lower()
        if key and key not in seen:
            seen.add(key)
            unique.append(job)
    return unique


# ── ATS-specific scrapers ─────────────────────────────────────────────────────

def _scrape_lever_api(company: dict[str, Any]) -> list[dict[str, Any]]:
    """Fetch job listings from the Lever public JSON API.

    Args:
        company: Company dict from careers_config.json (must have board_id).

    Returns:
        List of normalized job dicts, or [] on failure.
    """
    board_id = company.get("board_id", "")
    if not board_id:
        return []

    import urllib.request

    api_url = f"https://api.lever.co/v0/postings/{board_id}?mode=json"
    try:
        req = urllib.request.Request(api_url, headers={"User-Agent": _CHROME_UA})
        with urllib.request.urlopen(req, timeout=15) as resp:
            postings = json.loads(resp.read().decode("utf-8"))

        jobs: list[dict[str, Any]] = []
        for p in postings:
            categories = p.get("categories") or {}
            location = categories.get("location") or categories.get("allLocations") or "India"
            jobs.append({
                "title": p.get("text", ""),
                "company": company["name"],
                "location": location if isinstance(location, str) else ", ".join(location),
                "url": p.get("hostedUrl", ""),
                "description": (p.get("descriptionPlain") or "")[:600],
                "source": company["name"],
                "posted_date": "",
            })

        return [j for j in jobs if j["title"] and j["url"]]

    except Exception as exc:
        logger.warning(f"[TIER3] Lever API failed for {company['name']}: {exc}")
        return []


def _scrape_greenhouse_api(company: dict[str, Any]) -> list[dict[str, Any]]:
    """Fetch job listings from the Greenhouse public JSON API.

    Args:
        company: Company dict from careers_config.json (must have board_id).

    Returns:
        List of normalized job dicts, or [] on failure.
    """
    board_id = company.get("board_id", "")
    if not board_id:
        return []

    import urllib.request

    api_url = f"https://boards-api.greenhouse.io/v1/boards/{board_id}/jobs"
    try:
        req = urllib.request.Request(api_url, headers={"User-Agent": _CHROME_UA})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        jobs: list[dict[str, Any]] = []
        for j in data.get("jobs", []):
            location_obj = j.get("location") or {}
            jobs.append({
                "title": j.get("title", ""),
                "company": company["name"],
                "location": location_obj.get("name", "India") if isinstance(location_obj, dict) else str(location_obj),
                "url": j.get("absolute_url", ""),
                "description": "",
                "source": company["name"],
                "posted_date": j.get("updated_at", ""),
            })

        return [j for j in jobs if j["title"] and j["url"]]

    except Exception as exc:
        logger.warning(f"[TIER3] Greenhouse API failed for {company['name']}: {exc}")
        return []


def _scrape_playwright_darwinbox(url: str, company_name: str) -> list[dict[str, Any]]:
    """Scrape a Darwinbox career portal via Playwright.

    Args:
        url: Career page URL.
        company_name: Company display name.

    Returns:
        List of normalized job dicts.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.error("[TIER3] Darwinbox scraper requires playwright")
        return []

    jobs: list[dict[str, Any]] = []
    fallback = False
    try:
        with sync_playwright() as pw:
            browser = _pw_launch(pw)
            context = browser.new_context(user_agent=_CHROME_UA)
            page = context.new_page()

            logger.info(f"[TIER3] Darwinbox {company_name}: {url}")
            page.goto(url, timeout=45_000, wait_until="domcontentloaded")

            try:
                page.wait_for_selector(
                    ".job-card, [class*='job-listing'], [class*='opening'], [class*='position'], "
                    "[class*='vacancy'], .career-list-item",
                    timeout=15_000,
                )
            except Exception:
                logger.warning(f"[TIER3] Darwinbox {company_name}: no cards found, trying generic")
                browser.close()
                fallback = True

            if not fallback:
                cards = page.query_selector_all(
                    ".job-card, [class*='job-listing'], [class*='opening'], .career-list-item"
                )
                for card in cards:
                    try:
                        title_el = card.query_selector("h2, h3, [class*='title'], [class*='role'] a, a")
                        link_el = card.query_selector("a[href]")
                        location_el = card.query_selector("[class*='location'], [class*='city']")

                        title = title_el.inner_text().strip() if title_el else ""
                        link = link_el.get_attribute("href") if link_el else ""
                        loc = location_el.inner_text().strip() if location_el else "India"

                        if title and link:
                            parsed = urllib.parse.urlparse(url)
                            base = f"{parsed.scheme}://{parsed.netloc}"
                            full_url = link if link.startswith("http") else f"{base}{link}"
                            jobs.append({
                                "title": title,
                                "company": company_name,
                                "location": loc,
                                "url": full_url,
                                "description": "",
                                "source": company_name,
                                "posted_date": "",
                            })
                    except Exception:
                        continue

                browser.close()

    except Exception as exc:
        logger.error(f"[TIER3] Darwinbox {company_name} failed: {exc}")
        return []

    # Call generic AFTER the sync_playwright context has exited — avoids nesting two sync contexts
    if fallback:
        return _scrape_playwright_generic(url, company_name)

    return _dedup(jobs)


def _scrape_playwright_generic(url: str, company_name: str) -> list[dict[str, Any]]:
    """Generic Playwright scraper for custom career pages.

    Finds job-like anchor tags by matching href and text patterns.

    Args:
        url: Career page URL.
        company_name: Company display name.

    Returns:
        List of normalized job dicts.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.error("[TIER3] Generic scraper requires playwright")
        return []

    jobs: list[dict[str, Any]] = []
    parsed_base = urllib.parse.urlparse(url)
    base_origin = f"{parsed_base.scheme}://{parsed_base.netloc}"

    try:
        with sync_playwright() as pw:
            browser = _pw_launch(pw)
            context = browser.new_context(user_agent=_CHROME_UA)
            page = context.new_page()

            logger.info(f"[TIER3] Generic {company_name}: {url}")
            page.goto(url, timeout=45_000, wait_until="domcontentloaded")
            time.sleep(random.uniform(1.0, 2.0))

            links = page.query_selector_all("a[href]")
            seen_hrefs: set[str] = set()

            for link_el in links:
                try:
                    href = link_el.get_attribute("href") or ""
                    text = link_el.inner_text().strip()

                    if not text or not href or len(text) > 120 or len(text) < 4:
                        continue
                    if href in seen_hrefs:
                        continue

                    # Filter: text must look like a job title
                    if not _JOB_TITLE_RE.search(text):
                        continue

                    # Filter: href must look like a job detail link (not a category)
                    if not _JOB_HREF_RE.search(href):
                        continue

                    seen_hrefs.add(href)

                    if href.startswith("http"):
                        full_url = href
                    elif href.startswith("/"):
                        full_url = f"{base_origin}{href}"
                    else:
                        full_url = f"{url.rstrip('/')}/{href.lstrip('/')}"

                    jobs.append({
                        "title": text,
                        "company": company_name,
                        "location": "India",
                        "url": full_url,
                        "description": "",
                        "source": company_name,
                        "posted_date": "",
                    })
                except Exception:
                    continue

            browser.close()

    except Exception as exc:
        logger.error(f"[TIER3] Generic {company_name} failed: {exc}")
        return []

    return _dedup(jobs)


# ── Public API ────────────────────────────────────────────────────────────────

def scrape_company(company: dict[str, Any]) -> list[dict[str, Any]]:
    """Scrape a single company's career page using the appropriate ATS strategy.

    Strategy:
      - lever      → Lever JSON API (falls back to generic Playwright)
      - greenhouse → Greenhouse JSON API (falls back to generic Playwright)
      - darwinbox  → Darwinbox-specific Playwright (falls back to generic)
      - custom     → Generic Playwright link scanner

    Args:
        company: Company dict from careers_config.json.

    Returns:
        List of normalized job dicts.
    """
    ats = company.get("ats", "custom")
    url = company.get("url", "")
    name = company.get("name", "Unknown")

    if ats == "lever":
        jobs = _scrape_lever_api(company)
        if not jobs:
            logger.info(f"[TIER3] Lever API empty for {name}, trying Playwright")
            jobs = _scrape_playwright_generic(url, name)
        return jobs

    if ats == "greenhouse":
        jobs = _scrape_greenhouse_api(company)
        if not jobs:
            logger.info(f"[TIER3] Greenhouse API empty for {name}, trying Playwright")
            jobs = _scrape_playwright_generic(url, name)
        return jobs

    if ats == "darwinbox":
        return _scrape_playwright_darwinbox(url, name)

    # custom or unknown
    return _scrape_playwright_generic(url, name)


def scrape_all_companies(filter_keyword: str | None = None) -> list[dict[str, Any]]:
    """Scrape all companies in careers_config.json.

    Args:
        filter_keyword: If set, post-filter results to jobs whose title
                        contains this keyword (case-insensitive).

    Returns:
        Deduplicated list of all job dicts from all company pages.
    """
    companies = _load_config()
    all_jobs: list[dict[str, Any]] = []

    for company in companies:
        try:
            jobs = scrape_company(company)

            if filter_keyword:
                kw = filter_keyword.lower()
                jobs = [j for j in jobs if kw in (j.get("title") or "").lower()]

            logger.info(f"[TIER3] {company['name']}: {len(jobs)} jobs")
            all_jobs.extend(jobs)

        except Exception as exc:
            logger.error(f"[TIER3] {company['name']} crashed: {exc}")
            continue

    unique = _dedup(all_jobs)
    logger.info(f"[TIER3] Company pages complete: {len(unique)} unique jobs from {len(companies)} companies")
    return unique


if __name__ == "__main__":
    import config
    config.validate()
    sample = scrape_all_companies(filter_keyword="data analyst")
    logger.info(f"[TIER3] Sample results: {sample[:3]}")

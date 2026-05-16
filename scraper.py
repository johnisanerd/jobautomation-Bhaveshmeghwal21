"""Job board scrapers — Tier 1 (Apify + Playwright) and Tier 2 (Playwright)."""

import random
import time
import urllib.parse
from typing import Any

from apify_client import ApifyClient
from loguru import logger

import config

# ── Constants ─────────────────────────────────────────────────────────────────

ACTOR_IDS = {
    "indeed": "misceres/indeed-scraper",
    "linkedin": "curious_coder/linkedin-jobs-scraper",
    # No public Apify actors for Greenhouse / Lever.
    # Direct APIs: https://boards-api.greenhouse.io/v1/boards/{slug}/jobs
    #              https://api.lever.co/v0/postings/{slug}?mode=json
}

_CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_LINKEDIN_INDIA_GEO_ID = "102713980"


# ── Shared helpers ────────────────────────────────────────────────────────────

def _get_client() -> ApifyClient:
    """Return an authenticated Apify client."""
    return ApifyClient(config.APIFY_API_KEY)


def _normalize_job(raw: dict[str, Any], source: str) -> dict[str, Any]:
    """Map a raw Apify result to the standard job dict schema.

    Args:
        raw: Raw dict returned by the Apify actor.
        source: Human-readable source label (e.g. 'indeed').

    Returns:
        Normalized job dict.
    """
    return {
        "title": raw.get("title") or raw.get("jobTitle") or raw.get("positionName") or "",
        "company": raw.get("company") or raw.get("companyName") or raw.get("employerName") or "",
        "location": raw.get("location") or raw.get("jobLocation") or "",
        "url": (
            raw.get("url") or raw.get("link") or raw.get("jobUrl")
            or raw.get("applyUrl") or raw.get("externalApplyLink") or ""
        ),
        "description": (
            raw.get("description") or raw.get("jobDescription")
            or raw.get("descriptionText") or raw.get("descriptionHtml")
            or raw.get("snippet") or ""
        ),
        "source": source,
        "posted_date": raw.get("postedDate") or raw.get("postedAt") or raw.get("datePosted") or "",
    }


def _run_actor(actor_id: str, run_input: dict[str, Any], source: str) -> list[dict[str, Any]]:
    """Trigger an Apify actor synchronously and return normalized results.

    Args:
        actor_id: Apify actor ID string.
        run_input: Actor input payload.
        source: Label for the source field in returned job dicts.

    Returns:
        List of normalized job dicts.
    """
    client = _get_client()
    logger.info(f"[SCRAPER] Triggering actor {actor_id!r} (source={source})")
    try:
        run = client.actor(actor_id).call(run_input=run_input)
        items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
        jobs = [_normalize_job(item, source) for item in items]
        jobs = [j for j in jobs if j["title"] and j["url"]]
        logger.info(f"[SCRAPER] Got {len(jobs)} jobs from {source}")
        return jobs
    except Exception as exc:
        logger.error(f"[SCRAPER] Failed to run {actor_id}: {exc}")
        return []


def _dedup(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate a job list by lowercase-stripped URL.

    Args:
        jobs: List of job dicts (may contain duplicates).

    Returns:
        Deduplicated list preserving first-occurrence order.
    """
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for job in jobs:
        key = (job.get("url") or "").strip().lower()
        if key and key not in seen:
            seen.add(key)
            unique.append(job)
    return unique


def _pw_browser(pw: Any):  # type: ignore[return]
    """Launch a headless browser using system Chrome/Edge (no Playwright binary needed)."""
    for channel in ("chrome", "msedge"):
        try:
            return pw.chromium.launch(headless=True, channel=channel)
        except Exception:
            continue
    return pw.chromium.launch(headless=True)  # last resort


def _pw_context(browser: Any, viewport: dict | None = None):  # type: ignore[return]
    """Create a browser context with Chrome UA and optional viewport."""
    kwargs: dict[str, Any] = {"user_agent": _CHROME_UA}
    if viewport:
        kwargs["viewport"] = viewport
    return browser.new_context(**kwargs)


# ── Tier 1 — Apify scrapers ───────────────────────────────────────────────────

def scrape_indeed_apify(
    keyword: str,
    location: str = "India",
    mode: str = "india",
    date_filter: str = "",
) -> list[dict[str, Any]]:
    """Scrape Indeed via misceres/indeed-scraper Apify actor (fallback).

    Args:
        keyword: Job title or keyword to search.
        location: Target location string (ignored when mode='india').
        mode: 'india' forces location='India', country='IN'.
        date_filter: Optional date filter (e.g. 'today', '3days').

    Returns:
        List of normalized job dicts.
    """
    actor_location = "India" if mode == "india" else location
    country = "IN" if mode == "india" else "US"

    run_input: dict[str, Any] = {
        "position": keyword,
        "location": actor_location,
        "country": country,
        "maxItems": 50,
    }
    if date_filter:
        run_input["datePosted"] = date_filter

    logger.info(f"[TIER1] Indeed Apify — {keyword!r} @ {actor_location}")
    return _run_actor(ACTOR_IDS["indeed"], run_input, "indeed")


def scrape_indeed(
    keyword: str,
    location: str = "India",
    mode: str = "india",
    date_filter: str = "",
) -> list[dict[str, Any]]:
    """Scrape Indeed — Playwright-first, Apify fallback.

    Tries native Playwright scraper first (no credits required).
    Falls back to Apify only if APIFY_API_KEY is configured and Playwright returns nothing.

    Args:
        keyword: Job title or keyword to search.
        location: Target location string.
        mode: Passed to Apify fallback only.
        date_filter: Optional date filter ('today', '3days', '').

    Returns:
        List of normalized job dicts.
    """
    jobs = scrape_indeed_playwright(keyword, location, date_filter=date_filter)
    if jobs:
        return jobs
    if getattr(config, "APIFY_API_KEY", ""):
        logger.info("[TIER1] Indeed Playwright returned 0 — trying Apify fallback")
        return scrape_indeed_apify(keyword, location, mode, date_filter)
    return []


def scrape_linkedin(
    keyword: str,
    location: str = "India",
    mode: str = "india",
    date_filter: str = "",
) -> list[dict[str, Any]]:
    """Scrape LinkedIn Jobs via curious_coder/linkedin-jobs-scraper.

    Args:
        keyword: Job title or keyword to search.
        location: Target location string.
        mode: 'india' appends geoId=102713980.
        date_filter: 'today' appends f_TPR=r86400 (last 24 h).

    Returns:
        List of normalized job dicts.
    """
    params: dict[str, str] = {"keywords": keyword, "location": location}
    if mode == "india":
        params["geoId"] = _LINKEDIN_INDIA_GEO_ID
    if date_filter == "today":
        params["f_TPR"] = "r86400"

    search_url = "https://www.linkedin.com/jobs/search/?" + urllib.parse.urlencode(params)
    logger.info(f"[TIER1] LinkedIn — {keyword!r}")
    return _run_actor(
        ACTOR_IDS["linkedin"],
        {"urls": [search_url], "maxItems": 50},
        "linkedin",
    )


# ── Tier 1 — Playwright scrapers ──────────────────────────────────────────────

def scrape_indeed_playwright(
    keyword: str,
    location: str = "India",
    max_results: int = 60,
    date_filter: str = "",
) -> list[dict[str, Any]]:
    """Scrape Indeed.co.in via Playwright (no Apify credits needed).

    Args:
        keyword: Job title or keyword to search.
        location: Location filter string.
        max_results: Maximum number of jobs to return.
        date_filter: 'today' → fromage=1, '3days' → fromage=3, '' → no filter.

    Returns:
        List of normalized job dicts.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.error("[TIER1] Indeed Playwright requires playwright")
        return []

    params: dict[str, str] = {"q": keyword, "l": location}
    if date_filter == "today":
        params["fromage"] = "1"
    elif date_filter:
        params["fromage"] = "3"
    base_url = "https://www.indeed.co.in/jobs?" + urllib.parse.urlencode(params)

    jobs: list[dict[str, Any]] = []

    try:
        with sync_playwright() as pw:
            browser = _pw_browser(pw)
            context = _pw_context(browser)
            page = context.new_page()

            for page_num in range(6):  # max 6 pages × 10 results = 60
                start = page_num * 10
                url = base_url + (f"&start={start}" if start else "")
                logger.info(f"[TIER1] Indeed Playwright page {page_num + 1}: {url}")

                try:
                    page.goto(url, timeout=30_000, wait_until="domcontentloaded")
                except Exception as e:
                    logger.warning(f"[TIER1] Indeed page {page_num + 1} failed: {e}")
                    break

                time.sleep(random.uniform(2.0, 4.0))

                body_text = page.inner_text("body").lower()
                if "captcha" in body_text or "unusual traffic" in body_text:
                    logger.warning("[TIER1] Indeed: bot detection triggered, stopping")
                    break

                cards = page.query_selector_all("li.resultContent")
                if not cards:
                    cards = page.query_selector_all("div.job_seen_beacon")
                if not cards:
                    cards = page.query_selector_all(".mosaic-provider-jobcards li:has(h2)")

                if not cards:
                    logger.warning(f"[TIER1] Indeed page {page_num + 1}: no cards found, stopping")
                    break

                page_hits = 0
                for card in cards:
                    try:
                        title_el = card.query_selector(
                            "h2.jobTitle a, [data-testid='job-title'] a, h2 a"
                        )
                        company_el = card.query_selector(
                            "[data-testid='company-name'], .companyName"
                        )
                        location_el = card.query_selector(
                            "[data-testid='text-location'], .companyLocation"
                        )
                        date_el = card.query_selector(".date, [data-testid*='date']")
                        desc_el = card.query_selector(".job-snippet")

                        title = title_el.inner_text().strip() if title_el else ""
                        link = title_el.get_attribute("href") if title_el else ""
                        jk = title_el.get_attribute("data-jk") if title_el else ""
                        company = company_el.inner_text().strip() if company_el else ""
                        loc = location_el.inner_text().strip() if location_el else location
                        posted = date_el.inner_text().strip() if date_el else ""
                        desc = desc_el.inner_text().strip() if desc_el else ""

                        if not title:
                            continue

                        if jk:
                            full_url = f"https://www.indeed.co.in/viewjob?jk={jk}"
                        elif link:
                            full_url = link if link.startswith("http") else f"https://www.indeed.co.in{link}"
                        else:
                            continue

                        jobs.append({
                            "title": title,
                            "company": company,
                            "location": loc,
                            "url": full_url,
                            "description": desc,
                            "source": "indeed",
                            "posted_date": posted,
                        })
                        page_hits += 1

                    except Exception:
                        continue

                if page_hits == 0 or len(jobs) >= max_results:
                    break

                time.sleep(random.uniform(2.0, 4.0))

            browser.close()

    except Exception as exc:
        logger.error(f"[TIER1] Indeed Playwright failed: {exc}")
        return []

    jobs = _dedup(jobs)[:max_results]
    logger.info(f"[SCRAPER] Got {len(jobs)} jobs from indeed (playwright)")
    return jobs


def scrape_naukri(
    keyword: str,
    location: str = "India",
    max_results: int = 100,
) -> list[dict[str, Any]]:
    """Scrape Naukri.com via Playwright (up to 5 pages, capped at max_results).

    Args:
        keyword: Job title or keyword to search.
        location: Location filter for Naukri search.
        max_results: Maximum number of jobs to return.

    Returns:
        List of normalized job dicts.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.error("[TIER1] Naukri requires playwright — pip install playwright && playwright install chromium")
        return []

    jobs: list[dict[str, Any]] = []

    try:
        with sync_playwright() as pw:
            browser = _pw_browser(pw)
            context = _pw_context(browser)
            page = context.new_page()

            for page_num in range(1, 6):
                path = "jobs-in-india" if page_num == 1 else f"jobs-in-india-{page_num}"
                url = (
                    f"https://www.naukri.com/{path}?"
                    + urllib.parse.urlencode({"k": keyword, "l": location})
                )
                logger.info(f"[TIER1] Naukri page {page_num}: {url}")

                try:
                    page.goto(url, timeout=30_000)
                    page.wait_for_selector(
                        ".jobTuple, .srp-jobtuple-wrapper, [class*='job-tittle']",
                        timeout=15_000,
                    )
                except Exception as e:
                    logger.warning(f"[TIER1] Naukri page {page_num} load failed: {e}")
                    break

                cards = page.query_selector_all(".jobTuple, .srp-jobtuple-wrapper")
                if not cards:
                    break

                for card in cards:
                    try:
                        title_el = card.query_selector(
                            "a.title, [class*='job-tittle'] a, h2 a, a.jobTitle"
                        )
                        company_el = card.query_selector(
                            ".companyInfo a, .comp-name, [class*='comp-name'], a.subTitle"
                        )
                        location_el = card.query_selector(
                            ".loc span, [class*='location'] li, .locWdth"
                        )
                        date_el = card.query_selector(".date-format, [class*='freshness']")
                        desc_el = card.query_selector(".job-description, [class*='job-desc']")

                        title = title_el.inner_text().strip() if title_el else ""
                        link = title_el.get_attribute("href") if title_el else ""
                        company = company_el.inner_text().strip() if company_el else ""
                        loc = location_el.inner_text().strip() if location_el else location
                        posted = date_el.inner_text().strip() if date_el else ""
                        desc = desc_el.inner_text().strip() if desc_el else ""

                        if title and link:
                            jobs.append({
                                "title": title,
                                "company": company,
                                "location": loc,
                                "url": link if link.startswith("http") else f"https://www.naukri.com{link}",
                                "description": desc,
                                "source": "naukri",
                                "posted_date": posted,
                            })
                    except Exception:
                        continue

                if len(jobs) >= max_results:
                    break

                time.sleep(random.uniform(1.5, 3.0))

            browser.close()

    except Exception as exc:
        logger.error(f"[TIER1] Naukri failed: {exc}")
        return []

    jobs = _dedup(jobs)[:max_results]
    logger.info(f"[SCRAPER] Got {len(jobs)} jobs from naukri")
    return jobs


def scrape_google_jobs(
    keyword: str,
    location: str = "India",
    max_results: int = 60,
) -> list[dict[str, Any]]:
    """Scrape Google Jobs via Playwright.

    Google Jobs aggregates all job boards and company career pages, making it
    the highest-ROI single addition for India job coverage.

    Args:
        keyword: Job title or keyword to search.
        location: Location filter string.
        max_results: Maximum number of jobs to return.

    Returns:
        List of normalized job dicts.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.error("[TIER1] Google Jobs requires playwright")
        return []

    jobs: list[dict[str, Any]] = []
    query = f"{keyword} jobs {location}"
    url = "https://www.google.com/search?" + urllib.parse.urlencode({
        "q": query,
        "ibp": "htl;jobs",
    })

    try:
        with sync_playwright() as pw:
            browser = _pw_browser(pw)
            context = _pw_context(browser, viewport={"width": 1920, "height": 1080})
            page = context.new_page()

            logger.info(f"[TIER1] Google Jobs: {url}")
            page.goto(url, timeout=45_000, wait_until="domcontentloaded")

            try:
                page.wait_for_selector(
                    ".iFjolb, .gws-plugins-horizon-jobs__tl-lif, li.LL0hIb, [data-ved]",
                    timeout=20_000,
                )
            except Exception:
                logger.warning("[TIER1] Google Jobs: no job cards found (may be blocked)")
                browser.close()
                return []

            time.sleep(random.uniform(2.5, 4.0))

            # Try to click "More jobs" once for more results
            for _ in range(2):
                try:
                    more_btn = page.query_selector(
                        "button[jsname='GFE9nb'], [aria-label*='more jobs'], button:has-text('More jobs')"
                    )
                    if more_btn:
                        more_btn.click()
                        time.sleep(random.uniform(2.0, 3.5))
                except Exception:
                    break

            cards = page.query_selector_all(".iFjolb, li.LL0hIb")
            if not cards:
                # Fallback: try broader selector
                cards = page.query_selector_all("[data-ved][jscontroller]")

            for card in cards[:max_results]:
                try:
                    title_el = card.query_selector(
                        ".BjJfJf, [role='heading'], h2, h3, [class*='title']"
                    )
                    company_el = card.query_selector(".vNEEBe, [class*='company']")
                    location_el = card.query_selector(".Qk80Jf, .ShellP, [class*='location']")
                    date_el = card.query_selector(".LL4CDc, [class*='date'], [class*='time']")
                    link_el = card.query_selector("a[href]")

                    title = title_el.inner_text().strip() if title_el else ""
                    company = company_el.inner_text().strip() if company_el else ""
                    loc = location_el.inner_text().strip() if location_el else location
                    posted = date_el.inner_text().strip() if date_el else ""
                    link = link_el.get_attribute("href") if link_el else ""

                    if not link:
                        link = url  # fallback: Google Jobs search page

                    if title:
                        jobs.append({
                            "title": title,
                            "company": company,
                            "location": loc,
                            "url": link if link.startswith("http") else f"https://www.google.com{link}",
                            "description": "",
                            "source": "google_jobs",
                            "posted_date": posted,
                        })
                except Exception:
                    continue

            browser.close()

    except Exception as exc:
        logger.error(f"[TIER1] Google Jobs failed: {exc}")
        return []

    jobs = _dedup(jobs)
    logger.info(f"[SCRAPER] Got {len(jobs)} jobs from google_jobs")
    return jobs


# ── Tier 2 — Playwright scrapers ──────────────────────────────────────────────

def scrape_internshala(keyword: str) -> list[dict[str, Any]]:
    """Scrape Internshala via Apify actor with Playwright fallback.

    Args:
        keyword: Job title or keyword to search.

    Returns:
        List of normalized job dicts (max 50).
    """
    try:
        result = _run_actor(
            "prakhar_gupta/internshala-scraper",
            {"keyword": keyword, "maxItems": 50},
            "internshala",
        )
        if result:
            return result[:50]
    except Exception as exc:
        logger.warning(f"[TIER2] Internshala Apify failed, trying Playwright: {exc}")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.error("[TIER2] Internshala fallback requires playwright")
        return []

    jobs: list[dict[str, Any]] = []
    slug = keyword.strip().lower().replace(" ", "-")
    url = f"https://internshala.com/jobs/{slug}-jobs"

    try:
        with sync_playwright() as pw:
            browser = _pw_browser(pw)
            context = _pw_context(browser)
            page = context.new_page()

            logger.info(f"[TIER2] Internshala: {url}")
            page.goto(url, timeout=30_000)

            try:
                page.wait_for_selector(".internship_meta, .individual_internship", timeout=15_000)
            except Exception:
                logger.warning("[TIER2] Internshala: no job cards found")
                browser.close()
                return []

            for card in page.query_selector_all(".individual_internship")[:50]:
                try:
                    title_el = card.query_selector(".job-title-href, .profile a, h3 a")
                    company_el = card.query_selector(".company-name, .company_name")
                    location_el = card.query_selector(".location_link, .location span")
                    stipend_el = card.query_selector(".stipend, .salary")

                    title = title_el.inner_text().strip() if title_el else ""
                    link = title_el.get_attribute("href") if title_el else ""
                    company = company_el.inner_text().strip() if company_el else ""
                    loc = location_el.inner_text().strip() if location_el else ""
                    salary = stipend_el.inner_text().strip() if stipend_el else ""

                    if title and link:
                        jobs.append({
                            "title": title,
                            "company": company,
                            "location": loc,
                            "url": link if link.startswith("http") else f"https://internshala.com{link}",
                            "description": salary,
                            "source": "internshala",
                            "posted_date": "",
                        })
                except Exception:
                    continue

            browser.close()

    except Exception as exc:
        logger.error(f"[TIER2] Internshala failed: {exc}")
        return []

    jobs = _dedup(jobs)
    logger.info(f"[SCRAPER] Got {len(jobs)} jobs from internshala")
    return jobs


def scrape_instahyre(keyword: str, max_results: int = 50) -> list[dict[str, Any]]:
    """Scrape Instahyre.com via Playwright.

    Args:
        keyword: Job title or keyword to search.
        max_results: Maximum number of jobs to return.

    Returns:
        List of normalized job dicts.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.error("[TIER2] Instahyre requires playwright")
        return []

    jobs: list[dict[str, Any]] = []
    url = (
        "https://www.instahyre.com/search-jobs/?"
        + urllib.parse.urlencode({"q": keyword, "l": "India"})
    )

    try:
        with sync_playwright() as pw:
            browser = _pw_browser(pw)
            context = _pw_context(browser)
            page = context.new_page()

            logger.info(f"[TIER2] Instahyre: {url}")
            page.goto(url, timeout=30_000)

            try:
                page.wait_for_selector(
                    ".opportunity-card, [class*='job-card'], [class*='opportunity']",
                    timeout=15_000,
                )
            except Exception:
                logger.warning("[TIER2] Instahyre: no job cards found")
                browser.close()
                return []

            for _ in range(3):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(random.uniform(1.5, 2.5))

            for card in page.query_selector_all(".opportunity-card, [class*='job-card']")[:max_results]:
                try:
                    title_el = card.query_selector("h2, h3, [class*='title'], [class*='role']")
                    company_el = card.query_selector("[class*='company'], [class*='employer']")
                    location_el = card.query_selector("[class*='location'], [class*='city']")
                    link_el = card.query_selector("a")

                    title = title_el.inner_text().strip() if title_el else ""
                    company = company_el.inner_text().strip() if company_el else ""
                    loc = location_el.inner_text().strip() if location_el else "India"
                    link = link_el.get_attribute("href") if link_el else ""

                    if title and link:
                        jobs.append({
                            "title": title,
                            "company": company,
                            "location": loc,
                            "url": link if link.startswith("http") else f"https://www.instahyre.com{link}",
                            "description": "",
                            "source": "instahyre",
                            "posted_date": "",
                        })
                except Exception:
                    continue

            browser.close()

    except Exception as exc:
        logger.error(f"[TIER2] Instahyre failed: {exc}")
        return []

    jobs = _dedup(jobs)
    logger.info(f"[SCRAPER] Got {len(jobs)} jobs from instahyre")
    return jobs


def scrape_cutshort(keyword: str, max_results: int = 60) -> list[dict[str, Any]]:
    """Scrape Cutshort.io via Playwright.

    Args:
        keyword: Job title or keyword to search.
        max_results: Maximum number of jobs to return.

    Returns:
        List of normalized job dicts.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.error("[TIER2] Cutshort requires playwright")
        return []

    jobs: list[dict[str, Any]] = []
    url = "https://cutshort.io/jobs?" + urllib.parse.urlencode({"q": keyword, "location": "India"})

    try:
        with sync_playwright() as pw:
            browser = _pw_browser(pw)
            context = _pw_context(browser)
            page = context.new_page()

            logger.info(f"[TIER2] Cutshort: {url}")
            page.goto(url, timeout=30_000)

            try:
                page.wait_for_selector(
                    "[class*='job-card'], [class*='JobCard'], .job-list-item, [class*='listing']",
                    timeout=15_000,
                )
            except Exception:
                logger.warning("[TIER2] Cutshort: no job cards found")
                browser.close()
                return []

            # Scroll for more results
            for _ in range(3):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(random.uniform(1.5, 2.5))

            cards = page.query_selector_all(
                "[class*='job-card'], [class*='JobCard'], .job-list-item"
            )
            for card in cards[:max_results]:
                try:
                    title_el = card.query_selector(
                        "h2, h3, [class*='title'], [class*='role'], [class*='position']"
                    )
                    company_el = card.query_selector("[class*='company'], [class*='employer']")
                    location_el = card.query_selector("[class*='location'], [class*='city']")
                    link_el = card.query_selector("a[href]")

                    title = title_el.inner_text().strip() if title_el else ""
                    company = company_el.inner_text().strip() if company_el else ""
                    loc = location_el.inner_text().strip() if location_el else "India"
                    link = link_el.get_attribute("href") if link_el else ""

                    if title and link:
                        jobs.append({
                            "title": title,
                            "company": company,
                            "location": loc,
                            "url": link if link.startswith("http") else f"https://cutshort.io{link}",
                            "description": "",
                            "source": "cutshort",
                            "posted_date": "",
                        })
                except Exception:
                    continue

            browser.close()

    except Exception as exc:
        logger.error(f"[TIER2] Cutshort failed: {exc}")
        return []

    jobs = _dedup(jobs)
    logger.info(f"[SCRAPER] Got {len(jobs)} jobs from cutshort")
    return jobs


def scrape_hirist(keyword: str, max_results: int = 50) -> list[dict[str, Any]]:
    """Scrape Hirist.tech (tech-only jobs) via Playwright.

    Args:
        keyword: Job title or keyword to search.
        max_results: Maximum number of jobs to return.

    Returns:
        List of normalized job dicts.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.error("[TIER2] Hirist requires playwright")
        return []

    jobs: list[dict[str, Any]] = []
    url = "https://www.hirist.tech/search?" + urllib.parse.urlencode({"searchKey": keyword})

    try:
        with sync_playwright() as pw:
            browser = _pw_browser(pw)
            context = _pw_context(browser)
            page = context.new_page()

            logger.info(f"[TIER2] Hirist: {url}")
            page.goto(url, timeout=30_000)

            try:
                page.wait_for_selector(
                    ".job-card, .jobCard, [class*='job-listing'], [class*='jobList']",
                    timeout=15_000,
                )
            except Exception:
                logger.warning("[TIER2] Hirist: no job cards found")
                browser.close()
                return []

            cards = page.query_selector_all(".job-card, .jobCard, [class*='job-listing']")
            for card in cards[:max_results]:
                try:
                    title_el = card.query_selector("h2, h3, [class*='title'], [class*='role'] a")
                    company_el = card.query_selector("[class*='company'], [class*='employer']")
                    location_el = card.query_selector("[class*='location'], [class*='city']")
                    link_el = card.query_selector("a[href]")

                    title = title_el.inner_text().strip() if title_el else ""
                    company = company_el.inner_text().strip() if company_el else ""
                    loc = location_el.inner_text().strip() if location_el else "India"
                    link = link_el.get_attribute("href") if link_el else ""

                    if title and link:
                        jobs.append({
                            "title": title,
                            "company": company,
                            "location": loc,
                            "url": link if link.startswith("http") else f"https://www.hirist.tech{link}",
                            "description": "",
                            "source": "hirist",
                            "posted_date": "",
                        })
                except Exception:
                    continue

            browser.close()

    except Exception as exc:
        logger.error(f"[TIER2] Hirist failed: {exc}")
        return []

    jobs = _dedup(jobs)
    logger.info(f"[SCRAPER] Got {len(jobs)} jobs from hirist")
    return jobs


def scrape_iimjobs(keyword: str, max_results: int = 50) -> list[dict[str, Any]]:
    """Scrape IIMJobs.com via Playwright.

    Args:
        keyword: Job title or keyword to search.
        max_results: Maximum number of jobs to return.

    Returns:
        List of normalized job dicts.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.error("[TIER2] IIMJobs requires playwright")
        return []

    jobs: list[dict[str, Any]] = []
    slug = keyword.strip().lower().replace(" ", "-")
    url = f"https://www.iimjobs.com/search/keyword-{slug}"

    try:
        with sync_playwright() as pw:
            browser = _pw_browser(pw)
            context = _pw_context(browser)
            page = context.new_page()

            logger.info(f"[TIER2] IIMJobs: {url}")
            page.goto(url, timeout=30_000)

            try:
                page.wait_for_selector(
                    ".job-profile, [class*='job-card'], .jobDetails, li.clearfix",
                    timeout=15_000,
                )
            except Exception:
                logger.warning("[TIER2] IIMJobs: no job cards found")
                browser.close()
                return []

            cards = page.query_selector_all(".job-profile, .jobDetails, li.clearfix")
            for card in cards[:max_results]:
                try:
                    title_el = card.query_selector(
                        "h2 a, h3 a, [class*='title'] a, .job-name a"
                    )
                    company_el = card.query_selector(
                        "[class*='company'], [class*='employer'], .comp-name"
                    )
                    location_el = card.query_selector("[class*='location'], .loc")
                    date_el = card.query_selector("[class*='date'], [class*='time'], .fresh-date")

                    title = title_el.inner_text().strip() if title_el else ""
                    link = title_el.get_attribute("href") if title_el else ""
                    company = company_el.inner_text().strip() if company_el else ""
                    loc = location_el.inner_text().strip() if location_el else "India"
                    posted = date_el.inner_text().strip() if date_el else ""

                    if title and link:
                        jobs.append({
                            "title": title,
                            "company": company,
                            "location": loc,
                            "url": link if link.startswith("http") else f"https://www.iimjobs.com{link}",
                            "description": "",
                            "source": "iimjobs",
                            "posted_date": posted,
                        })
                except Exception:
                    continue

            browser.close()

    except Exception as exc:
        logger.error(f"[TIER2] IIMJobs failed: {exc}")
        return []

    jobs = _dedup(jobs)
    logger.info(f"[SCRAPER] Got {len(jobs)} jobs from iimjobs")
    return jobs


def scrape_foundit(keyword: str, max_results: int = 60) -> list[dict[str, Any]]:
    """Scrape Foundit.in (formerly Monster India) via Playwright.

    Args:
        keyword: Job title or keyword to search.
        max_results: Maximum number of jobs to return.

    Returns:
        List of normalized job dicts.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.error("[TIER2] Foundit requires playwright")
        return []

    jobs: list[dict[str, Any]] = []
    url = "https://www.foundit.in/search?" + urllib.parse.urlencode({
        "query": keyword,
        "location": "India",
    })

    try:
        with sync_playwright() as pw:
            browser = _pw_browser(pw)
            context = _pw_context(browser)
            page = context.new_page()

            logger.info(f"[TIER2] Foundit: {url}")
            page.goto(url, timeout=30_000)

            try:
                page.wait_for_selector(
                    "[class*='card'], [class*='job-card'], [class*='listing'], .job-container",
                    timeout=15_000,
                )
            except Exception:
                logger.warning("[TIER2] Foundit: no job cards found")
                browser.close()
                return []

            for _ in range(2):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(random.uniform(1.5, 2.5))

            cards = page.query_selector_all(
                "[class*='card-apply'], [class*='job-card'], .job-container"
            )
            for card in cards[:max_results]:
                try:
                    title_el = card.query_selector(
                        "h2, h3, [class*='title'], [class*='role'] a, [class*='job-title']"
                    )
                    company_el = card.query_selector(
                        "[class*='company'], [class*='employer'], [class*='comp-name']"
                    )
                    location_el = card.query_selector("[class*='location'], [class*='city']")
                    link_el = card.query_selector("a[href]")

                    title = title_el.inner_text().strip() if title_el else ""
                    company = company_el.inner_text().strip() if company_el else ""
                    loc = location_el.inner_text().strip() if location_el else "India"
                    link = link_el.get_attribute("href") if link_el else ""

                    if title and link:
                        jobs.append({
                            "title": title,
                            "company": company,
                            "location": loc,
                            "url": link if link.startswith("http") else f"https://www.foundit.in{link}",
                            "description": "",
                            "source": "foundit",
                            "posted_date": "",
                        })
                except Exception:
                    continue

            browser.close()

    except Exception as exc:
        logger.error(f"[TIER2] Foundit failed: {exc}")
        return []

    jobs = _dedup(jobs)
    logger.info(f"[SCRAPER] Got {len(jobs)} jobs from foundit")
    return jobs


def scrape_shine(keyword: str, max_results: int = 50) -> list[dict[str, Any]]:
    """Scrape Shine.com via Playwright.

    Args:
        keyword: Job title or keyword to search.
        max_results: Maximum number of jobs to return.

    Returns:
        List of normalized job dicts.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.error("[TIER2] Shine requires playwright")
        return []

    jobs: list[dict[str, Any]] = []
    slug = keyword.strip().lower().replace(" ", "-")
    url = f"https://www.shine.com/job-search/{slug}-jobs"

    try:
        with sync_playwright() as pw:
            browser = _pw_browser(pw)
            context = _pw_context(browser)
            page = context.new_page()

            logger.info(f"[TIER2] Shine: {url}")
            page.goto(url, timeout=30_000)

            try:
                page.wait_for_selector(
                    ".jobCard, [class*='job-card'], [class*='job_listing'], article",
                    timeout=15_000,
                )
            except Exception:
                logger.warning("[TIER2] Shine: no job cards found")
                browser.close()
                return []

            cards = page.query_selector_all(".jobCard, [class*='job-card'], [class*='job_listing']")
            if not cards:
                cards = page.query_selector_all("article")

            for card in cards[:max_results]:
                try:
                    title_el = card.query_selector(
                        "h2 a, h3 a, [class*='title'] a, [class*='job-title'] a"
                    )
                    company_el = card.query_selector("[class*='company'], [class*='employer']")
                    location_el = card.query_selector("[class*='location'], [class*='city']")
                    date_el = card.query_selector("[class*='date'], [class*='posted'], [class*='time']")

                    title = title_el.inner_text().strip() if title_el else ""
                    link = title_el.get_attribute("href") if title_el else ""
                    company = company_el.inner_text().strip() if company_el else ""
                    loc = location_el.inner_text().strip() if location_el else "India"
                    posted = date_el.inner_text().strip() if date_el else ""

                    if title and link:
                        jobs.append({
                            "title": title,
                            "company": company,
                            "location": loc,
                            "url": link if link.startswith("http") else f"https://www.shine.com{link}",
                            "description": "",
                            "source": "shine",
                            "posted_date": posted,
                        })
                except Exception:
                    continue

            browser.close()

    except Exception as exc:
        logger.error(f"[TIER2] Shine failed: {exc}")
        return []

    jobs = _dedup(jobs)
    logger.info(f"[SCRAPER] Got {len(jobs)} jobs from shine")
    return jobs


def scrape_timesjobs(keyword: str, max_results: int = 50) -> list[dict[str, Any]]:
    """Scrape TimesJobs.com via Playwright.

    Args:
        keyword: Job title or keyword to search.
        max_results: Maximum number of jobs to return.

    Returns:
        List of normalized job dicts.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.error("[TIER2] TimesJobs requires playwright")
        return []

    jobs: list[dict[str, Any]] = []
    url = (
        "https://www.timesjobs.com/candidate/job-search.html?"
        + urllib.parse.urlencode({
            "searchType": "personalizedSearch",
            "from": "submit",
            "txtKeywords": keyword,
            "txtLocation": "India",
        })
    )

    try:
        with sync_playwright() as pw:
            browser = _pw_browser(pw)
            context = _pw_context(browser)
            page = context.new_page()

            logger.info(f"[TIER2] TimesJobs: {url}")
            page.goto(url, timeout=30_000)

            try:
                page.wait_for_selector(
                    ".job-bx, li.clearfix, [class*='job-listing'], .jobs-list",
                    timeout=15_000,
                )
            except Exception:
                logger.warning("[TIER2] TimesJobs: no job cards found")
                browser.close()
                return []

            cards = page.query_selector_all(".job-bx, li.clearfix.job-post")
            for card in cards[:max_results]:
                try:
                    title_el = card.query_selector("h2 a, h3 a, [class*='job-title'] a")
                    company_el = card.query_selector(
                        "[class*='company'] a, [class*='employer'] a, h3.joblist-comp-name"
                    )
                    location_el = card.query_selector("[class*='location'] span, .srp-skills li")
                    date_el = card.query_selector(".sim-posted span, [class*='fresh-date']")

                    title = title_el.inner_text().strip() if title_el else ""
                    link = title_el.get_attribute("href") if title_el else ""
                    company = company_el.inner_text().strip() if company_el else ""
                    loc = location_el.inner_text().strip() if location_el else "India"
                    posted = date_el.inner_text().strip() if date_el else ""

                    if title and link:
                        jobs.append({
                            "title": title,
                            "company": company,
                            "location": loc,
                            "url": link if link.startswith("http") else f"https://www.timesjobs.com{link}",
                            "description": "",
                            "source": "timesjobs",
                            "posted_date": posted,
                        })
                except Exception:
                    continue

            browser.close()

    except Exception as exc:
        logger.error(f"[TIER2] TimesJobs failed: {exc}")
        return []

    jobs = _dedup(jobs)
    logger.info(f"[SCRAPER] Got {len(jobs)} jobs from timesjobs")
    return jobs


def scrape_unstop(keyword: str, max_results: int = 50) -> list[dict[str, Any]]:
    """Scrape Unstop.com jobs via Playwright.

    Args:
        keyword: Job title or keyword to search.
        max_results: Maximum number of jobs to return.

    Returns:
        List of normalized job dicts.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.error("[TIER2] Unstop requires playwright")
        return []

    jobs: list[dict[str, Any]] = []
    url = "https://unstop.com/jobs?" + urllib.parse.urlencode({
        "search": keyword,
        "opportunity": "jobs",
    })

    try:
        with sync_playwright() as pw:
            browser = _pw_browser(pw)
            context = _pw_context(browser)
            page = context.new_page()

            logger.info(f"[TIER2] Unstop: {url}")
            page.goto(url, timeout=30_000, wait_until="networkidle")
            time.sleep(random.uniform(2.0, 3.5))

            for _ in range(3):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(random.uniform(1.5, 2.5))

            cards = page.query_selector_all(
                "[class*='opportunity-card'], [class*='card-']:has(h2), "
                "[class*='listing-item'], [class*='job-card'], [class*='opp-card']"
            )
            for card in cards[:max_results]:
                try:
                    title_el = card.query_selector(
                        "h2, h3, [class*='title'], [class*='name'], [class*='heading']"
                    )
                    company_el = card.query_selector(
                        "[class*='company'], [class*='org'], [class*='employer']"
                    )
                    link_el = card.query_selector("a[href]")

                    title = title_el.inner_text().strip() if title_el else ""
                    company = company_el.inner_text().strip() if company_el else ""
                    link = link_el.get_attribute("href") if link_el else ""

                    if title and link:
                        full_url = link if link.startswith("http") else f"https://unstop.com{link}"
                        jobs.append({
                            "title": title,
                            "company": company,
                            "location": "India",
                            "url": full_url,
                            "description": "",
                            "source": "unstop",
                            "posted_date": "",
                        })
                except Exception:
                    continue

            browser.close()

    except Exception as exc:
        logger.error(f"[TIER2] Unstop failed: {exc}")
        return []

    jobs = _dedup(jobs)
    logger.info(f"[SCRAPER] Got {len(jobs)} jobs from unstop")
    return jobs


def scrape_freshersworld(keyword: str, max_results: int = 50) -> list[dict[str, Any]]:
    """Scrape Freshersworld.com via Playwright.

    Args:
        keyword: Job title or keyword to search.
        max_results: Maximum number of jobs to return.

    Returns:
        List of normalized job dicts.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.error("[TIER2] Freshersworld requires playwright")
        return []

    jobs: list[dict[str, Any]] = []
    slug = keyword.strip().lower().replace(" ", "-")
    url = f"https://www.freshersworld.com/jobs/jobsearch/{slug}-jobs-in-india"

    try:
        with sync_playwright() as pw:
            browser = _pw_browser(pw)
            context = _pw_context(browser)
            page = context.new_page()

            logger.info(f"[TIER2] Freshersworld: {url}")
            page.goto(url, timeout=30_000)

            try:
                page.wait_for_selector(
                    ".job-container, .job-item, article, [class*='job-list'], li:has(h2)",
                    timeout=15_000,
                )
            except Exception:
                logger.warning("[TIER2] Freshersworld: no job cards found")
                browser.close()
                return []

            cards = page.query_selector_all(".job-container, .job-item, [class*='job-listing']")
            if not cards:
                cards = page.query_selector_all("article, li:has(h2), li:has(h3)")

            for card in cards[:max_results]:
                try:
                    title_el = card.query_selector(
                        "h2 a, h3 a, [class*='title'] a, [class*='role'] a, a.job-title"
                    )
                    company_el = card.query_selector(
                        "[class*='company'], [class*='employer'], [class*='org']"
                    )
                    location_el = card.query_selector("[class*='location'], [class*='city']")
                    date_el = card.query_selector("[class*='date'], [class*='posted']")

                    title = title_el.inner_text().strip() if title_el else ""
                    link = title_el.get_attribute("href") if title_el else ""
                    company = company_el.inner_text().strip() if company_el else ""
                    loc = location_el.inner_text().strip() if location_el else "India"
                    posted = date_el.inner_text().strip() if date_el else ""

                    if title and link:
                        full_url = link if link.startswith("http") else f"https://www.freshersworld.com{link}"
                        jobs.append({
                            "title": title,
                            "company": company,
                            "location": loc,
                            "url": full_url,
                            "description": "",
                            "source": "freshersworld",
                            "posted_date": posted,
                        })
                except Exception:
                    continue

            browser.close()

    except Exception as exc:
        logger.error(f"[TIER2] Freshersworld failed: {exc}")
        return []

    jobs = _dedup(jobs)
    logger.info(f"[SCRAPER] Got {len(jobs)} jobs from freshersworld")
    return jobs


def scrape_hackerearth(keyword: str, max_results: int = 50) -> list[dict[str, Any]]:
    """Scrape HackerEarth Jobs via Playwright.

    Args:
        keyword: Job title or keyword to search.
        max_results: Maximum number of jobs to return.

    Returns:
        List of normalized job dicts.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.error("[TIER2] HackerEarth requires playwright")
        return []

    jobs: list[dict[str, Any]] = []
    url = "https://www.hackerearth.com/jobs/?" + urllib.parse.urlencode({
        "q": keyword,
        "sort": "date",
    })

    try:
        with sync_playwright() as pw:
            browser = _pw_browser(pw)
            context = _pw_context(browser)
            page = context.new_page()

            logger.info(f"[TIER2] HackerEarth: {url}")
            page.goto(url, timeout=30_000, wait_until="networkidle")
            time.sleep(random.uniform(2.0, 3.0))

            cards = page.query_selector_all(
                ".job-card, [class*='job-listing'], .jobs-list-item, "
                "[class*='JobCard'], [class*='card']:has(h2)"
            )
            for card in cards[:max_results]:
                try:
                    title_el = card.query_selector(
                        "h2, h3, [class*='title'], [class*='role'], [class*='position']"
                    )
                    company_el = card.query_selector("[class*='company'], [class*='employer']")
                    location_el = card.query_selector("[class*='location'], [class*='city']")
                    link_el = card.query_selector("a[href]")

                    title = title_el.inner_text().strip() if title_el else ""
                    company = company_el.inner_text().strip() if company_el else ""
                    loc = location_el.inner_text().strip() if location_el else "India"
                    link = link_el.get_attribute("href") if link_el else ""

                    if title and link:
                        full_url = link if link.startswith("http") else f"https://www.hackerearth.com{link}"
                        jobs.append({
                            "title": title,
                            "company": company,
                            "location": loc,
                            "url": full_url,
                            "description": "",
                            "source": "hackerearth",
                            "posted_date": "",
                        })
                except Exception:
                    continue

            browser.close()

    except Exception as exc:
        logger.error(f"[TIER2] HackerEarth failed: {exc}")
        return []

    jobs = _dedup(jobs)
    logger.info(f"[SCRAPER] Got {len(jobs)} jobs from hackerearth")
    return jobs


def scrape_analyticsvidhya(keyword: str, max_results: int = 40) -> list[dict[str, Any]]:
    """Scrape Analytics Vidhya Jobs portal via Playwright.

    Args:
        keyword: Job title or keyword to search.
        max_results: Maximum number of jobs to return.

    Returns:
        List of normalized job dicts.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.error("[TIER2] Analytics Vidhya requires playwright")
        return []

    jobs: list[dict[str, Any]] = []
    url = "https://jobs.analyticsvidhya.com/jobs/?" + urllib.parse.urlencode({
        "search": keyword,
    })

    try:
        with sync_playwright() as pw:
            browser = _pw_browser(pw)
            context = _pw_context(browser)
            page = context.new_page()

            logger.info(f"[TIER2] Analytics Vidhya: {url}")
            page.goto(url, timeout=30_000, wait_until="networkidle")
            time.sleep(random.uniform(1.5, 2.5))

            cards = page.query_selector_all(
                ".job-card, [class*='job-listing'], [class*='job_listing'], "
                ".jobs-list-item, [class*='JobCard'], article"
            )
            for card in cards[:max_results]:
                try:
                    title_el = card.query_selector(
                        "h2 a, h3 a, [class*='title'] a, a.job-title, [class*='job-title'] a"
                    )
                    company_el = card.query_selector("[class*='company'], [class*='employer']")
                    location_el = card.query_selector("[class*='location'], [class*='city']")

                    title = title_el.inner_text().strip() if title_el else ""
                    link = title_el.get_attribute("href") if title_el else ""
                    company = company_el.inner_text().strip() if company_el else ""
                    loc = location_el.inner_text().strip() if location_el else "India"

                    if title and link:
                        full_url = link if link.startswith("http") else f"https://jobs.analyticsvidhya.com{link}"
                        jobs.append({
                            "title": title,
                            "company": company,
                            "location": loc,
                            "url": full_url,
                            "description": "",
                            "source": "analyticsvidhya",
                            "posted_date": "",
                        })
                except Exception:
                    continue

            browser.close()

    except Exception as exc:
        logger.error(f"[TIER2] Analytics Vidhya failed: {exc}")
        return []

    jobs = _dedup(jobs)
    logger.info(f"[SCRAPER] Got {len(jobs)} jobs from analyticsvidhya")
    return jobs


def scrape_wellfound(keyword: str, max_results: int = 50) -> list[dict[str, Any]]:
    """Scrape Wellfound (AngelList Talent) jobs via Playwright.

    Args:
        keyword: Job title or keyword to search.
        max_results: Maximum number of jobs to return.

    Returns:
        List of normalized job dicts.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.error("[TIER2] Wellfound requires playwright")
        return []

    jobs: list[dict[str, Any]] = []
    url = "https://wellfound.com/jobs?" + urllib.parse.urlencode({
        "query": keyword,
        "locationNames": "India",
    })

    try:
        with sync_playwright() as pw:
            browser = _pw_browser(pw)
            context = _pw_context(browser)
            page = context.new_page()

            logger.info(f"[TIER2] Wellfound: {url}")
            page.goto(url, timeout=30_000, wait_until="networkidle")
            time.sleep(random.uniform(2.5, 4.0))

            for _ in range(3):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(random.uniform(1.5, 2.0))

            cards = page.query_selector_all(
                "[class*='JobListing'], [class*='job-listing'], [class*='StartupRow'], "
                "[data-test*='job'], [class*='styles_component']:has(h2)"
            )
            for card in cards[:max_results]:
                try:
                    title_el = card.query_selector(
                        "h2, h3, [class*='title'], [class*='role'], "
                        "[class*='JobTitle'], [class*='job-title']"
                    )
                    company_el = card.query_selector(
                        "[class*='StartupName'], [class*='company'], [class*='employer']"
                    )
                    location_el = card.query_selector("[class*='location'], [class*='city']")
                    link_el = card.query_selector("a[href*='/jobs/'], a[href*='/role/']")
                    if not link_el:
                        link_el = card.query_selector("a[href]")

                    title = title_el.inner_text().strip() if title_el else ""
                    company = company_el.inner_text().strip() if company_el else ""
                    loc = location_el.inner_text().strip() if location_el else "India"
                    link = link_el.get_attribute("href") if link_el else ""

                    if title and link:
                        full_url = link if link.startswith("http") else f"https://wellfound.com{link}"
                        jobs.append({
                            "title": title,
                            "company": company,
                            "location": loc,
                            "url": full_url,
                            "description": "",
                            "source": "wellfound",
                            "posted_date": "",
                        })
                except Exception:
                    continue

            browser.close()

    except Exception as exc:
        logger.error(f"[TIER2] Wellfound failed: {exc}")
        return []

    jobs = _dedup(jobs)
    logger.info(f"[SCRAPER] Got {len(jobs)} jobs from wellfound")
    return jobs


def scrape_apna(keyword: str, max_results: int = 50) -> list[dict[str, Any]]:
    """Scrape Apna.co jobs via Playwright.

    Args:
        keyword: Job title or keyword to search.
        max_results: Maximum number of jobs to return.

    Returns:
        List of normalized job dicts.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.error("[TIER2] Apna requires playwright")
        return []

    jobs: list[dict[str, Any]] = []
    url = "https://apna.co/find-jobs?" + urllib.parse.urlencode({
        "designation": keyword,
    })

    try:
        with sync_playwright() as pw:
            browser = _pw_browser(pw)
            context = _pw_context(browser)
            page = context.new_page()

            logger.info(f"[TIER2] Apna: {url}")
            page.goto(url, timeout=30_000, wait_until="networkidle")
            time.sleep(random.uniform(2.0, 3.5))

            for _ in range(3):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(random.uniform(1.5, 2.0))

            cards = page.query_selector_all(
                "[class*='job-card'], [class*='JobCard'], [class*='job-item'], "
                "[class*='listing-card'], [class*='card']:has(h2)"
            )
            for card in cards[:max_results]:
                try:
                    title_el = card.query_selector(
                        "h2, h3, [class*='title'], [class*='role'], [class*='designation']"
                    )
                    company_el = card.query_selector(
                        "[class*='company'], [class*='employer'], [class*='org']"
                    )
                    location_el = card.query_selector("[class*='location'], [class*='city']")
                    link_el = card.query_selector("a[href]")

                    title = title_el.inner_text().strip() if title_el else ""
                    company = company_el.inner_text().strip() if company_el else ""
                    loc = location_el.inner_text().strip() if location_el else "India"
                    link = link_el.get_attribute("href") if link_el else ""

                    if title and link:
                        full_url = link if link.startswith("http") else f"https://apna.co{link}"
                        jobs.append({
                            "title": title,
                            "company": company,
                            "location": loc,
                            "url": full_url,
                            "description": "",
                            "source": "apna",
                            "posted_date": "",
                        })
                except Exception:
                    continue

            browser.close()

    except Exception as exc:
        logger.error(f"[TIER2] Apna failed: {exc}")
        return []

    jobs = _dedup(jobs)
    logger.info(f"[SCRAPER] Got {len(jobs)} jobs from apna")
    return jobs



def scrape_snaphunt(keyword: str, max_results: int = 50) -> list[dict[str, Any]]:
    """Scrape Snaphunt.com startup jobs via Playwright.

    Snaphunt is a Series-B startup job board (backed by Tencent) with 100% verified
    recruiters — no spam. Covers India remote/hybrid startup roles.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.error("[TIER2] Snaphunt requires playwright")
        return []

    jobs: list[dict[str, Any]] = []
    url = "https://snaphunt.com/jobs?" + urllib.parse.urlencode({
        "jobTitle": keyword,
        "country": "India",
    })

    try:
        with sync_playwright() as pw:
            browser = _pw_browser(pw)
            context = _pw_context(browser)
            page = context.new_page()

            logger.info(f"[TIER2] Snaphunt: {url}")
            page.goto(url, timeout=30_000, wait_until="domcontentloaded")
            time.sleep(random.uniform(2.0, 3.0))

            for _ in range(3):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(random.uniform(1.5, 2.0))

            cards = page.query_selector_all(
                "[class*='job-card'], [class*='JobCard'], [class*='job-item'], "
                "[class*='listing'], article[class*='job']"
            )
            for card in cards[:max_results]:
                try:
                    title_el = card.query_selector("h2, h3, [class*='title'], [class*='role']")
                    company_el = card.query_selector("[class*='company'], [class*='employer'], [class*='org']")
                    location_el = card.query_selector("[class*='location'], [class*='city'], [class*='place']")
                    link_el = card.query_selector("a[href]")

                    title = title_el.inner_text().strip() if title_el else ""
                    company = company_el.inner_text().strip() if company_el else ""
                    loc = location_el.inner_text().strip() if location_el else "India"
                    link = link_el.get_attribute("href") if link_el else ""

                    if title and link:
                        full_url = link if link.startswith("http") else f"https://snaphunt.com{link}"
                        jobs.append({
                            "title": title,
                            "company": company,
                            "location": loc,
                            "url": full_url,
                            "description": "",
                            "source": "snaphunt",
                            "posted_date": "",
                        })
                except Exception:
                    continue

            browser.close()

    except Exception as exc:
        logger.error(f"[TIER2] Snaphunt failed: {exc}")
        return []

    jobs = _dedup(jobs)
    logger.info(f"[SCRAPER] Got {len(jobs)} jobs from snaphunt")
    return jobs


def scrape_letsintern(keyword: str, max_results: int = 50) -> list[dict[str, Any]]:
    """Scrape LetsIntern.in via Playwright.

    LetsIntern is an internship-specific platform with structured programs —
    ideal for fresher / final-year roles.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.error("[TIER2] LetsIntern requires playwright")
        return []

    jobs: list[dict[str, Any]] = []
    slug = urllib.parse.quote_plus(keyword.replace(" ", "-"))
    url = f"https://letsintern.in/internship/{slug}-internship"

    try:
        with sync_playwright() as pw:
            browser = _pw_browser(pw)
            context = _pw_context(browser)
            page = context.new_page()

            logger.info(f"[TIER2] LetsIntern: {url}")
            page.goto(url, timeout=30_000, wait_until="domcontentloaded")
            time.sleep(random.uniform(2.0, 3.0))

            for _ in range(3):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(random.uniform(1.5, 2.0))

            cards = page.query_selector_all(
                "[class*='internship-card'], [class*='job-card'], "
                "[class*='card']:has(h3), [class*='listing-item']"
            )
            for card in cards[:max_results]:
                try:
                    title_el = card.query_selector("h2, h3, [class*='title'], [class*='role']")
                    company_el = card.query_selector("[class*='company'], [class*='org'], [class*='employer']")
                    location_el = card.query_selector("[class*='location'], [class*='city'], [class*='place']")
                    link_el = card.query_selector("a[href]")

                    title = title_el.inner_text().strip() if title_el else ""
                    company = company_el.inner_text().strip() if company_el else ""
                    loc = location_el.inner_text().strip() if location_el else "India"
                    link = link_el.get_attribute("href") if link_el else ""

                    if title and link:
                        full_url = link if link.startswith("http") else f"https://letsintern.in{link}"
                        jobs.append({
                            "title": title,
                            "company": company,
                            "location": loc,
                            "url": full_url,
                            "description": "",
                            "source": "letsintern",
                            "posted_date": "",
                        })
                except Exception:
                    continue

            browser.close()

    except Exception as exc:
        logger.error(f"[TIER2] LetsIntern failed: {exc}")
        return []

    jobs = _dedup(jobs)
    logger.info(f"[SCRAPER] Got {len(jobs)} jobs from letsintern")
    return jobs


def scrape_hirect(keyword: str, max_results: int = 50) -> list[dict[str, Any]]:
    """Scrape Hirect.in startup jobs via Playwright.

    Hirect is a Series-B Bengaluru-based startup job board (190K+ verified startups)
    with direct chat hiring — no middlemen.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.error("[TIER2] Hirect requires playwright")
        return []

    jobs: list[dict[str, Any]] = []
    url = "https://hirect.in/job-search?" + urllib.parse.urlencode({"q": keyword})

    try:
        with sync_playwright() as pw:
            browser = _pw_browser(pw)
            context = _pw_context(browser)
            page = context.new_page()

            logger.info(f"[TIER2] Hirect: {url}")
            page.goto(url, timeout=30_000, wait_until="domcontentloaded")
            time.sleep(random.uniform(2.5, 4.0))

            for _ in range(3):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(random.uniform(1.5, 2.0))

            cards = page.query_selector_all(
                "[class*='job-card'], [class*='JobCard'], [class*='job-item'], "
                "[class*='card']:has(h2), [class*='card']:has(h3)"
            )
            for card in cards[:max_results]:
                try:
                    title_el = card.query_selector("h2, h3, [class*='title'], [class*='role'], [class*='position']")
                    company_el = card.query_selector("[class*='company'], [class*='startup'], [class*='employer']")
                    location_el = card.query_selector("[class*='location'], [class*='city']")
                    link_el = card.query_selector("a[href]")

                    title = title_el.inner_text().strip() if title_el else ""
                    company = company_el.inner_text().strip() if company_el else ""
                    loc = location_el.inner_text().strip() if location_el else "India"
                    link = link_el.get_attribute("href") if link_el else ""

                    if title and link:
                        full_url = link if link.startswith("http") else f"https://hirect.in{link}"
                        jobs.append({
                            "title": title,
                            "company": company,
                            "location": loc,
                            "url": full_url,
                            "description": "",
                            "source": "hirect",
                            "posted_date": "",
                        })
                except Exception:
                    continue

            browser.close()

    except Exception as exc:
        logger.error(f"[TIER2] Hirect failed: {exc}")
        return []

    jobs = _dedup(jobs)
    logger.info(f"[SCRAPER] Got {len(jobs)} jobs from hirect")
    return jobs


def scrape_techgig(keyword: str, max_results: int = 50) -> list[dict[str, Any]]:
    """Scrape TechGig.com tech jobs via Playwright.

    TechGig is owned by Times Group — one of India's most established tech job
    boards covering data, software, and analytics roles.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.error("[TIER2] TechGig requires playwright")
        return []

    jobs: list[dict[str, Any]] = []
    url = "https://www.techgig.com/jobs?" + urllib.parse.urlencode({
        "skill": keyword,
        "location": "India",
    })

    try:
        with sync_playwright() as pw:
            browser = _pw_browser(pw)
            context = _pw_context(browser)
            page = context.new_page()

            logger.info(f"[TIER2] TechGig: {url}")
            page.goto(url, timeout=30_000, wait_until="domcontentloaded")
            time.sleep(random.uniform(2.0, 3.0))

            for _ in range(4):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(random.uniform(1.5, 2.0))

            cards = page.query_selector_all(
                ".job_list_box, [class*='job-list'], [class*='job_card'], "
                "[class*='job-card'], article[class*='job']"
            )
            for card in cards[:max_results]:
                try:
                    title_el = card.query_selector(
                        "h2, h3, .job_title, [class*='title'], [class*='role']"
                    )
                    company_el = card.query_selector(
                        ".company_name, [class*='company'], [class*='employer']"
                    )
                    location_el = card.query_selector(
                        ".location, [class*='location'], [class*='city']"
                    )
                    link_el = card.query_selector("a[href]")

                    title = title_el.inner_text().strip() if title_el else ""
                    company = company_el.inner_text().strip() if company_el else ""
                    loc = location_el.inner_text().strip() if location_el else "India"
                    link = link_el.get_attribute("href") if link_el else ""

                    if title and link:
                        full_url = link if link.startswith("http") else f"https://www.techgig.com{link}"
                        jobs.append({
                            "title": title,
                            "company": company,
                            "location": loc,
                            "url": full_url,
                            "description": "",
                            "source": "techgig",
                            "posted_date": "",
                        })
                except Exception:
                    continue

            browser.close()

    except Exception as exc:
        logger.error(f"[TIER2] TechGig failed: {exc}")
        return []

    jobs = _dedup(jobs)
    logger.info(f"[SCRAPER] Got {len(jobs)} jobs from techgig")
    return jobs


def scrape_naukri_campus(keyword: str, max_results: int = 50) -> list[dict[str, Any]]:
    """Scrape Naukri Campus (campus.naukri.com) fresher jobs via Playwright.

    Naukri Campus is the dedicated fresher/campus-hiring arm of Naukri —
    specifically targets final-year students and recent graduates.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.error("[TIER2] Naukri Campus requires playwright")
        return []

    jobs: list[dict[str, Any]] = []
    url = "https://campus.naukri.com/jobs?" + urllib.parse.urlencode({
        "k": keyword,
        "l": "India",
    })

    try:
        with sync_playwright() as pw:
            browser = _pw_browser(pw)
            context = _pw_context(browser)
            page = context.new_page()

            logger.info(f"[TIER2] Naukri Campus: {url}")
            page.goto(url, timeout=30_000, wait_until="domcontentloaded")
            time.sleep(random.uniform(2.0, 3.5))

            for _ in range(3):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(random.uniform(1.5, 2.0))

            cards = page.query_selector_all(
                ".jobTuple, .job-card, [class*='JobTuple'], [class*='job-card'], "
                "[class*='jobCard'], article.jobTupleHeader"
            )
            for card in cards[:max_results]:
                try:
                    title_el = card.query_selector(
                        "a.title, .title, h2, h3, [class*='title']"
                    )
                    company_el = card.query_selector(
                        ".companyInfo, a.subTitle, [class*='company'], [class*='employer']"
                    )
                    location_el = card.query_selector(
                        ".location, [class*='location'], [class*='city']"
                    )
                    link_el = card.query_selector("a.title, a[href*='/jobs/']")

                    title = title_el.inner_text().strip() if title_el else ""
                    company = company_el.inner_text().strip() if company_el else ""
                    loc = location_el.inner_text().strip() if location_el else "India"
                    link = link_el.get_attribute("href") if link_el else ""

                    if title and link:
                        full_url = link if link.startswith("http") else f"https://campus.naukri.com{link}"
                        jobs.append({
                            "title": title,
                            "company": company,
                            "location": loc,
                            "url": full_url,
                            "description": "",
                            "source": "naukri_campus",
                            "posted_date": "",
                        })
                except Exception:
                    continue

            browser.close()

    except Exception as exc:
        logger.error(f"[TIER2] Naukri Campus failed: {exc}")
        return []

    jobs = _dedup(jobs)
    logger.info(f"[SCRAPER] Got {len(jobs)} jobs from naukri_campus")
    return jobs


# ── Orchestrator ──────────────────────────────────────────────────────────────

def scrape_all(
    keyword: str,
    location: str = "India",
    date_filter: str = "",
    naukri_limit: int = 100,
    include_tier1: bool = True,
    include_tier2: bool = True,
    include_tier3: bool = False,
) -> list[dict[str, Any]]:
    """Scrape all job boards by tier and deduplicate by URL.

    Args:
        keyword: Job title or keyword to search.
        location: Target location string (default: 'India').
        date_filter: Optional date filter for Indeed/LinkedIn (e.g. 'today').
        naukri_limit: Max results from Naukri (use 30 for daily delta runs).
        include_tier1: Run Tier 1 boards — LinkedIn, Google Jobs.
        include_tier2: Run Tier 2 boards — Indeed, Naukri, Cutshort, Hirist,
                       IIMJobs, Foundit, Shine, TimesJobs, Internshala, Instahyre,
                       Unstop, Freshersworld, HackerEarth, Analytics Vidhya, Wellfound,
                       Apna, Snaphunt, LetsIntern, Hirect, TechGig, Naukri Campus.
        include_tier3: Run Tier 3 company career pages via company_careers.py.

    Returns:
        Deduplicated list of normalized job dicts.
    """
    results: dict[str, list[dict[str, Any]]] = {}

    # Tier 1 — optional (default on)
    if include_tier1:
        results["linkedin"] = scrape_linkedin(keyword, location, mode="india", date_filter=date_filter)
        results["google_jobs"] = scrape_google_jobs(keyword, location)

    # Tier 2 — optional
    if include_tier2:
        results["indeed"] = scrape_indeed(keyword, location, mode="india", date_filter=date_filter)
        results["naukri"] = scrape_naukri(keyword, max_results=naukri_limit)
        results["cutshort"] = scrape_cutshort(keyword)
        results["hirist"] = scrape_hirist(keyword)
        results["iimjobs"] = scrape_iimjobs(keyword)
        results["foundit"] = scrape_foundit(keyword)
        results["shine"] = scrape_shine(keyword)
        results["timesjobs"] = scrape_timesjobs(keyword)
        results["internshala"] = scrape_internshala(keyword)
        results["instahyre"] = scrape_instahyre(keyword)
        results["unstop"] = scrape_unstop(keyword)
        results["freshersworld"] = scrape_freshersworld(keyword)
        results["hackerearth"] = scrape_hackerearth(keyword)
        results["analyticsvidhya"] = scrape_analyticsvidhya(keyword)
        results["wellfound"] = scrape_wellfound(keyword)
        results["apna"] = scrape_apna(keyword)
        results["snaphunt"] = scrape_snaphunt(keyword)
        results["letsintern"] = scrape_letsintern(keyword)
        results["hirect"] = scrape_hirect(keyword)
        results["techgig"] = scrape_techgig(keyword)
        results["naukri_campus"] = scrape_naukri_campus(keyword)

    # Tier 3 — optional
    if include_tier3:
        import company_careers  # lazy import to avoid circular dependency
        results["company_pages"] = company_careers.scrape_all_companies(filter_keyword=keyword)

    counts = " | ".join(
        f"{src.capitalize()}: {len(jobs)}" for src, jobs in results.items()
    )
    all_jobs = [job for jobs in results.values() for job in jobs]
    unique = _dedup(all_jobs)

    logger.info(f"[SCRAPER] {counts} | Total: {len(unique)}")
    return unique


if __name__ == "__main__":
    config.validate()
    jobs = scrape_all(config.DEFAULT_JOB_KEYWORD, include_tier2=False)
    logger.info(f"[SCRAPER] Sample: {jobs[:2]}")

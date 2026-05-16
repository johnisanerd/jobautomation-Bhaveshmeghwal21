"""Google Sheets application tracker."""

from datetime import date, timedelta
from pathlib import Path
from loguru import logger
import config

_HEADERS = [
    "Company",
    "Role",
    "URL",
    "Source",
    "Score",
    "Applied Date",
    "Status",
    "Apply Result",
    "Docs Path",
    "Follow-up Date",
]

_COL: dict[str, int] = {h: i + 1 for i, h in enumerate(_HEADERS)}  # 1-indexed

_sheet = None


def _get_sheet():
    """Return a cached gspread Worksheet, or None if not configured/reachable."""
    global _sheet
    if _sheet is not None:
        return _sheet

    if not config.GOOGLE_SHEETS_ID:
        logger.warning("[TRACKER] GOOGLE_SHEETS_ID not set — Sheets disabled.")
        return None

    creds_path = Path(config.GOOGLE_CREDENTIALS_PATH)
    if not creds_path.exists():
        logger.warning(f"[TRACKER] Credentials file not found: {creds_path} — Sheets disabled.")
        return None

    try:
        import gspread

        gc = gspread.service_account(filename=str(creds_path))
        sh = gc.open_by_key(config.GOOGLE_SHEETS_ID)
        ws = sh.sheet1

        if not ws.row_values(1):
            ws.append_row(_HEADERS, value_input_option="RAW")
            logger.info("[TRACKER] Initialized Google Sheet with header row.")

        _sheet = ws
        logger.info("[TRACKER] Connected to Google Sheets.")
        return _sheet

    except Exception as exc:
        logger.error(f"[TRACKER] Failed to connect to Google Sheets: {exc}")
        return None


def log_application(job: dict, docs: dict | None = None) -> None:
    """Append one application row to the tracker sheet.

    Args:
        job: Scored job dict (title, company, url, source, score).
        docs: Output from generator.generate_docs(), or None.
    """
    today = date.today()
    followup = today + timedelta(days=7)
    docs = docs or {}

    row = [
        job.get("company", ""),
        job.get("title", ""),
        job.get("url", ""),
        job.get("source", ""),
        str(job.get("score", "")),
        str(today),
        "applied",
        docs.get("apply_result", ""),
        docs.get("output_dir", ""),
        str(followup),
    ]

    ws = _get_sheet()
    if ws is None:
        logger.warning(
            f"[TRACKER] Sheets unavailable — skipping log for "
            f"{job.get('title')} @ {job.get('company')}"
        )
        return

    try:
        ws.append_row(row, value_input_option="RAW")
        logger.info(f"[TRACKER] Logged: {job.get('title')} @ {job.get('company')}")
    except Exception as exc:
        logger.error(f"[TRACKER] log_application failed: {exc}")


def update_status(url: str, status: str) -> bool:
    """Find a row by exact URL and update its Status column.

    Args:
        url: Job posting URL (unique key).
        status: New status string, e.g. 'interviewed', 'rejected', 'offer'.

    Returns:
        True if the row was found and updated, False otherwise.
    """
    ws = _get_sheet()
    if ws is None:
        logger.warning(f"[TRACKER] Sheets unavailable — cannot update status for {url!r}")
        return False

    try:
        cell = ws.find(url, in_column=_COL["URL"])
        if cell is None:
            logger.warning(f"[TRACKER] URL not found in sheet: {url!r}")
            return False
        ws.update_cell(cell.row, _COL["Status"], status)
        logger.info(f"[TRACKER] Row {cell.row}: Status → {status!r}")
        return True
    except Exception as exc:
        logger.error(f"[TRACKER] update_status failed: {exc}")
        return False


def update_status_by_company(company: str, status: str) -> bool:
    """Find the most recent row for a company and update its Status column.

    Args:
        company: Company name (case-insensitive).
        status: New status string.

    Returns:
        True if found and updated, False otherwise.
    """
    ws = _get_sheet()
    if ws is None:
        logger.warning(f"[TRACKER] Sheets unavailable — cannot update {company!r}")
        return False

    try:
        records = ws.get_all_records()
        matched_idx: int | None = None
        for i, row in enumerate(records):
            if row.get("Company", "").lower() == company.lower():
                matched_idx = i  # keep iterating to get the last (most recent) match

        if matched_idx is None:
            logger.warning(f"[TRACKER] Company not found in sheet: {company!r}")
            return False

        sheet_row = matched_idx + 2  # +1 header row, +1 for 1-indexing
        ws.update_cell(sheet_row, _COL["Status"], status)
        logger.info(f"[TRACKER] {company!r} row {sheet_row}: Status → {status!r}")
        return True
    except Exception as exc:
        logger.error(f"[TRACKER] update_status_by_company failed: {exc}")
        return False


def get_followups_due() -> list[dict]:
    """Return application rows whose Follow-up Date is today.

    Returns:
        List of row dicts (gspread column names as keys).
    """
    ws = _get_sheet()
    if ws is None:
        return []

    try:
        today_str = str(date.today())
        records = ws.get_all_records()
        due = [r for r in records if r.get("Follow-up Date") == today_str]
        logger.info(f"[TRACKER] {len(due)} follow-up(s) due today.")
        return due
    except Exception as exc:
        logger.error(f"[TRACKER] get_followups_due failed: {exc}")
        return []


def check_already_applied(url: str) -> bool:
    """Return True if this URL already has an applied/submitted entry in the application log."""
    ws = _get_sheet()
    if ws is None:
        return False
    try:
        cell = ws.find(url.strip(), in_column=_COL["URL"])
        if cell is None:
            return False
        status = (ws.cell(cell.row, _COL["Status"]).value or "").lower()
        return status in ("applied", "submitted", "needs_manual_apply")
    except Exception:
        return False


_JOBS_HEADERS = [
    "Company",
    "Title",
    "URL",
    "Source",
    "Score",
    "Reason",
    "Posted Date",
    "Status",
    "Date Added",
]

_JCOL: dict[str, int] = {h: i + 1 for i, h in enumerate(_JOBS_HEADERS)}

_ws_cache: dict[str, object] = {}  # tab_name → gspread.Worksheet


def _get_worksheet(tab_name: str):
    """Open or auto-create a named worksheet tab in the configured spreadsheet.

    Results are cached so repeated calls don't re-authenticate.
    Returns None (with a warning) if Sheets is not configured or unreachable.
    """
    if tab_name in _ws_cache:
        return _ws_cache[tab_name]

    if not config.GOOGLE_SHEETS_ID:
        logger.warning(f"[TRACKER] GOOGLE_SHEETS_ID not set — '{tab_name}' tab disabled.")
        return None

    creds_path = Path(config.GOOGLE_CREDENTIALS_PATH)
    if not creds_path.exists():
        logger.warning(f"[TRACKER] Credentials not found: {creds_path}")
        return None

    try:
        import gspread

        gc = gspread.service_account(filename=str(creds_path))
        sh = gc.open_by_key(config.GOOGLE_SHEETS_ID)

        try:
            ws = sh.worksheet(tab_name)
        except gspread.exceptions.WorksheetNotFound:
            ws = sh.add_worksheet(title=tab_name, rows=5000, cols=len(_JOBS_HEADERS))
            ws.append_row(_JOBS_HEADERS, value_input_option="RAW")
            logger.info(f"[TRACKER] Created '{tab_name}' tab.")

        _ws_cache[tab_name] = ws
        logger.info(f"[TRACKER] Connected to '{tab_name}' tab.")
        return ws

    except Exception as exc:
        logger.error(f"[TRACKER] Failed to open '{tab_name}' tab: {exc}")
        return None


def _get_jobs_sheet():
    """Return the 'Jobs' worksheet (used by daily run and update_job_status)."""
    return _get_worksheet("Jobs")


def _log_jobs_to_tab(tab_name: str, jobs: list[dict], status: str = "scored") -> int:
    """Batch-insert scored jobs into a named tab, skipping existing URLs.

    Args:
        tab_name: Worksheet tab name — created automatically if missing.
        jobs: List of scored job dicts (title, company, url, source, score, reason).
        status: Initial status for all rows.

    Returns:
        Number of new rows inserted.
    """
    ws = _get_worksheet(tab_name)
    if ws is None:
        logger.warning(f"[TRACKER] '{tab_name}' unavailable — skipping batch insert.")
        return 0

    try:
        records = ws.get_all_records()
        existing_urls: set[str] = {r.get("URL", "") for r in records if r.get("URL")}

        today_str = str(date.today())
        rows = []
        for job in jobs:
            url = job.get("url", "")
            if url in existing_urls:
                continue
            rows.append([
                job.get("company", ""),
                job.get("title", ""),
                url,
                job.get("source", ""),
                str(job.get("score", "")),
                job.get("reason", ""),
                job.get("posted_date", ""),
                status,
                today_str,
            ])

        if rows:
            ws.append_rows(rows, value_input_option="RAW")
            logger.info(
                f"[TRACKER] '{tab_name}': inserted {len(rows)} new rows "
                f"({len(jobs) - len(rows)} skipped as duplicates)."
            )
        else:
            logger.info(f"[TRACKER] '{tab_name}': all URLs already present, nothing inserted.")

        return len(rows)

    except Exception as exc:
        logger.error(f"[TRACKER] _log_jobs_to_tab('{tab_name}') failed: {exc}")
        return 0


def log_scored_jobs(jobs: list[dict], status: str = "scored") -> int:
    """Insert daily-run (Tier 1) results into the 'Jobs' tab."""
    return _log_jobs_to_tab("Jobs", jobs, status)


def log_explore_jobs(jobs: list[dict], status: str = "scored") -> int:
    """Insert Tier 1+2 explore results into the 'Explore Jobs' tab."""
    return _log_jobs_to_tab("Explore Jobs", jobs, status)


def log_tier2_jobs(jobs: list[dict], status: str = "scored") -> int:
    """Insert Tier 2-only explore results into the 'Tier 2 Jobs' tab."""
    return _log_jobs_to_tab("Tier 2 Jobs", jobs, status)


def log_tier3_jobs(jobs: list[dict], status: str = "scored") -> int:
    """Insert Tier 3 (company career page) results into the 'Company Jobs' tab."""
    return _log_jobs_to_tab("Company Jobs", jobs, status)


def log_all_jobs(jobs: list[dict], status: str = "scored") -> int:
    """Insert full explore-all (Tier 1+2+3) results into the 'All Jobs' tab."""
    return _log_jobs_to_tab("All Jobs", jobs, status)


def log_post_jobs(jobs: list[dict], status: str = "scored") -> int:
    """Insert LinkedIn post-sourced jobs into the 'Post Jobs' tab."""
    return _log_jobs_to_tab("Post Jobs", jobs, status)


def backfill_posted_dates(url_to_date: dict[str, str]) -> int:
    """Update blank Posted Date cells in the Jobs sheet from a URL→date mapping.

    Reads the actual header row first so it works regardless of whether the sheet
    was created before or after the "Posted Date" column was added.  If the column
    is absent it is appended to the header row before writing any data.
    All writes go through batch_update to stay under API rate limits.

    Args:
        url_to_date: Mapping of job URL → posted_date string.

    Returns:
        Number of cells updated.
    """
    ws = _get_jobs_sheet()
    if ws is None:
        logger.warning("[TRACKER] Jobs sheet unavailable — cannot backfill dates.")
        return 0

    try:
        # Always use the live header row — never assume _JCOL matches the sheet
        header_row = ws.row_values(1)

        if "Posted Date" in header_row:
            col_idx = header_row.index("Posted Date") + 1  # 1-indexed
            logger.info(f"[TRACKER] 'Posted Date' found at column {col_idx}.")
        else:
            # Append the missing header at the next free column
            col_idx = len(header_row) + 1
            # Expand the sheet grid if it doesn't have enough columns
            if ws.col_count < col_idx:
                ws.resize(rows=ws.row_count, cols=col_idx)
            ws.update_cell(1, col_idx, "Posted Date")
            logger.info(f"[TRACKER] Added 'Posted Date' header at column {col_idx}.")

        col_letter = chr(ord("A") + col_idx - 1)

        # Use get_all_values() so we see the raw cell contents for every column,
        # even columns that are beyond the named-header range.
        all_values = ws.get_all_values()  # includes header row at index 0

        # Find URL column index (0-based) from the live header
        try:
            url_col = header_row.index("URL")
        except ValueError:
            logger.error("[TRACKER] 'URL' column not found in Jobs sheet header.")
            return 0

        updates: list[dict] = []
        for i, row_vals in enumerate(all_values[1:], start=0):
            url = row_vals[url_col] if url_col < len(row_vals) else ""
            existing = row_vals[col_idx - 1] if (col_idx - 1) < len(row_vals) else ""
            if existing or not url or url not in url_to_date:
                continue
            sheet_row = i + 2  # +1 header row, +1 for 1-indexing
            updates.append({"range": f"{col_letter}{sheet_row}", "values": [[url_to_date[url]]]})

        if not updates:
            logger.info("[TRACKER] Backfill: nothing to update (all dates already present).")
            return 0

        chunk_size = 300
        for start in range(0, len(updates), chunk_size):
            ws.batch_update(updates[start : start + chunk_size], value_input_option="RAW")

        logger.info(f"[TRACKER] Backfill complete — {len(updates)} cell(s) updated.")
        return len(updates)
    except Exception as exc:
        logger.error(f"[TRACKER] backfill_posted_dates failed: {exc}")
        return 0


def update_job_status(url: str, status: str, tab_name: str = "Jobs") -> bool:
    """Find a job row by URL in the given tab and update its Status column.

    Args:
        url: Job posting URL (unique key).
        status: New status, e.g. 'digest_sent', 'approval_pending', 'approved', 'applied', 'skipped'.
        tab_name: Worksheet tab to search (default 'Jobs').

    Returns:
        True if found and updated.
    """
    ws = _get_worksheet(tab_name)
    if ws is None:
        return False

    try:
        cell = ws.find(url, in_column=_JCOL["URL"])
        if cell is None:
            return False
        ws.update_cell(cell.row, _JCOL["Status"], status)
        return True
    except Exception as exc:
        logger.error(f"[TRACKER] update_job_status failed: {exc}")
        return False


_REFERRAL_HEADERS = [
    "Name",
    "Title",
    "Company",
    "Profile URL",
    "Message",
    "Date",
    "Status",
    "Follow-up Date",
]

_RCOL: dict[str, int] = {h: i + 1 for i, h in enumerate(_REFERRAL_HEADERS)}

_referrals_sheet = None


def _get_referrals_sheet():
    """Return a cached 'Referrals' gspread Worksheet, creating it if needed."""
    global _referrals_sheet
    if _referrals_sheet is not None:
        return _referrals_sheet

    ws = _get_sheet()
    if ws is None:
        return None

    try:
        import gspread
        from pathlib import Path as _Path

        gc = gspread.service_account(filename=str(_Path(config.GOOGLE_CREDENTIALS_PATH)))
        sh = gc.open_by_key(config.GOOGLE_SHEETS_ID)

        try:
            ref_ws = sh.worksheet("Referrals")
        except gspread.exceptions.WorksheetNotFound:
            ref_ws = sh.add_worksheet(title="Referrals", rows=1000, cols=len(_REFERRAL_HEADERS))
            ref_ws.append_row(_REFERRAL_HEADERS, value_input_option="RAW")
            logger.info("[TRACKER] Created 'Referrals' worksheet.")

        _referrals_sheet = ref_ws
        return _referrals_sheet

    except Exception as exc:
        logger.error(f"[TRACKER] Failed to open Referrals sheet: {exc}")
        return None


def log_outreach(
    name: str,
    title: str,
    company: str,
    profile_url: str,
    message: str,
    status: str = "pending_reply",
) -> None:
    """Append one outreach row to the Referrals sheet.

    Args:
        name: Recipient's full name.
        title: Recipient's LinkedIn headline/title.
        company: Company name.
        profile_url: LinkedIn profile URL.
        message: Connection note sent.
        status: e.g. 'pending_reply', 'replied', 'connected', 'no_response'.
    """
    today = date.today()
    followup = today + timedelta(days=7)

    row = [
        name,
        title,
        company,
        profile_url,
        message,
        str(today),
        status,
        str(followup),
    ]

    ws = _get_referrals_sheet()
    if ws is None:
        logger.warning(
            f"[TRACKER] Referrals sheet unavailable — skipping log for {name} @ {company}"
        )
        return

    try:
        ws.append_row(row, value_input_option="RAW")
        logger.info(f"[TRACKER] Referral logged: {name} @ {company}")
    except Exception as exc:
        logger.error(f"[TRACKER] log_outreach failed: {exc}")


def get_all_referral_urls() -> set[str]:
    """Return all LinkedIn profile URLs already logged in the Referrals sheet.

    Used by referral.py to avoid duplicate connection requests.

    Returns:
        Set of profile URL strings.
    """
    ws = _get_referrals_sheet()
    if ws is None:
        return set()

    try:
        records = ws.get_all_records()
        return {r.get("Profile URL", "") for r in records if r.get("Profile URL")}
    except Exception as exc:
        logger.error(f"[TRACKER] get_all_referral_urls failed: {exc}")
        return set()


def get_referral_followups_due() -> list[dict]:
    """Return Referrals rows whose Follow-up Date is today.

    Returns:
        List of row dicts (gspread column names as keys).
    """
    ws = _get_referrals_sheet()
    if ws is None:
        return []

    try:
        today_str = str(date.today())
        records = ws.get_all_records()
        due = [r for r in records if r.get("Follow-up Date") == today_str]
        logger.info(f"[TRACKER] {len(due)} referral follow-up(s) due today.")
        return due
    except Exception as exc:
        logger.error(f"[TRACKER] get_referral_followups_due failed: {exc}")
        return []


if __name__ == "__main__":
    config.validate()
    sample_job = {
        "title": "Data Analyst Intern",
        "company": "Razorpay",
        "url": "https://jobs.lever.co/razorpay/test-123",
        "source": "lever",
        "score": 87,
    }
    log_application(
        sample_job,
        {"apply_result": "submitted", "output_dir": "applications/razorpay-data-analyst-intern"},
    )
    print("Done. Check your Google Sheet.")

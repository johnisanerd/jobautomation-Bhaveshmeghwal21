"""Telegram bot for sending digests, approval requests, and handling callbacks."""

import asyncio
import hashlib
from typing import Any, Callable
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes
from telegram.request import HTTPXRequest
from loguru import logger
import config

_bot: Bot | None = None

# Telegram callback_data is limited to 64 bytes.
# We hash long URLs to a 16-char key and keep the full URL here.
_url_registry: dict[str, str] = {}


def _register_url(url: str) -> str:
    """Return a short 16-char key for url, storing the mapping for later lookup."""
    key = hashlib.md5(url.encode()).hexdigest()[:16]
    _url_registry[key] = url
    return key


def _resolve_url(key: str) -> str:
    """Return the full URL for a previously registered key (falls back to key itself)."""
    return _url_registry.get(key, key)


def _make_request() -> HTTPXRequest:
    """Return an HTTPXRequest with SSL verification controlled by SSL_NO_VERIFY."""
    return HTTPXRequest(
        connect_timeout=20,
        read_timeout=60,
        write_timeout=60,
        pool_timeout=10,
        httpx_kwargs={"verify": not config.SSL_NO_VERIFY},
    )


def _get_bot() -> Bot:
    """Return a singleton Telegram Bot instance."""
    global _bot
    if _bot is None:
        _bot = Bot(token=config.TELEGRAM_BOT_TOKEN, request=_make_request())
    return _bot


async def send_message(text: str) -> None:
    """Send a plain-text message to the configured chat.

    Args:
        text: Message content (Markdown-safe).
    """
    bot = _get_bot()
    await bot.send_message(
        chat_id=config.TELEGRAM_CHAT_ID,
        text=text,
        parse_mode="Markdown",
    )
    logger.info(f"[TELEGRAM] Sent message ({len(text)} chars)")


async def send_job_digest(jobs: list[dict[str, Any]]) -> None:
    """Send a formatted digest of top-scored jobs.

    Args:
        jobs: List of scored job dicts (title, company, url, score, reason).
    """
    if not jobs:
        await send_message("No matching jobs found today.")
        return

    lines = ["*Today's Top Job Matches*\n"]
    for i, job in enumerate(jobs, 1):
        score = job.get("score", "?")
        title = job.get("title", "Unknown Role")
        company = job.get("company", "Unknown Company")
        url = job.get("url", "")
        reason = job.get("reason", "")
        lines.append(
            f"{i}. *{title}* @ {company} — Score: {score}/100\n"
            f"   _{reason}_\n"
            f"   [Apply]({url})"
        )

    text = "\n\n".join(lines)
    await send_message(text)
    logger.info(f"[TELEGRAM] Digest sent with {len(jobs)} jobs.")


async def send_approval_card(job: dict[str, Any]) -> "asyncio.Future[str]":
    """Send an inline approval card and return the future — does NOT wait for response.

    The future resolves to "approved" or "skipped" when the user presses a button.
    There is no timeout; the future waits indefinitely.

    Args:
        job: Scored job dict.

    Returns:
        Future that resolves to "approved" or "skipped".
    """
    title = job.get("title", "Unknown Role")
    company = job.get("company", "Unknown Company")
    url = job.get("url", "")
    score = job.get("score", "?")
    reason = job.get("reason", "")

    text = (
        f"*Apply to this job?*\n\n"
        f"*{title}* @ {company}\n"
        f"Score: {score}/100\n"
        f"_{reason}_\n"
        f"[View Job]({url})"
    )

    key = _register_url(url)
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Approve", callback_data=f"approve|{key}"),
            InlineKeyboardButton("Skip", callback_data=f"skip|{key}"),
        ]
    ])

    bot = _get_bot()
    await bot.send_message(
        chat_id=config.TELEGRAM_CHAT_ID,
        text=text,
        parse_mode="Markdown",
        reply_markup=keyboard,
    )
    logger.info(f"[TELEGRAM] Approval card sent for {title!r} @ {company!r}")

    result_future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
    _pending_approvals[key] = result_future
    return result_future


async def send_approval_request(job: dict[str, Any]) -> str:
    """Send an inline-button approval card for a single job and wait for response.

    Waits indefinitely — no timeout. Used by daily_run for sequential approval.

    Args:
        job: Scored job dict.

    Returns:
        "approved" or "skipped" based on user button press.
    """
    future = await send_approval_card(job)
    return await future


_pending_approvals: dict[str, asyncio.Future[str]] = {}


async def send_apply_confirmation(job: dict[str, Any], screenshot_path: Any) -> str:
    """Send a screenshot of the filled application form and wait for submit/skip decision.

    Args:
        job: Job dict (title, company, url, score).
        screenshot_path: Path to the PNG screenshot of the filled form.

    Returns:
        "submit" or "skip".
    """
    title = job.get("title", "Unknown Role")
    company = job.get("company", "Unknown Company")
    url = job.get("url", "")

    caption = (
        f"*Ready to submit*\n\n"
        f"*{title}* @ {company}\n"
        f"[View Job]({url})\n\n"
        f"Form has been filled. Review and confirm submission."
    )

    url_key = _register_url(url)
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Submit Application", callback_data=f"submit_app|{url_key}"),
            InlineKeyboardButton("Skip Submission", callback_data=f"skip_app|{url_key}"),
        ]
    ])

    bot = _get_bot()
    key = f"app_{url_key}"
    result_future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
    _pending_approvals[key] = result_future

    try:
        from pathlib import Path
        p = Path(screenshot_path)
        if p.exists():
            with open(p, "rb") as f:
                await bot.send_photo(
                    chat_id=config.TELEGRAM_CHAT_ID,
                    photo=f,
                    caption=caption,
                    parse_mode="Markdown",
                    reply_markup=keyboard,
                )
        else:
            await bot.send_message(
                chat_id=config.TELEGRAM_CHAT_ID,
                text=caption,
                parse_mode="Markdown",
                reply_markup=keyboard,
            )
    except Exception as exc:
        logger.error(f"[TELEGRAM] send_apply_confirmation failed: {exc}")
        _pending_approvals.pop(key, None)
        return "skip"

    logger.info(f"[TELEGRAM] Apply confirmation sent for {title!r} @ {company!r}")

    try:
        return await asyncio.wait_for(result_future, timeout=300)
    except asyncio.TimeoutError:
        logger.warning(f"[TELEGRAM] No submit response for {title!r} — skipping.")
        _pending_approvals.pop(key, None)
        return "skip"


async def send_apply_notification(job: dict[str, Any], screenshot_path: Any, note: str) -> None:
    """Send a screenshot with a manual-submission notice (no buttons).

    Used for semi-auto ATS (Naukri, Darwinbox, Keka, Zoho) and fill-only ATS
    (Workday, Taleo) where auto-submission is not possible.

    Args:
        job: Job dict.
        screenshot_path: Path to the filled form screenshot.
        note: Short explanatory note to include in the caption.
    """
    title = job.get("title", "Unknown Role")
    company = job.get("company", "Unknown Company")
    url = job.get("url", "")

    caption = (
        f"*Application prepared*\n\n"
        f"*{title}* @ {company}\n"
        f"[Open to Submit]({url})\n\n"
        f"_{note}_"
    )

    bot = _get_bot()
    try:
        from pathlib import Path
        p = Path(screenshot_path)
        if p.exists():
            with open(p, "rb") as f:
                await bot.send_photo(
                    chat_id=config.TELEGRAM_CHAT_ID,
                    photo=f,
                    caption=caption,
                    parse_mode="Markdown",
                )
        else:
            await bot.send_message(
                chat_id=config.TELEGRAM_CHAT_ID,
                text=caption,
                parse_mode="Markdown",
            )
    except Exception as exc:
        logger.error(f"[TELEGRAM] send_apply_notification failed: {exc}")

    logger.info(f"[TELEGRAM] Manual-apply notification sent for {title!r}")


async def _send_chunked(header: str, lines: list[str], label: str) -> None:
    """Send a list of lines split into ≤4000-char Telegram messages."""
    LIMIT = 4000
    chunk: list[str] = [header]
    chunk_size = len(header)

    for line in lines:
        entry = "\n\n" + line
        if chunk_size + len(entry) > LIMIT:
            await send_message("\n".join(chunk))
            chunk = [line]
            chunk_size = len(line)
        else:
            chunk.append(entry)
            chunk_size += len(entry)

    if chunk:
        await send_message("\n".join(chunk))
    logger.info(f"[TELEGRAM] {label} sent ({len(lines)} item(s)).")


async def send_followup_reminders(followups: list[dict]) -> None:
    """Send Telegram messages listing applications due for follow-up today.

    Args:
        followups: List of row dicts from tracker.get_followups_due().
    """
    lines = []
    for item in followups:
        role = item.get("Role", "Unknown Role")
        company = item.get("Company", "Unknown Company")
        applied = item.get("Applied Date", "?")
        url = item.get("URL", "")
        lines.append(
            f"• *{role}* @ {company} — Applied: {applied}\n"
            f"  [Job Link]({url})"
        )
    await _send_chunked(
        f"*Follow-up reminders for today ({len(followups)} total):*",
        lines,
        "Follow-up reminders",
    )


async def send_referral_summary(stats: dict) -> None:
    """Send a Telegram summary after a referral outreach run.

    Args:
        stats: Dict with keys 'sent', 'skipped', 'failed'.
    """
    sent = stats.get("sent", 0)
    skipped = stats.get("skipped", 0)
    failed = stats.get("failed", 0)
    text = (
        f"*Referral outreach complete*\n\n"
        f"Sent: {sent} connection request(s)\n"
        f"Skipped (already messaged): {skipped}\n"
        f"Failed: {failed}"
    )
    await send_message(text)
    logger.info("[TELEGRAM] Referral summary sent.")


async def send_referral_followup_reminders(followups: list[dict]) -> None:
    """Send Telegram messages listing referral contacts due for follow-up today.

    Args:
        followups: List of row dicts from tracker.get_referral_followups_due().
    """
    lines = []
    for item in followups:
        name = item.get("Name", "?")
        title = item.get("Title", "")
        company = item.get("Company", "?")
        url = item.get("Profile URL", "")
        sent_date = item.get("Date", "?")
        lines.append(
            f"• *{name}* ({title}) @ {company} — Sent: {sent_date}\n"
            f"  [LinkedIn Profile]({url})"
        )
    await _send_chunked(
        f"*Referral follow-up reminders for today ({len(followups)} total):*",
        lines,
        "Referral follow-up reminders",
    )


async def handle_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /status <company> <new_status> — update application status in Google Sheets.

    Usage:
        /status Razorpay interviewed
        /status CRED rejected
        /status Zepto offer

    Args:
        update: Telegram Update object.
        context: Contains context.args (words after /status).
    """
    import tracker

    args = context.args or []
    if len(args) < 2:
        await update.message.reply_text(
            "Usage: /status <company> <new\\_status>\n"
            "Example: /status Razorpay interviewed",
            parse_mode="Markdown",
        )
        return

    company = args[0]
    new_status = " ".join(args[1:])
    ok = tracker.update_status_by_company(company, new_status)

    if ok:
        await update.message.reply_text(
            f"Updated: *{company}* → `{new_status}`",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(
            f"Company not found in tracker: *{company}*\n"
            f"Check the spelling or use the exact company name from the sheet.",
            parse_mode="Markdown",
        )

    logger.info(f"[TELEGRAM] /status command: {company!r} → {new_status!r} (ok={ok})")


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Process inline button presses and resolve the matching future.

    Handles both job approval callbacks (approve|/skip|) and application
    submission callbacks (submit_app|/skip_app|).

    Args:
        update: Telegram Update object.
        context: Callback context (unused).
    """
    query = update.callback_query
    if query is None:
        return

    # Acknowledge the button press — non-critical, ignore timeouts
    try:
        await query.answer()
    except Exception:
        pass

    data: str = query.data or ""

    if "|" not in data:
        return

    action, payload = data.split("|", 1)

    # Determine future key and result string
    if action == "approve":
        key, result, status_text = payload, "approved", "Approved!"
    elif action == "skip":
        key, result, status_text = payload, "skipped", "Skipped."
    elif action == "submit_app":
        key, result, status_text = f"app_{payload}", "submit", "Submitting..."
    elif action == "skip_app":
        key, result, status_text = f"app_{payload}", "skip", "Submission skipped."
    else:
        return

    # Resolve short key back to full URL for logging (payload is the short key)
    _ = _resolve_url(payload)

    future = _pending_approvals.pop(key, None)
    if future and not future.done():
        future.set_result(result)

    # Edit the message to reflect the decision (ignore if already updated by a duplicate tap)
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass
    try:
        if query.message and query.message.photo:
            current = query.message.caption or ""
            await query.edit_message_caption(
                caption=f"{current}\n\n*{status_text}*",
                parse_mode="Markdown",
            )
        elif query.message and query.message.text:
            await query.edit_message_text(
                text=f"{query.message.text}\n\n*{status_text}*",
                parse_mode="Markdown",
            )
    except Exception:
        pass

    logger.info(f"[TELEGRAM] Callback: {action} → {payload[:60]}")


def build_application() -> Application:
    """Build and return the Telegram Application with handlers registered.

    Returns:
        Configured Application instance ready to run.
    """
    req = _make_request()
    app = (
        Application.builder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .request(req)
        .get_updates_request(req)
        .build()
    )
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(CommandHandler("status", handle_status_command))
    return app


if __name__ == "__main__":
    config.validate()

    async def _test() -> None:
        await send_message("Job Automation bot is online!")

    asyncio.run(_test())
    logger.info("[TELEGRAM] Test message sent.")

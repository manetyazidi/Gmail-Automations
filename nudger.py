"""The poll-cycle business logic: Outreach -> Gmail reply -> DB record."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import db
from config import Config
from gmail_client import GmailClient
from outreach import HotMailing, OutreachClient

logger = logging.getLogger(__name__)


def render_body(now_local: datetime) -> str:
    days_until_friday = (4 - now_local.weekday()) % 7 or 7
    friday = now_local + timedelta(days=days_until_friday)
    friday_str = friday.strftime("%A %B %-d")
    return (
        "hey love to connect before the end of the week. "
        "usually fridays are more free — hows before lunch, "
        f"maybe {friday_str} at 10am CST work?\n\n"
        "Cheers,\nManet"
    )


def run_poll_cycle(
    config: Config,
    outreach: OutreachClient,
    gmail: GmailClient,
) -> int:
    """One poll iteration. Returns the number of nudges sent."""
    cursor = db.get_cursor(config.db_path)
    # First run: look back a day so we don't act on years of history.
    since = cursor or (datetime.now(timezone.utc) - timedelta(days=1))

    mailings = outreach.list_hot_mailings(
        open_threshold=config.open_threshold, since=since
    )
    logger.info(
        "Outreach returned %d hot mailings (openCount > %d) since %s",
        len(mailings),
        config.open_threshold,
        since.isoformat(),
    )

    sent = 0
    latest_seen = cursor
    now_local = datetime.now(ZoneInfo(config.timezone))

    for mailing in mailings:
        if latest_seen is None or mailing.updated_at > latest_seen:
            latest_seen = mailing.updated_at

        if not _process_one(config, gmail, mailing, now_local):
            continue
        sent += 1

    if latest_seen is not None:
        db.set_cursor(config.db_path, latest_seen)

    return sent


def _process_one(
    config: Config,
    gmail: GmailClient,
    mailing: HotMailing,
    now_local: datetime,
) -> bool:
    thread = gmail.find_thread(
        recipient=mailing.recipient_email,
        subject=mailing.subject,
        lookback_days=config.thread_lookback_days,
    )
    if thread is None:
        logger.info(
            "No Gmail thread found for %s / %r; skipping",
            mailing.recipient_email,
            mailing.subject,
        )
        return False

    if db.already_nudged(config.db_path, thread.thread_id):
        logger.debug("Thread %s already nudged; skipping", thread.thread_id)
        return False

    body = render_body(now_local)

    if config.dry_run:
        logger.info(
            "[DRY-RUN] Would reply to %s on thread %s (opens=%d): %s",
            mailing.recipient_email,
            thread.thread_id,
            mailing.open_count,
            body.replace("\n", " | "),
        )
        return True

    message_id = gmail.reply(
        from_addr=config.manet_email,
        to_addr=mailing.recipient_email,
        thread=thread,
        body=body,
    )
    inserted = db.record_nudge(
        config.db_path,
        thread_id=thread.thread_id,
        recipient_email=mailing.recipient_email,
        recipient_name=mailing.recipient_name,
        subject=mailing.subject,
        outreach_mailing_id=mailing.mailing_id,
        open_count_at_nudge=mailing.open_count,
    )
    if not inserted:
        logger.warning(
            "Race: reply %s sent but thread %s was already recorded",
            message_id,
            thread.thread_id,
        )
    else:
        logger.info(
            "Replied to %s on thread %s (opens=%d, msg=%s)",
            mailing.recipient_email,
            thread.thread_id,
            mailing.open_count,
            message_id,
        )
    return True

"""Friday-morning weekly recap email."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import db
from config import Config
from gmail_client import GmailClient

logger = logging.getLogger(__name__)


def send_weekly_recap(config: Config, gmail: GmailClient) -> str | None:
    tz = ZoneInfo(config.timezone)
    now_local = datetime.now(tz)
    since = now_local - timedelta(days=7)

    rows = db.nudges_since(config.db_path, since)

    if not rows:
        subject = "Weekly outreach auto-nudge recap — 0 prospects"
        body = (
            f"No auto-nudges went out this past week "
            f"(since {since.strftime('%a %b %-d')}).\n"
        )
    else:
        subject = f"Weekly outreach auto-nudge recap — {len(rows)} prospects"
        lines = [
            f"Auto-replied to these prospects since {since.strftime('%a %b %-d')}:",
            "",
        ]
        for i, row in enumerate(rows, 1):
            nudged_local = _to_local(row.nudged_at, tz)
            who = (
                f"{row.recipient_name} <{row.recipient_email}>"
                if row.recipient_name
                else row.recipient_email
            )
            lines.append(
                f"{i:>2}. {who} — \"{row.subject}\" "
                f"({row.open_count_at_nudge} opens, "
                f"{nudged_local.strftime('%a %-I:%M%p')})"
            )
        body = "\n".join(lines) + "\n"

    if config.dry_run:
        logger.info("[DRY-RUN] Would send recap:\n%s\n\n%s", subject, body)
        return None

    message_id = gmail.send_self(
        from_addr=config.manet_email, subject=subject, body=body
    )
    logger.info("Sent weekly recap (msg=%s, %d rows)", message_id, len(rows))
    return message_id


def _to_local(dt: datetime, tz: ZoneInfo) -> datetime:
    if dt.tzinfo is None:
        # SQLite's CURRENT_TIMESTAMP returns UTC without tzinfo.
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    return dt.astimezone(tz)

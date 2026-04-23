"""Runtime configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    outreach_client_id: str
    outreach_client_secret: str
    outreach_refresh_token: str
    outreach_redirect_uri: str

    gmail_credentials_path: Path
    gmail_token_path: Path

    manet_email: str

    poll_interval_min: int
    open_threshold: int
    thread_lookback_days: int
    db_path: Path

    recap_cron_day: str
    recap_cron_hour: int
    recap_cron_minute: int
    timezone: str

    dry_run: bool


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def load_config(dry_run: bool = False) -> Config:
    return Config(
        outreach_client_id=_required("OUTREACH_CLIENT_ID"),
        outreach_client_secret=_required("OUTREACH_CLIENT_SECRET"),
        outreach_refresh_token=_required("OUTREACH_REFRESH_TOKEN"),
        outreach_redirect_uri=os.environ.get(
            "OUTREACH_REDIRECT_URI", "http://localhost:8080/callback"
        ),
        gmail_credentials_path=Path(
            os.environ.get("GMAIL_CREDENTIALS_PATH", "credentials.json")
        ),
        gmail_token_path=Path(os.environ.get("GMAIL_TOKEN_PATH", "token.json")),
        manet_email=_required("MANET_EMAIL"),
        poll_interval_min=int(os.environ.get("POLL_INTERVAL_MIN", "5")),
        open_threshold=int(os.environ.get("OPEN_THRESHOLD", "2")),
        thread_lookback_days=int(os.environ.get("THREAD_LOOKBACK_DAYS", "30")),
        db_path=Path(os.environ.get("DB_PATH", "nudges.db")),
        recap_cron_day=os.environ.get("RECAP_CRON_DAY", "fri"),
        recap_cron_hour=int(os.environ.get("RECAP_CRON_HOUR", "8")),
        recap_cron_minute=int(os.environ.get("RECAP_CRON_MINUTE", "0")),
        timezone=os.environ.get("TIMEZONE", "America/Chicago"),
        dry_run=dry_run,
    )

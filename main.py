"""Entry point: starts the APScheduler with the poll loop and Friday recap."""

from __future__ import annotations

import argparse
import logging
import signal
import sys
from datetime import datetime

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

import db
from config import Config, load_config
from gmail_client import GmailClient
from nudger import run_poll_cycle
from outreach import OutreachClient
from recap import send_weekly_recap


def _build_clients(config: Config) -> tuple[OutreachClient, GmailClient]:
    outreach = OutreachClient(
        client_id=config.outreach_client_id,
        client_secret=config.outreach_client_secret,
        refresh_token=config.outreach_refresh_token,
        redirect_uri=config.outreach_redirect_uri,
    )
    gmail = GmailClient(
        credentials_path=config.gmail_credentials_path,
        token_path=config.gmail_token_path,
    )
    return outreach, gmail


def _run_poll_job(config: Config, outreach: OutreachClient, gmail: GmailClient) -> None:
    try:
        count = run_poll_cycle(config, outreach, gmail)
        if count:
            logging.info("Poll cycle sent %d nudge(s)", count)
    except Exception:
        logging.exception("Poll cycle failed")


def _run_recap_job(config: Config, gmail: GmailClient) -> None:
    try:
        send_weekly_recap(config, gmail)
    except Exception:
        logging.exception("Recap job failed")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gmail outreach auto-responder")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log the reply that would be sent without actually sending",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single poll cycle and exit (useful for testing)",
    )
    parser.add_argument(
        "--recap-now",
        action="store_true",
        help="Send the weekly recap immediately and exit",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    config = load_config(dry_run=args.dry_run)
    db.init_db(config.db_path)
    outreach, gmail = _build_clients(config)

    if args.once:
        count = run_poll_cycle(config, outreach, gmail)
        logging.info("Single poll cycle done; %d nudge(s) sent", count)
        return 0

    if args.recap_now:
        send_weekly_recap(config, gmail)
        return 0

    scheduler = BlockingScheduler(timezone=config.timezone)
    scheduler.add_job(
        _run_poll_job,
        trigger=IntervalTrigger(minutes=config.poll_interval_min),
        args=(config, outreach, gmail),
        id="poll",
        next_run_time=datetime.now(),
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        _run_recap_job,
        trigger=CronTrigger(
            day_of_week=config.recap_cron_day,
            hour=config.recap_cron_hour,
            minute=config.recap_cron_minute,
            timezone=config.timezone,
        ),
        args=(config, gmail),
        id="recap",
        max_instances=1,
        coalesce=True,
    )

    def _shutdown(signum, frame):  # noqa: ARG001
        logging.info("Shutdown signal received; stopping scheduler")
        scheduler.shutdown(wait=False)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    logging.info(
        "Starting scheduler: poll every %d min, recap %s %02d:%02d %s",
        config.poll_interval_min,
        config.recap_cron_day,
        config.recap_cron_hour,
        config.recap_cron_minute,
        config.timezone,
    )
    scheduler.start()
    return 0


if __name__ == "__main__":
    sys.exit(main())

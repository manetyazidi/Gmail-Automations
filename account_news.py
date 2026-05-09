"""Daily account-news pipeline.

Fetches accounts owned by an AE from Salesforce, searches the web for
M&A / funding / IPO news per account, and emails an HTML digest.

Entry point intended to be invoked once per day (e.g. GitHub Actions cron).
"""

from __future__ import annotations

import argparse
import html
import logging
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from gmail_client import GmailClient
from news_classifier import ClassifiedNews, filter_relevant, relevant_query_terms
from news_search import search_news
from salesforce_client import SalesforceAccount, SalesforceClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AccountNewsConfig:
    sf_login_url: str
    sf_client_id: str
    sf_client_secret: str
    sf_username: str
    sf_password: str
    sf_security_token: str

    ae_owner_name: str

    google_api_key: str | None
    google_cse_id: str | None

    gmail_credentials_path: Path
    gmail_token_path: Path
    recipient_email: str

    lookback_days: int
    timezone: str


def _required(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return v


def load_account_news_config() -> AccountNewsConfig:
    load_dotenv()
    return AccountNewsConfig(
        sf_login_url=os.environ.get("SF_LOGIN_URL", "https://login.salesforce.com"),
        sf_client_id=_required("SF_CLIENT_ID"),
        sf_client_secret=_required("SF_CLIENT_SECRET"),
        sf_username=_required("SF_USERNAME"),
        sf_password=_required("SF_PASSWORD"),
        sf_security_token=os.environ.get("SF_SECURITY_TOKEN", ""),
        ae_owner_name=_required("AE_OWNER_NAME"),
        google_api_key=os.environ.get("GOOGLE_API_KEY") or None,
        google_cse_id=os.environ.get("GOOGLE_CSE_ID") or None,
        gmail_credentials_path=Path(
            os.environ.get("GMAIL_CREDENTIALS_PATH", "credentials.json")
        ),
        gmail_token_path=Path(os.environ.get("GMAIL_TOKEN_PATH", "token.json")),
        recipient_email=_required("MANET_EMAIL"),
        lookback_days=int(os.environ.get("NEWS_LOOKBACK_DAYS", "1")),
        timezone=os.environ.get("TIMEZONE", "America/Chicago"),
    )


def run(config: AccountNewsConfig, *, dry_run: bool = False) -> None:
    sf = SalesforceClient(
        login_url=config.sf_login_url,
        client_id=config.sf_client_id,
        client_secret=config.sf_client_secret,
        username=config.sf_username,
        password=config.sf_password,
        security_token=config.sf_security_token,
    )
    accounts = sf.query_accounts_by_owner(config.ae_owner_name)
    instance_url = sf.instance_url

    query_terms = relevant_query_terms()
    results: dict[str, list[ClassifiedNews]] = {}
    for account in accounts:
        items = search_news(
            account_name=account.name,
            google_api_key=config.google_api_key,
            google_cse_id=config.google_cse_id,
            lookback_days=config.lookback_days,
            query_terms=query_terms,
        )
        relevant = filter_relevant(items)
        results[account.id] = relevant
        logger.info(
            "Account %s: %d raw / %d relevant",
            account.name,
            len(items),
            len(relevant),
        )

    now = datetime.now(ZoneInfo(config.timezone))
    subject = f"Account news digest — {now.strftime('%a %b %d, %Y')}"
    text_body, html_body = _compose_email(
        accounts=accounts,
        results=results,
        instance_url=instance_url,
        ae_owner_name=config.ae_owner_name,
        generated_at=now,
        lookback_days=config.lookback_days,
    )

    if dry_run:
        logger.info("DRY RUN — would send to %s", config.recipient_email)
        print(subject)
        print(text_body)
        return

    gmail = GmailClient(
        credentials_path=config.gmail_credentials_path,
        token_path=config.gmail_token_path,
    )
    msg_id = gmail.send_self_html(
        from_addr=config.recipient_email,
        subject=subject,
        text_body=text_body,
        html_body=html_body,
    )
    logger.info("Sent digest message id=%s", msg_id)


def _compose_email(
    *,
    accounts: list[SalesforceAccount],
    results: dict[str, list[ClassifiedNews]],
    instance_url: str,
    ae_owner_name: str,
    generated_at: datetime,
    lookback_days: int,
) -> tuple[str, str]:
    accounts_with_news = [a for a in accounts if results.get(a.id)]
    accounts_without_news = [a for a in accounts if not results.get(a.id)]

    text_lines: list[str] = []
    text_lines.append(
        f"Account news digest for {ae_owner_name} "
        f"(window: last {lookback_days}d, generated "
        f"{generated_at.strftime('%Y-%m-%d %H:%M %Z')})"
    )
    text_lines.append("")
    text_lines.append(
        f"Accounts scanned: {len(accounts)}    "
        f"With news: {len(accounts_with_news)}"
    )
    text_lines.append("=" * 60)

    for account in accounts_with_news:
        sf_url = f"{instance_url}/lightning/r/Account/{account.id}/view"
        text_lines.append("")
        text_lines.append(f"{account.name}")
        text_lines.append(f"  Salesforce: {sf_url}")
        by_category: dict[str, list[ClassifiedNews]] = defaultdict(list)
        for c in results[account.id]:
            by_category[c.category].append(c)
        for category, entries in by_category.items():
            text_lines.append(f"  [{category}]")
            for entry in entries:
                text_lines.append(f"    - {entry.item.title}")
                if entry.item.source:
                    text_lines.append(f"      source: {entry.item.source}")
                text_lines.append(f"      {entry.item.url}")

    if accounts_without_news:
        text_lines.append("")
        text_lines.append("-" * 60)
        text_lines.append("No news today:")
        for a in accounts_without_news:
            text_lines.append(f"  - {a.name}")

    text_body = "\n".join(text_lines)
    html_body = _render_html(
        accounts_with_news=accounts_with_news,
        accounts_without_news=accounts_without_news,
        results=results,
        instance_url=instance_url,
        ae_owner_name=ae_owner_name,
        generated_at=generated_at,
        lookback_days=lookback_days,
        total_accounts=len(accounts),
    )
    return text_body, html_body


def _render_html(
    *,
    accounts_with_news: list[SalesforceAccount],
    accounts_without_news: list[SalesforceAccount],
    results: dict[str, list[ClassifiedNews]],
    instance_url: str,
    ae_owner_name: str,
    generated_at: datetime,
    lookback_days: int,
    total_accounts: int,
) -> str:
    parts: list[str] = []
    parts.append(
        '<!DOCTYPE html><html><body style="font-family:-apple-system,'
        'Segoe UI,Roboto,sans-serif;color:#222;max-width:720px;margin:auto;">'
    )
    parts.append(
        f"<h2 style='margin-bottom:4px;'>Account news digest</h2>"
        f"<div style='color:#666;font-size:13px;margin-bottom:16px;'>"
        f"AE: <strong>{html.escape(ae_owner_name)}</strong> "
        f"&middot; window: last {lookback_days}d "
        f"&middot; generated "
        f"{html.escape(generated_at.strftime('%Y-%m-%d %H:%M %Z'))}"
        f"</div>"
    )
    parts.append(
        f"<div style='background:#f5f7fa;padding:10px 14px;border-radius:6px;"
        f"margin-bottom:18px;font-size:14px;'>"
        f"Accounts scanned: <strong>{total_accounts}</strong> "
        f"&middot; With news: <strong>{len(accounts_with_news)}</strong>"
        f"</div>"
    )

    if not accounts_with_news:
        parts.append(
            "<p><em>No qualifying M&amp;A, funding, or IPO news found "
            "across the AE's book today.</em></p>"
        )

    for account in accounts_with_news:
        sf_url = f"{instance_url}/lightning/r/Account/{account.id}/view"
        parts.append(
            f"<div style='border:1px solid #e5e7eb;border-radius:8px;"
            f"padding:14px 16px;margin-bottom:14px;'>"
            f"<div style='display:flex;justify-content:space-between;'>"
            f"<strong style='font-size:16px;'>{html.escape(account.name)}</strong>"
            f"<a href='{html.escape(sf_url)}' "
            f"style='font-size:12px;color:#0b5fff;text-decoration:none;'>"
            f"open in Salesforce &rarr;</a>"
            f"</div>"
        )
        by_category: dict[str, list[ClassifiedNews]] = defaultdict(list)
        for c in results[account.id]:
            by_category[c.category].append(c)
        for category, entries in by_category.items():
            parts.append(
                f"<div style='margin-top:10px;'>"
                f"<span style='display:inline-block;font-size:11px;"
                f"background:{_category_color(category)};color:#fff;"
                f"padding:2px 8px;border-radius:10px;letter-spacing:0.3px;'>"
                f"{html.escape(category)}</span>"
                f"<ul style='margin:6px 0 0 0;padding-left:20px;'>"
            )
            for entry in entries:
                date_str = ""
                if entry.item.published:
                    date_str = (
                        f"<span style='color:#888;font-size:12px;'> "
                        f"&middot; {entry.item.published.strftime('%b %d')}</span>"
                    )
                source_str = (
                    f"<span style='color:#888;font-size:12px;'> "
                    f"&middot; {html.escape(entry.item.source)}</span>"
                    if entry.item.source
                    else ""
                )
                parts.append(
                    f"<li style='margin-bottom:6px;'>"
                    f"<a href='{html.escape(entry.item.url)}' "
                    f"style='color:#0b5fff;text-decoration:none;'>"
                    f"{html.escape(entry.item.title)}</a>"
                    f"{source_str}{date_str}"
                    f"</li>"
                )
            parts.append("</ul></div>")
        parts.append("</div>")

    if accounts_without_news:
        parts.append(
            "<details style='margin-top:18px;color:#555;'>"
            f"<summary style='cursor:pointer;'>No news today "
            f"({len(accounts_without_news)} accounts)</summary>"
            "<ul style='margin-top:8px;'>"
        )
        for a in accounts_without_news:
            sf_url = f"{instance_url}/lightning/r/Account/{a.id}/view"
            parts.append(
                f"<li><a href='{html.escape(sf_url)}' "
                f"style='color:#0b5fff;text-decoration:none;'>"
                f"{html.escape(a.name)}</a></li>"
            )
        parts.append("</ul></details>")

    parts.append("</body></html>")
    return "".join(parts)


def _category_color(category: str) -> str:
    return {
        "M&A": "#7c3aed",
        "Funding": "#0b5fff",
        "IPO": "#059669",
    }.get(category, "#374151")


def main() -> None:
    parser = argparse.ArgumentParser(description="Daily account news digest")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    config = load_account_news_config()
    run(config, dry_run=args.dry_run)


if __name__ == "__main__":
    main()

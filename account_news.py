"""Daily account-news digest: Salesforce -> news search -> Gmail."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from html import escape
from pathlib import Path

import httpx
from dotenv import load_dotenv

from gmail_client import GmailClient
from news_classifier import ClassifiedHit, classify_all
from news_search import search_account
from salesforce_client import SalesforceAccount, SalesforceClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AccountReport:
    account: SalesforceAccount
    hits: list[ClassifiedHit]


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def run(*, dry_run: bool = False) -> int:
    load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    ae_email = _required("AE_EMAIL")
    digest_to = os.environ.get("DIGEST_TO", ae_email)

    sf = SalesforceClient(
        client_id=_required("SF_CLIENT_ID"),
        client_secret=_required("SF_CLIENT_SECRET"),
        refresh_token=_required("SF_REFRESH_TOKEN"),
        login_url=os.environ.get("SF_LOGIN_URL", "https://login.salesforce.com"),
    )

    accounts = sf.accounts_owned_by(ae_email)
    logger.info("Loaded %d accounts owned by %s", len(accounts), ae_email)

    google_api_key = os.environ.get("GOOGLE_API_KEY")
    google_cse_id = os.environ.get("GOOGLE_CSE_ID")
    lookback_hours = int(os.environ.get("NEWS_LOOKBACK_HOURS", "24"))

    reports: list[AccountReport] = []
    with httpx.Client(timeout=15.0) as http:
        for account in accounts:
            try:
                hits = search_account(
                    account.name,
                    google_api_key=google_api_key,
                    google_cse_id=google_cse_id,
                    lookback_hours=lookback_hours,
                    http=http,
                )
            except Exception as exc:
                logger.exception("News search failed for %s: %s", account.name, exc)
                hits = []
            classified = classify_all(hits)
            if classified:
                logger.info("%s: %d relevant hits", account.name, len(classified))
            reports.append(AccountReport(account=account, hits=classified))

    subject, html_body, text_body = _compose_email(
        reports=reports,
        instance_url=sf.instance_url,
        ae_email=ae_email,
    )

    if dry_run:
        print(subject)
        print()
        print(text_body)
        return 0

    gmail = GmailClient(
        credentials_path=Path(os.environ.get("GMAIL_CREDENTIALS_PATH", "credentials.json")),
        token_path=Path(os.environ.get("GMAIL_TOKEN_PATH", "token.json")),
    )
    _send_html(
        gmail,
        from_addr=_required("GMAIL_SENDER"),
        to_addr=digest_to,
        subject=subject,
        html_body=html_body,
        text_body=text_body,
    )
    logger.info("Sent digest to %s", digest_to)
    return 0


def _compose_email(
    *,
    reports: list[AccountReport],
    instance_url: str,
    ae_email: str,
) -> tuple[str, str, str]:
    today = datetime.now().strftime("%a %b %d, %Y")
    accounts_with_news = [r for r in reports if r.hits]
    total_hits = sum(len(r.hits) for r in accounts_with_news)

    subject = (
        f"[Account News] {today} — {total_hits} item(s) across "
        f"{len(accounts_with_news)} account(s)"
    )

    html_parts = [
        f"<p>Daily M&amp;A / Funding / IPO digest for <b>{escape(ae_email)}</b> — {escape(today)}</p>",
        f"<p>{len(reports)} accounts scanned, {len(accounts_with_news)} with news.</p>",
    ]
    text_parts = [
        f"Daily M&A / Funding / IPO digest for {ae_email} - {today}",
        f"{len(reports)} accounts scanned, {len(accounts_with_news)} with news.",
        "",
    ]

    if not accounts_with_news:
        html_parts.append("<p><i>No qualifying news found in the last 24 hours.</i></p>")
        text_parts.append("No qualifying news found in the last 24 hours.")
    else:
        for report in accounts_with_news:
            sf_url = report.account.record_url(instance_url)
            html_parts.append(
                f'<h3><a href="{escape(sf_url)}">{escape(report.account.name)}</a></h3>'
            )
            text_parts.append(f"== {report.account.name} ==")
            text_parts.append(f"Salesforce: {sf_url}")

            html_parts.append("<ul>")
            for c in report.hits:
                tags = ", ".join(cat.value for cat in c.categories)
                published = (
                    c.hit.published_at.strftime("%Y-%m-%d %H:%M")
                    if c.hit.published_at
                    else "n/a"
                )
                source = c.hit.source or "source"
                html_parts.append(
                    "<li>"
                    f"<b>[{escape(tags)}]</b> "
                    f'<a href="{escape(c.hit.url)}">{escape(c.hit.title)}</a> '
                    f"<span style=\"color:#666\">— {escape(source)} · {escape(published)}</span>"
                    "</li>"
                )
                text_parts.append(f"  [{tags}] {c.hit.title}")
                text_parts.append(f"    {source} · {published}")
                text_parts.append(f"    {c.hit.url}")
            html_parts.append("</ul>")
            text_parts.append("")

    html_body = "<html><body>" + "\n".join(html_parts) + "</body></html>"
    text_body = "\n".join(text_parts)
    return subject, html_body, text_body


def _send_html(
    gmail: GmailClient,
    *,
    from_addr: str,
    to_addr: str,
    subject: str,
    html_body: str,
    text_body: str,
) -> str:
    import base64
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    sent = (
        gmail._service.users()  # noqa: SLF001 - reuse existing client
        .messages()
        .send(userId="me", body={"raw": raw})
        .execute()
    )
    return sent["id"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Daily account-news digest")
    parser.add_argument("--dry-run", action="store_true", help="Print email instead of sending")
    args = parser.parse_args()
    return run(dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())

"""Daily account-news digest: per-AE CSV -> news search -> Gmail.

Reads one CSV per AE from accounts/<ae_email>.csv, searches the web for
M&A / funding / IPO news for each account in the last 24h, and emails
each AE their digest. If an AE has no qualifying news, sends a short
"all up to date" note instead.
"""

from __future__ import annotations

import argparse
import base64
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from html import escape
from pathlib import Path

import httpx
from dotenv import load_dotenv

from csv_accounts import AeCsv, CsvAccount, load_aes, parse_tier_filter
from gmail_client import GmailClient
from news_classifier import ClassifiedHit, classify_all
from news_search import search_account

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AccountReport:
    account: CsvAccount
    hits: list[ClassifiedHit]


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _has_account_csvs(accounts_dir: Path) -> bool:
    return accounts_dir.exists() and any(accounts_dir.glob("*.csv"))


def _prompt_upload_csv(accounts_dir: Path) -> None:
    accounts_dir.mkdir(exist_ok=True)
    print()
    print("=" * 70)
    print("No account CSV files found in 'accounts/'.")
    print()
    print("To get news for an AE, you need to add a CSV file:")
    print(f"  1. Drag-drop the AE's account export into the '{accounts_dir}/' folder")
    print("     (left-side file explorer in your codespace)")
    print("  2. Rename it to <ae_name>.csv (e.g. lauren_tabler.csv)")
    print("  3. The file just needs a header row with 'Account Name' column.")
    print("     6sense exports work as-is.")
    print("=" * 70)
    print()
    while True:
        answer = input("Press Enter once the CSV is uploaded (or type 'q' to quit): ").strip().lower()
        if answer == "q":
            sys.exit(0)
        if _has_account_csvs(accounts_dir):
            print(f"OK: found {len(list(accounts_dir.glob('*.csv')))} CSV file(s).")
            print()
            return
        print(f"Still no CSV files in '{accounts_dir}/'. Try again.")


def _prompt_email() -> str:
    print()
    print("=" * 70)
    print("Enter the Gmail/Vanta address you authorized in gmail_oauth_setup.py.")
    print("This is also the address that will receive the daily digest.")
    print("=" * 70)
    while True:
        email = input("Your Vanta email: ").strip()
        if "@" in email and "." in email.split("@", 1)[1]:
            return email
        print("That doesn't look like an email address. Try again.")


def run(*, dry_run: bool = False, limit: int | None = None,
        include_names: list[str] | None = None,
        lookback_hours_override: int | None = None) -> int:
    load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    interactive = sys.stdin.isatty() and sys.stdout.isatty()

    accounts_dir = Path(os.environ.get("ACCOUNTS_DIR", "accounts"))
    if not _has_account_csvs(accounts_dir):
        if interactive:
            _prompt_upload_csv(accounts_dir)
        else:
            logger.error("No AE CSV files found in %s", accounts_dir)
            return 1

    tier_raw = os.environ.get("TIER_FILTER", "1,2")
    tier_filter = parse_tier_filter(tier_raw)
    aes = load_aes(accounts_dir, tier_filter=tier_filter)
    if not aes or not any(ae.accounts for ae in aes):
        logger.error("No accounts loaded from %s", accounts_dir)
        return 1

    google_api_key = os.environ.get("GOOGLE_API_KEY")
    google_cse_id = os.environ.get("GOOGLE_CSE_ID")
    lookback_hours = lookback_hours_override or int(
        os.environ.get("NEWS_LOOKBACK_HOURS", "24")
    )

    if include_names:
        wanted = {n.strip().lower() for n in include_names if n.strip()}
        for ae in aes:
            ae.accounts = [
                a for a in ae.accounts
                if any(w in a.name.lower() or a.name.lower() in w for w in wanted)
            ]
    if limit:
        for ae in aes:
            ae.accounts = ae.accounts[:limit]

    gmail_sender = os.environ.get("GMAIL_SENDER")
    if not gmail_sender:
        if interactive:
            gmail_sender = _prompt_email()
        else:
            raise RuntimeError("Missing required environment variable: GMAIL_SENDER")
    digest_to = os.environ.get("DIGEST_TO") or gmail_sender

    gmail: GmailClient | None = None
    if not dry_run:
        gmail = GmailClient(
            credentials_path=Path(
                os.environ.get("GMAIL_CREDENTIALS_PATH", "credentials.json")
            ),
            token_path=Path(os.environ.get("GMAIL_TOKEN_PATH", "token.json")),
        )

    with httpx.Client(timeout=15.0) as http:
        for ae in aes:
            logger.info(
                "Processing AE %s: %d accounts",
                ae.display_name, len(ae.accounts),
            )
            reports = _gather_reports(ae, http, google_api_key, google_cse_id, lookback_hours)
            subject, html_body, text_body = _compose_email(ae, reports)

            if dry_run:
                print("=" * 60)
                print(f"To: {digest_to}")
                print(f"Subject: {subject}")
                print()
                print(text_body)
                print()
                continue

            assert gmail is not None
            _send_html(
                gmail,
                from_addr=gmail_sender,
                to_addr=digest_to,
                subject=subject,
                html_body=html_body,
                text_body=text_body,
            )
            logger.info("Sent %s digest to %s", ae.display_name, digest_to)

    return 0


def _gather_reports(
    ae: AeCsv,
    http: httpx.Client,
    google_api_key: str | None,
    google_cse_id: str | None,
    lookback_hours: int,
) -> list[AccountReport]:
    reports: list[AccountReport] = []
    for account in ae.accounts:
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
        relevant = [h for h in hits if _is_about_account(h, account.name)]
        if hits and len(relevant) < len(hits):
            logger.debug(
                "%s: filtered %d/%d hits by name match",
                account.name, len(hits) - len(relevant), len(hits),
            )
        reports.append(AccountReport(account=account, hits=classify_all(relevant)))
    return reports


def _is_about_account(hit, account_name: str) -> bool:
    """Require the account name (or a strong variant) to appear in the title.

    Cuts false positives like 'Anaconda' (city in MT), 'Twins' (baseball
    team), and 'Acoustic' (adjective) where the search engine returned
    unrelated articles that happened to mention M&A/funding/IPO.
    """
    name = account_name.strip().lower()
    title = (hit.title or "").lower()
    snippet = (hit.snippet or "").lower()

    # Strict: full account name in title.
    if name in title:
        return True
    # Lenient fallback: full name in the first 200 chars of snippet
    # (covers cases where the title got truncated by the source).
    if name in snippet[:200]:
        return True

    # Strip common corporate suffixes and retry once: "ANI Pharmaceuticals, Inc."
    # should still match "ANI Pharmaceuticals" in a headline.
    stripped = name
    for suffix in [", inc.", ", inc", " inc.", " inc", " corp", " corporation",
                   " llc", " ltd", " limited", " plc", " ag", " gmbh", " co."]:
        if stripped.endswith(suffix):
            stripped = stripped[: -len(suffix)].strip()
            break
    if stripped != name and len(stripped) >= 4:
        if stripped in title or stripped in snippet[:200]:
            return True

    return False


def _compose_email(
    ae: AeCsv, reports: list[AccountReport]
) -> tuple[str, str, str]:
    today = datetime.now().strftime("%a %b %d, %Y")
    accounts_with_news = [r for r in reports if r.hits]
    total_hits = sum(len(r.hits) for r in accounts_with_news)

    if not accounts_with_news:
        subject = f"[{ae.display_name}] {today} — All up to date"
        html_body = (
            "<html><body>"
            f"<p>Daily digest for <b>{escape(ae.display_name)}</b> — {escape(today)}</p>"
            f"<p>Scanned <b>{len(reports)}</b> account(s). "
            "No M&amp;A, funding, or IPO news in the last 24 hours.</p>"
            "<p>All up to date.</p>"
            "</body></html>"
        )
        text_body = (
            f"Daily digest for {ae.display_name} - {today}\n\n"
            f"Scanned {len(reports)} account(s). "
            "No M&A, funding, or IPO news in the last 24 hours.\n\n"
            "All up to date."
        )
        return subject, html_body, text_body

    subject = (
        f"[{ae.display_name}] {today} — {total_hits} item(s) across "
        f"{len(accounts_with_news)} account(s)"
    )

    html_parts = [
        f"<p>Daily digest for <b>{escape(ae.display_name)}</b> — {escape(today)}</p>",
        f"<p>{len(reports)} accounts scanned, "
        f"<b>{len(accounts_with_news)}</b> with news today.</p>",
    ]
    text_parts = [
        f"Daily digest for {ae.display_name} - {today}",
        "",
        f"{len(reports)} accounts scanned, "
        f"{len(accounts_with_news)} with news today.",
        "",
    ]

    for report in accounts_with_news:
        if report.account.salesforce_url:
            heading = (
                f'<h3><a href="{escape(report.account.salesforce_url)}">'
                f"{escape(report.account.name)}</a></h3>"
            )
        else:
            heading = f"<h3>{escape(report.account.name)}</h3>"
        html_parts.append(heading)
        text_parts.append(f"== {report.account.name} ==")
        if report.account.salesforce_url:
            text_parts.append(f"Salesforce: {report.account.salesforce_url}")

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
                f'<span style="color:#666">— {escape(source)} · {escape(published)}</span>'
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
    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    sent = (
        gmail._service.users()  # noqa: SLF001
        .messages()
        .send(userId="me", body={"raw": raw})
        .execute()
    )
    return sent["id"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Daily account-news digest")
    parser.add_argument(
        "--dry-run", action="store_true", help="Print emails instead of sending"
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Process only the first N accounts (testing)"
    )
    parser.add_argument(
        "--include", default=None,
        help="Comma-separated account name substrings to include (testing)"
    )
    parser.add_argument(
        "--lookback-hours", type=int, default=None,
        help="Override news lookback window in hours (default 24)"
    )
    args = parser.parse_args()
    include = [s for s in (args.include or "").split(",") if s.strip()] or None
    return run(
        dry_run=args.dry_run,
        limit=args.limit,
        include_names=include,
        lookback_hours_override=args.lookback_hours,
    )


if __name__ == "__main__":
    sys.exit(main())

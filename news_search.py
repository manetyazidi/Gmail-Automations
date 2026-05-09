"""News search: Google Custom Search API + Google News RSS, deduped."""

from __future__ import annotations

import logging
import math
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus, urlparse

import httpx

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
RSS_DELAY_SECONDS = 0.5

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NewsHit:
    title: str
    url: str
    source: str
    snippet: str
    published_at: datetime | None

    def canonical_url(self) -> str:
        parsed = urlparse(self.url)
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")


# Single combined query — fewer requests = less rate-limiting.
KEYWORDS = (
    'acquires OR acquired OR "to acquire" OR merger OR "merges with" OR '
    '"Series A" OR "Series B" OR "Series C" OR "Series D" OR raises OR raised OR '
    '"funding round" OR "secures funding" OR IPO OR "initial public offering" OR '
    '"goes public" OR "files to go public"'
)


def search_account(
    account_name: str,
    *,
    google_api_key: str | None,
    google_cse_id: str | None,
    lookback_hours: int = 24,
    http: httpx.Client | None = None,
) -> list[NewsHit]:
    owns_http = http is None
    client = http or httpx.Client(timeout=15.0, headers={"User-Agent": USER_AGENT})
    try:
        query = f'"{account_name}" ({KEYWORDS})'
        hits: list[NewsHit] = []
        if google_api_key and google_cse_id:
            hits.extend(
                _google_cse(client, query, google_api_key, google_cse_id, lookback_hours)
            )
        hits.extend(_google_news_rss(client, query, lookback_hours))
        return _dedupe_recent(hits, lookback_hours=lookback_hours)
    finally:
        if owns_http:
            client.close()


def _google_cse(
    http: httpx.Client, query: str, api_key: str, cse_id: str, lookback_hours: int
) -> list[NewsHit]:
    days = max(1, math.ceil(lookback_hours / 24))
    try:
        response = http.get(
            "https://www.googleapis.com/customsearch/v1",
            params={
                "key": api_key,
                "cx": cse_id,
                "q": query,
                "num": 10,
                "dateRestrict": f"d{days}",
                "sort": "date",
            },
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("Google CSE failed for %r: %s", query, exc)
        return []

    items = response.json().get("items", []) or []
    out: list[NewsHit] = []
    for item in items:
        published = _extract_cse_date(item)
        out.append(
            NewsHit(
                title=item.get("title", ""),
                url=item.get("link", ""),
                source=item.get("displayLink", ""),
                snippet=item.get("snippet", ""),
                published_at=published,
            )
        )
    return out


def _extract_cse_date(item: dict) -> datetime | None:
    pagemap = item.get("pagemap") or {}
    metatags = (pagemap.get("metatags") or [{}])[0]
    for key in ("article:published_time", "og:updated_time", "date"):
        value = metatags.get(key)
        if not value:
            continue
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            continue
    return None


def _google_news_rss(
    http: httpx.Client, query: str, lookback_hours: int
) -> list[NewsHit]:
    days = max(1, math.ceil(lookback_hours / 24))
    url = (
        "https://news.google.com/rss/search?"
        f"q={quote_plus(query)}+when:{days}d&hl=en-US&gl=US&ceid=US:en"
    )
    time.sleep(RSS_DELAY_SECONDS)
    try:
        response = http.get(
            url, headers={"User-Agent": USER_AGENT}, follow_redirects=True
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("Google News RSS failed for %r: %s", query, exc)
        return []

    out: list[NewsHit] = []
    try:
        root = ET.fromstring(response.text)
    except ET.ParseError as exc:
        logger.warning("RSS parse failed: %s", exc)
        return []

    for item in root.findall("./channel/item"):
        link = (item.findtext("link") or "").strip()
        if not link:
            continue
        pub = item.findtext("pubDate")
        published: datetime | None = None
        if pub:
            try:
                published = parsedate_to_datetime(pub)
            except (TypeError, ValueError):
                published = None
        source_el = item.find("source")
        source = (source_el.text or "").strip() if source_el is not None else ""
        out.append(
            NewsHit(
                title=(item.findtext("title") or "").strip(),
                url=link,
                source=source,
                snippet=(item.findtext("description") or "").strip(),
                published_at=published,
            )
        )
    return out


def _dedupe_recent(hits: list[NewsHit], *, lookback_hours: int) -> list[NewsHit]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    seen: set[str] = set()
    out: list[NewsHit] = []
    for hit in hits:
        if hit.published_at:
            published = hit.published_at
            if published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
            if published < cutoff:
                continue
        key = hit.canonical_url()
        if key in seen:
            continue
        seen.add(key)
        out.append(hit)
    out.sort(key=lambda h: h.published_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return out

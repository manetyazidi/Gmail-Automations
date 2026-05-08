"""News search: Google Custom Search API + Google News RSS, deduped."""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus, urlparse

import httpx

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


# Search query templates targeting M&A, funding, and IPO coverage.
QUERY_TEMPLATES = [
    '"{name}" (acquires OR acquired OR "to acquire" OR merger OR "merges with")',
    '"{name}" ("Series A" OR "Series B" OR "Series C" OR "Series D" OR "raises" OR "raised" OR "funding round" OR "secures funding")',
    '"{name}" (IPO OR "initial public offering" OR "goes public" OR "files to go public")',
]


def search_account(
    account_name: str,
    *,
    google_api_key: str | None,
    google_cse_id: str | None,
    lookback_hours: int = 24,
    http: httpx.Client | None = None,
) -> list[NewsHit]:
    owns_http = http is None
    client = http or httpx.Client(timeout=15.0)
    try:
        hits: list[NewsHit] = []
        for template in QUERY_TEMPLATES:
            query = template.format(name=account_name)
            if google_api_key and google_cse_id:
                hits.extend(
                    _google_cse(client, query, google_api_key, google_cse_id)
                )
            hits.extend(_google_news_rss(client, query))
        return _dedupe_recent(hits, lookback_hours=lookback_hours)
    finally:
        if owns_http:
            client.close()


def _google_cse(
    http: httpx.Client, query: str, api_key: str, cse_id: str
) -> list[NewsHit]:
    try:
        response = http.get(
            "https://www.googleapis.com/customsearch/v1",
            params={
                "key": api_key,
                "cx": cse_id,
                "q": query,
                "num": 5,
                "dateRestrict": "d2",
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


def _google_news_rss(http: httpx.Client, query: str) -> list[NewsHit]:
    url = (
        "https://news.google.com/rss/search?"
        f"q={quote_plus(query)}+when:2d&hl=en-US&gl=US&ceid=US:en"
    )
    try:
        response = http.get(url)
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

"""News search across Google Custom Search API and Google News RSS.

Both sources are queried per account. Results are merged and deduped by URL.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus

import httpx

logger = logging.getLogger(__name__)

GOOGLE_CSE_ENDPOINT = "https://www.googleapis.com/customsearch/v1"
GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"


@dataclass(frozen=True)
class NewsItem:
    title: str
    url: str
    snippet: str
    source: str
    published: datetime | None


def search_news(
    *,
    account_name: str,
    google_api_key: str | None,
    google_cse_id: str | None,
    lookback_days: int,
    query_terms: str,
) -> list[NewsItem]:
    """Return deduped news items for an account name within the lookback window."""
    items: list[NewsItem] = []
    if google_api_key and google_cse_id:
        try:
            items.extend(
                _search_google_cse(
                    account_name=account_name,
                    api_key=google_api_key,
                    cse_id=google_cse_id,
                    query_terms=query_terms,
                    lookback_days=lookback_days,
                )
            )
        except Exception as exc:
            logger.warning("Google CSE failed for %s: %s", account_name, exc)
    try:
        items.extend(
            _search_google_news_rss(
                account_name=account_name,
                query_terms=query_terms,
                lookback_days=lookback_days,
            )
        )
    except Exception as exc:
        logger.warning("Google News RSS failed for %s: %s", account_name, exc)

    return _dedupe(items)


def _search_google_cse(
    *,
    account_name: str,
    api_key: str,
    cse_id: str,
    query_terms: str,
    lookback_days: int,
) -> list[NewsItem]:
    query = f'"{account_name}" {query_terms}'
    params = {
        "key": api_key,
        "cx": cse_id,
        "q": query,
        "num": 10,
        "dateRestrict": f"d{lookback_days}",
        "sort": "date",
    }
    resp = httpx.get(GOOGLE_CSE_ENDPOINT, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    items: list[NewsItem] = []
    for r in data.get("items", []):
        items.append(
            NewsItem(
                title=r.get("title", ""),
                url=r.get("link", ""),
                snippet=r.get("snippet", ""),
                source=_domain_of(r.get("displayLink", "")),
                published=_parse_cse_date(r),
            )
        )
    return items


def _search_google_news_rss(
    *,
    account_name: str,
    query_terms: str,
    lookback_days: int,
) -> list[NewsItem]:
    query = f'"{account_name}" {query_terms} when:{lookback_days}d'
    url = (
        f"{GOOGLE_NEWS_RSS}?q={quote_plus(query)}"
        "&hl=en-US&gl=US&ceid=US:en"
    )
    resp = httpx.get(url, timeout=30, follow_redirects=True)
    resp.raise_for_status()
    root = ET.fromstring(resp.text)
    items: list[NewsItem] = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        description = (item.findtext("description") or "").strip()
        pub = item.findtext("pubDate")
        source_el = item.find("source")
        source = source_el.text.strip() if source_el is not None and source_el.text else ""
        items.append(
            NewsItem(
                title=title,
                url=link,
                snippet=_strip_html(description),
                source=source or _domain_of(link),
                published=_parse_rfc_date(pub),
            )
        )
    return items


def _dedupe(items: list[NewsItem]) -> list[NewsItem]:
    seen: set[str] = set()
    out: list[NewsItem] = []
    for it in items:
        key = _normalize_url(it.url)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(it)
    out.sort(key=lambda i: i.published or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return out


def _normalize_url(url: str) -> str:
    return re.sub(r"[?#].*$", "", url.strip().lower())


def _domain_of(url_or_host: str) -> str:
    s = url_or_host.replace("https://", "").replace("http://", "")
    return s.split("/")[0]


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text).strip()


def _parse_rfc_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None


def _parse_cse_date(record: dict) -> datetime | None:
    metatags = (record.get("pagemap") or {}).get("metatags") or []
    for tag in metatags:
        for key in ("article:published_time", "datePublished", "og:updated_time"):
            v = tag.get(key)
            if v:
                try:
                    return datetime.fromisoformat(v.replace("Z", "+00:00"))
                except ValueError:
                    continue
    return None

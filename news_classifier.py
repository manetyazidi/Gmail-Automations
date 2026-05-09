"""Classify news items into M&A, Funding, or IPO buckets via keyword matching."""

from __future__ import annotations

import re
from dataclasses import dataclass

from news_search import NewsItem

CATEGORY_MA = "M&A"
CATEGORY_FUNDING = "Funding"
CATEGORY_IPO = "IPO"

# Word-boundary keyword patterns. Order matters for precedence in classify().
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        CATEGORY_MA,
        re.compile(
            r"\b(acquir(?:e|es|ed|ing|ition)|merger|merg(?:e|es|ed|ing)|"
            r"buyout|takeover|bought\s+by|acquired\s+by|to\s+acquire|"
            r"agree[ds]?\s+to\s+acquire|sell[s]?\s+to|sold\s+to|"
            r"divest(?:iture|ed|ing)?)\b",
            re.IGNORECASE,
        ),
    ),
    (
        CATEGORY_IPO,
        re.compile(
            r"\b(ipo|initial\s+public\s+offering|going\s+public|"
            r"public\s+listing|files?\s+to\s+go\s+public|"
            r"direct\s+listing|spac\s+merger)\b",
            re.IGNORECASE,
        ),
    ),
    (
        CATEGORY_FUNDING,
        re.compile(
            r"\b(series\s+[a-k]\b|seed\s+round|pre[-\s]?seed|"
            r"raises?\s+\$?\d|raised\s+\$?\d|funding\s+round|"
            r"venture\s+round|growth\s+round|new\s+fund|launches?\s+fund|"
            r"closes?\s+\$?\d+\s*(million|billion|m\b|b\b)|"
            r"valuation\s+of\s+\$|valued\s+at\s+\$)\b",
            re.IGNORECASE,
        ),
    ),
]


@dataclass(frozen=True)
class ClassifiedNews:
    item: NewsItem
    category: str


def classify(item: NewsItem) -> str | None:
    """Return the matched category or None if the item is not relevant."""
    haystack = f"{item.title}\n{item.snippet}"
    for category, pattern in _PATTERNS:
        if pattern.search(haystack):
            return category
    return None


def filter_relevant(items: list[NewsItem]) -> list[ClassifiedNews]:
    out: list[ClassifiedNews] = []
    for it in items:
        cat = classify(it)
        if cat:
            out.append(ClassifiedNews(item=it, category=cat))
    return out


def relevant_query_terms() -> str:
    """Search query fragment biased toward the categories we care about."""
    return (
        "(acquisition OR merger OR \"acquired by\" OR buyout "
        "OR \"raises\" OR \"funding round\" OR \"Series A\" OR \"Series B\" "
        "OR \"Series C\" OR \"new fund\" OR IPO OR \"going public\")"
    )

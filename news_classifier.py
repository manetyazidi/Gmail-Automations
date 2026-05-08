"""Classify news hits into M&A, funding, or IPO categories via keyword match."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from news_search import NewsHit


class Category(str, Enum):
    MA = "M&A"
    FUNDING = "Funding"
    IPO = "IPO"


_PATTERNS: dict[Category, list[re.Pattern[str]]] = {
    Category.MA: [
        re.compile(r"\b(acquires?|acquired|acquiring|acquisition)\b", re.I),
        re.compile(r"\b(merger|merges? with|to merge)\b", re.I),
        re.compile(r"\b(buys|bought|to buy|takeover)\b", re.I),
    ],
    Category.FUNDING: [
        re.compile(r"\bSeries\s+[A-J]\b", re.I),
        re.compile(r"\b(seed|pre-seed|growth)\s+(round|funding)\b", re.I),
        re.compile(r"\b(raises|raised|secures|secured|closes)\s+\$?\d", re.I),
        re.compile(r"\b(funding round|new round|fresh capital|valuation)\b", re.I),
    ],
    Category.IPO: [
        re.compile(r"\bIPO\b"),
        re.compile(r"\binitial public offering\b", re.I),
        re.compile(r"\b(goes public|going public|files to go public|public listing)\b", re.I),
        re.compile(r"\b(direct listing|SPAC merger)\b", re.I),
    ],
}


@dataclass(frozen=True)
class ClassifiedHit:
    hit: NewsHit
    categories: tuple[Category, ...]


def classify(hit: NewsHit) -> ClassifiedHit | None:
    text = f"{hit.title} {hit.snippet}"
    matched: list[Category] = []
    for category, patterns in _PATTERNS.items():
        if any(p.search(text) for p in patterns):
            matched.append(category)
    if not matched:
        return None
    return ClassifiedHit(hit=hit, categories=tuple(matched))


def classify_all(hits: list[NewsHit]) -> list[ClassifiedHit]:
    return [c for c in (classify(h) for h in hits) if c is not None]

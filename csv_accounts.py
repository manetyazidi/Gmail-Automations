"""Load accounts from CSV files. One CSV per AE (Account Owner).

File layout:
    accounts/<ae_name>.csv  (filename stem becomes the AE display name)

Each CSV must have a header row. Recognized columns (case-insensitive):
    - "Account Name" (required)
    - "Salesforce URL" / "Salesforce Link" (optional)
    - "Website" (optional)
    - "Account ICP Tier" / "Tier" (optional, used by TIER_FILTER env var)

Optional metadata lines anywhere above the header:
    # ae_name=Lauren Tabler

Encoding is auto-detected: UTF-8 (with BOM) -> Windows-1252 -> Latin-1.
Duplicate Account Names are removed (case-insensitive, first wins).
"""

from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CsvAccount:
    name: str
    salesforce_url: str | None
    website: str | None
    tier: str | None


@dataclass
class AeCsv:
    display_name: str
    accounts: list[CsvAccount] = field(default_factory=list)


def load_aes(
    accounts_dir: Path,
    *,
    tier_filter: set[str] | None = None,
) -> list[AeCsv]:
    if not accounts_dir.exists():
        logger.warning("Accounts dir %s does not exist", accounts_dir)
        return []

    aes: list[AeCsv] = []
    for path in sorted(accounts_dir.glob("*.csv")):
        ae = _load_one(path, tier_filter=tier_filter)
        if ae:
            aes.append(ae)
    return aes


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def _load_one(path: Path, *, tier_filter: set[str] | None) -> AeCsv | None:
    display_name = _humanize(path.stem)
    text = _read_text(path)

    data_lines: list[str] = []
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("# ae_name="):
            display_name = stripped.split("=", 1)[1].strip()
            continue
        if stripped.startswith("#") or not stripped:
            continue
        data_lines.append(line)

    if not data_lines:
        logger.warning("%s has no data rows", path.name)
        return AeCsv(display_name=display_name, accounts=[])

    reader = csv.DictReader(io.StringIO("".join(data_lines)))
    if not reader.fieldnames:
        return AeCsv(display_name=display_name, accounts=[])

    field_map = {(name or "").strip().lower(): name for name in reader.fieldnames}
    name_key = field_map.get("account name") or field_map.get("name")
    sf_key = field_map.get("salesforce url") or field_map.get("salesforce link")
    web_key = field_map.get("website")
    tier_key = (
        field_map.get("account icp tier")
        or field_map.get("tier")
        or field_map.get("icp tier")
    )

    if not name_key:
        logger.error("%s missing required 'Account Name' column", path.name)
        return AeCsv(display_name=display_name, accounts=[])

    accounts: list[CsvAccount] = []
    seen: set[str] = set()
    for row in reader:
        name = (row.get(name_key) or "").strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue

        tier = (row.get(tier_key) or "").strip() if tier_key else None
        if tier_filter is not None:
            normalized = (tier or "").lower().replace(" ", "")
            if normalized not in tier_filter:
                continue

        seen.add(key)
        accounts.append(
            CsvAccount(
                name=name,
                salesforce_url=(row.get(sf_key) or "").strip() if sf_key else None,
                website=(row.get(web_key) or "").strip() if web_key else None,
                tier=tier or None,
            )
        )

    logger.info(
        "%s: %d account(s) for AE %s%s",
        path.name,
        len(accounts),
        display_name,
        f" (tier filter: {sorted(tier_filter)})" if tier_filter else "",
    )
    return AeCsv(display_name=display_name, accounts=accounts)


def _humanize(stem: str) -> str:
    return stem.replace("_", " ").replace("-", " ").strip().title()


def parse_tier_filter(raw: str | None) -> set[str] | None:
    if not raw:
        return None
    out: set[str] = set()
    for piece in raw.split(","):
        normalized = piece.strip().lower().replace(" ", "")
        if not normalized:
            continue
        if normalized.isdigit():
            normalized = f"tier{normalized}"
        out.add(normalized)
    return out or None

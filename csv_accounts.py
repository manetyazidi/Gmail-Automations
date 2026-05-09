"""Load accounts from CSV files, one CSV per AE.

File layout:
    accounts/<ae_email>.csv

Each CSV must have a header row. Recognized columns (case-insensitive):
    - "Account Name" (required)
    - "Salesforce URL" (optional)
    - "Website" (optional)

Optionally, the first non-comment line may be a metadata row of the form:
    # ae_name=Sarah Smith
to set the AE's display name. Otherwise the filename stem is used.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CsvAccount:
    name: str
    salesforce_url: str | None
    website: str | None

    def record_url(self, _instance_url: str = "") -> str:
        return self.salesforce_url or ""


@dataclass
class AeCsv:
    email: str
    display_name: str
    accounts: list[CsvAccount] = field(default_factory=list)


def load_aes(accounts_dir: Path) -> list[AeCsv]:
    if not accounts_dir.exists():
        logger.warning("Accounts dir %s does not exist", accounts_dir)
        return []

    aes: list[AeCsv] = []
    for path in sorted(accounts_dir.glob("*.csv")):
        ae = _load_one(path)
        if ae:
            aes.append(ae)
    return aes


def _load_one(path: Path) -> AeCsv | None:
    email = path.stem.strip().lower()
    if "@" not in email:
        logger.warning("Skipping %s: filename must be <email>.csv", path.name)
        return None

    display_name = email.split("@", 1)[0].replace(".", " ").title()
    accounts: list[CsvAccount] = []

    with path.open(newline="", encoding="utf-8-sig") as fh:
        lines = fh.readlines()

    data_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("# ae_name="):
            display_name = stripped.split("=", 1)[1].strip()
            continue
        if stripped.startswith("#") or not stripped:
            continue
        data_lines.append(line)

    if not data_lines:
        logger.warning("%s has no data rows", path.name)
        return AeCsv(email=email, display_name=display_name, accounts=[])

    reader = csv.DictReader(data_lines)
    if not reader.fieldnames:
        return AeCsv(email=email, display_name=display_name, accounts=[])

    field_map = {(name or "").strip().lower(): name for name in reader.fieldnames}
    name_key = field_map.get("account name") or field_map.get("name")
    sf_key = field_map.get("salesforce url") or field_map.get("salesforce link")
    web_key = field_map.get("website")

    if not name_key:
        logger.error("%s missing required 'Account Name' column", path.name)
        return AeCsv(email=email, display_name=display_name, accounts=[])

    for row in reader:
        name = (row.get(name_key) or "").strip()
        if not name:
            continue
        accounts.append(
            CsvAccount(
                name=name,
                salesforce_url=(row.get(sf_key) or "").strip() if sf_key else None,
                website=(row.get(web_key) or "").strip() if web_key else None,
            )
        )

    return AeCsv(email=email, display_name=display_name, accounts=accounts)

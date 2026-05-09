"""Salesforce client for fetching accounts owned by a given AE.

Uses OAuth 2.0 username-password flow against the Salesforce REST API.
Returns Account records (Id, Name, Website) where Owner.Name matches the AE.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SalesforceAccount:
    id: str
    name: str
    website: str | None
    owner_name: str

    @property
    def record_url(self) -> str:
        return f"{{instance}}/lightning/r/Account/{self.id}/view"


class SalesforceClient:
    def __init__(
        self,
        *,
        login_url: str,
        client_id: str,
        client_secret: str,
        username: str,
        password: str,
        security_token: str,
    ) -> None:
        self._login_url = login_url.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self._username = username
        self._password = password + security_token
        self._access_token: str | None = None
        self._instance_url: str | None = None

    def _login(self) -> None:
        body = {
            "grant_type": "password",
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "username": self._username,
            "password": self._password,
        }
        resp = httpx.post(
            f"{self._login_url}/services/oauth2/token",
            data=body,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        self._access_token = data["access_token"]
        self._instance_url = data["instance_url"].rstrip("/")

    def _ensure_session(self) -> None:
        if not self._access_token or not self._instance_url:
            self._login()

    def query_accounts_by_owner(self, owner_name: str) -> list[SalesforceAccount]:
        self._ensure_session()
        soql = (
            "SELECT Id, Name, Website, Owner.Name "
            "FROM Account "
            f"WHERE Owner.Name = '{_escape_soql(owner_name)}' "
            "ORDER BY Name"
        )
        url = (
            f"{self._instance_url}/services/data/v60.0/query/?"
            + urlencode({"q": soql})
        )
        records: list[SalesforceAccount] = []
        while url:
            resp = httpx.get(
                url,
                headers={"Authorization": f"Bearer {self._access_token}"},
                timeout=30,
            )
            resp.raise_for_status()
            payload = resp.json()
            for r in payload.get("records", []):
                records.append(
                    SalesforceAccount(
                        id=r["Id"],
                        name=r["Name"],
                        website=r.get("Website"),
                        owner_name=(r.get("Owner") or {}).get("Name", owner_name),
                    )
                )
            next_path = payload.get("nextRecordsUrl")
            url = f"{self._instance_url}{next_path}" if next_path else None
        logger.info("Fetched %d accounts for owner %s", len(records), owner_name)
        return records

    @property
    def instance_url(self) -> str:
        self._ensure_session()
        assert self._instance_url
        return self._instance_url


def _escape_soql(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")

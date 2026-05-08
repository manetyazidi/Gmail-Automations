"""Salesforce client: list Accounts owned by a given AE."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SalesforceAccount:
    id: str
    name: str
    website: str | None
    industry: str | None
    owner_name: str

    def record_url(self, instance_url: str) -> str:
        return f"{instance_url.rstrip('/')}/lightning/r/Account/{self.id}/view"


class SalesforceClient:
    """Minimal Salesforce REST client using OAuth refresh-token flow."""

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        login_url: str = "https://login.salesforce.com",
        api_version: str = "v60.0",
        timeout: float = 20.0,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self._login_url = login_url.rstrip("/")
        self._api_version = api_version
        self._http = httpx.Client(timeout=timeout)
        self._access_token: str | None = None
        self._instance_url: str | None = None

    @property
    def instance_url(self) -> str:
        if not self._instance_url:
            self._refresh()
        assert self._instance_url is not None
        return self._instance_url

    def accounts_owned_by(self, ae_email: str) -> list[SalesforceAccount]:
        soql = (
            "SELECT Id, Name, Website, Industry, Owner.Name "
            "FROM Account "
            f"WHERE Owner.Email = '{_escape_soql(ae_email)}' "
            "ORDER BY Name"
        )
        records = self._query(soql)
        return [
            SalesforceAccount(
                id=r["Id"],
                name=r["Name"],
                website=r.get("Website"),
                industry=r.get("Industry"),
                owner_name=(r.get("Owner") or {}).get("Name", ""),
            )
            for r in records
        ]

    def _query(self, soql: str) -> list[dict]:
        if not self._access_token:
            self._refresh()
        url = (
            f"{self._instance_url}/services/data/{self._api_version}/query"
            f"?q={quote(soql)}"
        )
        response = self._http.get(
            url, headers={"Authorization": f"Bearer {self._access_token}"}
        )
        if response.status_code == 401:
            self._refresh()
            response = self._http.get(
                url, headers={"Authorization": f"Bearer {self._access_token}"}
            )
        response.raise_for_status()

        records: list[dict] = []
        payload = response.json()
        records.extend(payload.get("records", []))
        next_url = payload.get("nextRecordsUrl")
        while next_url:
            page = self._http.get(
                f"{self._instance_url}{next_url}",
                headers={"Authorization": f"Bearer {self._access_token}"},
            )
            page.raise_for_status()
            page_payload = page.json()
            records.extend(page_payload.get("records", []))
            next_url = page_payload.get("nextRecordsUrl")
        return records

    def _refresh(self) -> None:
        token_url = f"{self._login_url}/services/oauth2/token"
        response = self._http.post(
            token_url,
            data={
                "grant_type": "refresh_token",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "refresh_token": self._refresh_token,
            },
        )
        response.raise_for_status()
        payload = response.json()
        self._access_token = payload["access_token"]
        self._instance_url = payload["instance_url"]


def _escape_soql(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")

"""Outreach REST API client for fetching hot mailings.

Docs: https://developers.outreach.io/api/reference/
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

API_BASE = "https://api.outreach.io/api/v2"
TOKEN_URL = "https://api.outreach.io/oauth/token"
PAGE_SIZE = 100

logger = logging.getLogger(__name__)


@dataclass
class HotMailing:
    mailing_id: str
    subject: str
    recipient_email: str
    recipient_name: str | None
    open_count: int
    delivered_at: datetime | None
    updated_at: datetime


class OutreachClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        redirect_uri: str,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self._redirect_uri = redirect_uri
        self._access_token: str | None = None
        self._token_expires_at: float = 0.0

    def _refresh_access_token(self) -> None:
        response = httpx.post(
            TOKEN_URL,
            data={
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "redirect_uri": self._redirect_uri,
                "grant_type": "refresh_token",
                "refresh_token": self._refresh_token,
            },
            timeout=30.0,
        )
        response.raise_for_status()
        payload = response.json()
        self._access_token = payload["access_token"]
        # Outreach tokens live ~2h; refresh 60s early.
        self._token_expires_at = time.time() + int(payload["expires_in"]) - 60
        new_refresh = payload.get("refresh_token")
        if new_refresh and new_refresh != self._refresh_token:
            self._refresh_token = new_refresh
            logger.info(
                "Outreach issued a new refresh token; persist OUTREACH_REFRESH_TOKEN=%s",
                new_refresh,
            )

    def _auth_header(self) -> dict[str, str]:
        if not self._access_token or time.time() >= self._token_expires_at:
            self._refresh_access_token()
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Accept": "application/vnd.api+json",
        }

    def list_hot_mailings(
        self, *, open_threshold: int, since: datetime | None
    ) -> list[HotMailing]:
        """Return mailings with openCount > open_threshold updated after `since`."""
        params: dict[str, str] = {
            "filter[openCount][greaterThan]": str(open_threshold),
            "filter[state]": "delivered",
            "sort": "updatedAt",
            "page[size]": str(PAGE_SIZE),
            "include": "prospect",
        }
        if since is not None:
            params["filter[updatedAt]"] = f"{_iso(since)}..inf"

        url: str | None = f"{API_BASE}/mailings"
        out: list[HotMailing] = []
        prospects_by_id: dict[str, dict] = {}

        while url:
            response = httpx.get(
                url,
                headers=self._auth_header(),
                params=params if url.endswith("/mailings") else None,
                timeout=30.0,
            )
            if response.status_code == 401:
                self._refresh_access_token()
                response = httpx.get(
                    url,
                    headers=self._auth_header(),
                    params=params if url.endswith("/mailings") else None,
                    timeout=30.0,
                )
            response.raise_for_status()
            body = response.json()

            for included in body.get("included", []):
                if included.get("type") == "prospect":
                    prospects_by_id[included["id"]] = included

            for item in body.get("data", []):
                attrs = item.get("attributes", {})
                prospect_id = (
                    item.get("relationships", {})
                    .get("prospect", {})
                    .get("data", {})
                    .get("id")
                )
                prospect = prospects_by_id.get(prospect_id) if prospect_id else None
                recipient_name = None
                if prospect:
                    p_attrs = prospect.get("attributes", {})
                    first = p_attrs.get("firstName") or ""
                    last = p_attrs.get("lastName") or ""
                    full = f"{first} {last}".strip()
                    recipient_name = full or p_attrs.get("name")

                to_list = attrs.get("toRecipients") or []
                recipient_email = to_list[0] if to_list else None
                if not recipient_email:
                    logger.warning(
                        "Mailing %s has no recipient; skipping", item.get("id")
                    )
                    continue

                out.append(
                    HotMailing(
                        mailing_id=item["id"],
                        subject=attrs.get("subject") or "",
                        recipient_email=recipient_email,
                        recipient_name=recipient_name,
                        open_count=int(attrs.get("openCount") or 0),
                        delivered_at=_parse_iso(attrs.get("deliveredAt")),
                        updated_at=_parse_iso(attrs.get("updatedAt"))
                        or datetime.now(timezone.utc),
                    )
                )

            url = body.get("links", {}).get("next")

        return out


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))

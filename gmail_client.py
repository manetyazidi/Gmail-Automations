"""Thin Gmail API wrapper: find a sent thread, reply on it, send a self email."""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
]

logger = logging.getLogger(__name__)


@dataclass
class ThreadMatch:
    thread_id: str
    message_id_header: str
    subject: str
    references: str | None


class GmailClient:
    def __init__(self, credentials_path: Path, token_path: Path) -> None:
        self._service = _build_service(credentials_path, token_path)

    def find_thread(
        self, *, recipient: str, subject: str, lookback_days: int
    ) -> ThreadMatch | None:
        query = (
            f'to:{recipient} subject:"{_escape_subject(subject)}" '
            f"in:sent newer_than:{lookback_days}d"
        )
        result = (
            self._service.users()
            .threads()
            .list(userId="me", q=query, maxResults=5)
            .execute()
        )
        threads = result.get("threads", [])
        if not threads:
            return None

        thread_id = threads[0]["id"]
        thread = (
            self._service.users()
            .threads()
            .get(userId="me", id=thread_id, format="metadata",
                 metadataHeaders=["Message-ID", "Subject", "References"])
            .execute()
        )

        first_msg = thread["messages"][0]
        headers = {h["name"].lower(): h["value"] for h in first_msg["payload"]["headers"]}
        return ThreadMatch(
            thread_id=thread_id,
            message_id_header=headers.get("message-id", ""),
            subject=headers.get("subject", subject),
            references=headers.get("references"),
        )

    def reply(
        self,
        *,
        from_addr: str,
        to_addr: str,
        thread: ThreadMatch,
        body: str,
    ) -> str:
        msg = EmailMessage()
        msg["From"] = from_addr
        msg["To"] = to_addr
        reply_subject = thread.subject
        if not reply_subject.lower().startswith("re:"):
            reply_subject = f"Re: {reply_subject}"
        msg["Subject"] = reply_subject
        if thread.message_id_header:
            msg["In-Reply-To"] = thread.message_id_header
            refs = (
                f"{thread.references} {thread.message_id_header}"
                if thread.references
                else thread.message_id_header
            )
            msg["References"] = refs
        msg.set_content(body)

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        sent = (
            self._service.users()
            .messages()
            .send(
                userId="me",
                body={"raw": raw, "threadId": thread.thread_id},
            )
            .execute()
        )
        return sent["id"]

    def send_self(self, *, from_addr: str, subject: str, body: str) -> str:
        msg = EmailMessage()
        msg["From"] = from_addr
        msg["To"] = from_addr
        msg["Subject"] = subject
        msg.set_content(body)
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        sent = (
            self._service.users()
            .messages()
            .send(userId="me", body={"raw": raw})
            .execute()
        )
        return sent["id"]

    def send_self_html(
        self,
        *,
        from_addr: str,
        subject: str,
        text_body: str,
        html_body: str,
    ) -> str:
        msg = EmailMessage()
        msg["From"] = from_addr
        msg["To"] = from_addr
        msg["Subject"] = subject
        msg.set_content(text_body)
        msg.add_alternative(html_body, subtype="html")
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        sent = (
            self._service.users()
            .messages()
            .send(userId="me", body={"raw": raw})
            .execute()
        )
        return sent["id"]


def _escape_subject(subject: str) -> str:
    # Gmail search strips most punctuation; quoting is enough.
    return subject.replace('"', "")


def _build_service(credentials_path: Path, token_path: Path):
    creds: Credentials | None = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(credentials_path), SCOPES
            )
            creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json())
    return build("gmail", "v1", credentials=creds, cache_discovery=False)

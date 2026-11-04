"""Gmail (inbox) and Google Sheets (ledger) through one OAuth client.

Get the refresh token once with ``python scripts/google_auth.py``.
"""
from __future__ import annotations

import base64
import html
import logging
import re
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import parseaddr

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from app.config import Settings
from app.integrations.base import Email

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",  # read, label, send; no permanent delete
    "https://www.googleapis.com/auth/spreadsheets",
]


def credentials(s: Settings) -> Credentials:
    return Credentials(
        token=None,
        refresh_token=s.google_refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=s.google_client_id,
        client_secret=s.google_client_secret,
        scopes=SCOPES,
    )


def _decode(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")


def _body(payload: dict) -> str:
    """Prefer text/plain; fall back to stripped text/html."""
    plain, rich = [], []

    def walk(part):
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data")
        if data and mime == "text/plain":
            plain.append(_decode(data))
        elif data and mime == "text/html":
            rich.append(_decode(data))
        for p in part.get("parts", []) or []:
            walk(p)

    walk(payload)
    if plain:
        text = "\n".join(plain)
    elif rich:
        text = html.unescape(re.sub(r"<[^>]+>", " ", "\n".join(rich)))
    else:
        text = ""
    # Drop the quoted history of earlier replies so the agent sees the new message.
    text = re.split(r"\nOn .{5,200}wrote:\s*\n", text, maxsplit=1)[0]
    return re.sub(r"\n{3,}", "\n\n", text).strip()


class GmailInbox:
    def __init__(self, s: Settings):
        self.svc = build("gmail", "v1", credentials=credentials(s), cache_discovery=False)
        self.label_name = s.gmail_processed_label
        self.label_id = self._ensure_label(self.label_name)
        self.address = self.svc.users().getProfile(userId="me").execute()["emailAddress"]

    def _ensure_label(self, name: str) -> str:
        labels = self.svc.users().labels().list(userId="me").execute().get("labels", [])
        for label in labels:
            if label["name"] == name:
                return label["id"]
        created = self.svc.users().labels().create(
            userId="me", body={"name": name, "labelListVisibility": "labelShow", "messageListVisibility": "show"}
        ).execute()
        return created["id"]

    def fetch_new(self) -> list[Email]:
        q = f"in:inbox -label:{self.label_name} newer_than:7d"
        listed = self.svc.users().messages().list(userId="me", q=q, maxResults=20).execute()
        emails = []
        for m in reversed(listed.get("messages", [])):  # oldest first
            full = self.svc.users().messages().get(userId="me", id=m["id"], format="full").execute()
            headers = {h["name"].lower(): h["value"] for h in full["payload"].get("headers", [])}
            name, addr = parseaddr(headers.get("from", ""))
            emails.append(
                Email(
                    message_id=full["id"],
                    thread_id=full["threadId"],
                    sender_email=addr.lower(),
                    sender_name=name or addr,
                    subject=headers.get("subject", "(no subject)"),
                    body=_body(full["payload"]),
                    received_at=datetime.fromtimestamp(int(full["internalDate"]) / 1000, tz=timezone.utc),
                    rfc822_id=headers.get("message-id", ""),
                    headers=headers,
                )
            )
        return emails

    def send_reply(self, to: str, subject: str, body: str, thread_id: str, in_reply_to: str) -> None:
        msg = EmailMessage()
        msg["To"] = to
        msg["From"] = self.address
        msg["Subject"] = subject
        msg["Auto-Submitted"] = "auto-replied"  # tells other robots not to answer us (loop safety)
        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
            msg["References"] = in_reply_to
        msg.set_content(body)
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        self.svc.users().messages().send(userId="me", body={"raw": raw, "threadId": thread_id}).execute()

    def mark_processed(self, message_id: str) -> None:
        self.svc.users().messages().modify(
            userId="me", id=message_id, body={"addLabelIds": [self.label_id], "removeLabelIds": ["UNREAD"]}
        ).execute()


class SheetsLedger:
    COLUMNS = ["received", "from", "subject", "category", "action", "order", "summary", "tokens", "seconds"]
    HEADERS = ["Received (UTC)", "From", "Subject", "Category", "Action", "Order", "Summary", "Tokens", "Seconds"]

    def __init__(self, s: Settings):
        self.svc = build("sheets", "v4", credentials=credentials(s), cache_discovery=False)
        self.sheet_id = s.sheet_id
        first = self.svc.spreadsheets().values().get(spreadsheetId=self.sheet_id, range="A1:I1").execute()
        if not first.get("values"):
            self._append([self.HEADERS])

    def _append(self, rows):
        self.svc.spreadsheets().values().append(
            spreadsheetId=self.sheet_id, range="A1", valueInputOption="RAW",
            insertDataOption="INSERT_ROWS", body={"values": rows},
        ).execute()

    def append_row(self, row: dict) -> None:
        # RAW, not USER_ENTERED: a subject like "=HYPERLINK(...)" must stay text, not become a formula.
        self._append([[str(row.get(c, "")) for c in self.COLUMNS]])

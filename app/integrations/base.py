"""Interfaces for the outside world (inbox, ledger sheet, team chat) plus
in-memory fakes. The pipeline only talks to these interfaces, so tests and
local runs use the fakes and production plugs in Gmail, Sheets and Slack."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol


@dataclass
class Email:
    message_id: str  # provider id (Gmail message id)
    thread_id: str
    sender_email: str
    sender_name: str
    subject: str
    body: str
    received_at: datetime
    rfc822_id: str = ""  # Message-ID header, for In-Reply-To threading
    headers: dict[str, str] = field(default_factory=dict)  # lower-cased names


class Inbox(Protocol):
    def fetch_new(self) -> list[Email]: ...
    def send_reply(self, to: str, subject: str, body: str, thread_id: str, in_reply_to: str) -> None: ...
    def mark_processed(self, message_id: str) -> None: ...


class Ledger(Protocol):
    def append_row(self, row: dict) -> None: ...


class Notifier(Protocol):
    def post_approval(self, approval_id: int, text: str) -> str | None: ...
    def post_escalation(self, text: str) -> None: ...
    def update_approval(self, ts: str | None, text: str) -> None: ...


@dataclass
class Services:
    inbox: Inbox
    ledger: Ledger
    notifier: Notifier


# --- Fakes ----------------------------------------------------------------------
class FakeInbox:
    def __init__(self, emails: list[Email] | None = None):
        self.pending = list(emails or [])
        self.sent: list[dict] = []
        self.processed: list[str] = []

    def fetch_new(self) -> list[Email]:
        return [e for e in self.pending if e.message_id not in self.processed]

    def send_reply(self, to, subject, body, thread_id, in_reply_to):
        self.sent.append({"to": to, "subject": subject, "body": body, "thread_id": thread_id,
                          "in_reply_to": in_reply_to})

    def mark_processed(self, message_id):
        self.processed.append(message_id)


class FakeLedger:
    def __init__(self):
        self.rows: list[dict] = []

    def append_row(self, row):
        self.rows.append(row)


class FakeNotifier:
    def __init__(self):
        self.approvals: list[tuple[int, str]] = []
        self.escalations: list[str] = []
        self.updates: list[tuple[str | None, str]] = []

    def post_approval(self, approval_id, text):
        self.approvals.append((approval_id, text))
        return f"ts-{approval_id}"

    def post_escalation(self, text):
        self.escalations.append(text)

    def update_approval(self, ts, text):
        self.updates.append((ts, text))


def fake_services(emails: list[Email] | None = None) -> Services:
    return Services(inbox=FakeInbox(emails), ledger=FakeLedger(), notifier=FakeNotifier())

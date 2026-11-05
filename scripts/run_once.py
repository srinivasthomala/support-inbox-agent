"""Process the inbox once, locally, with real Gmail, Sheets and Claude.

    python scripts/run_once.py

Slack is used if SLACK_BOT_TOKEN is set; otherwise Slack messages are printed
here instead, so you can test before the Slack app exists.
"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anthropic  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import ProcessedEmail, init_db, session  # noqa: E402
from app.integrations.base import Services  # noqa: E402
from app.integrations.google import GmailInbox, SheetsLedger  # noqa: E402
from app.pipeline import process_inbox  # noqa: E402
from app.store import seed  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")


class ConsoleNotifier:
    def post_approval(self, approval_id, text):
        print(f"\n[SLACK approval #{approval_id}]\n{text}\n  (buttons: Approve / Reject)\n")
        return None

    def post_escalation(self, text):
        print(f"\n[SLACK escalation]\n{text}\n")

    def update_approval(self, ts, text):
        print(f"\n[SLACK update] {text}\n")


s = get_settings()
init_db()
db = session()
seed(db)

if s.slack_bot_token:
    from app.integrations.slack import SlackNotifier

    notifier = SlackNotifier(s)
else:
    notifier = ConsoleNotifier()

inbox = GmailInbox(s)
services = Services(inbox=inbox, ledger=SheetsLedger(s), notifier=notifier)
client = anthropic.Anthropic(api_key=s.anthropic_api_key, timeout=30.0, max_retries=2)

n = process_inbox(db, services, client, s, own_address=inbox.address)
print(f"\nHandled {n} email(s).")
for r in db.query(ProcessedEmail).order_by(ProcessedEmail.id.desc()).limit(n):
    print(f"- {r.sender} | {r.subject[:50]} -> {r.outcome} ({r.category}) "
          f"{r.steps} steps, {r.input_tokens + r.output_tokens} tokens, {r.latency_ms / 1000:.1f}s")
    if r.summary:
        print(f"    {r.summary[:200]}")
db.close()

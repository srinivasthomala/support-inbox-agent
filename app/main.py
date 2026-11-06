"""FastAPI app: runs the Gmail poller and receives Slack button clicks."""
from __future__ import annotations

import json
import logging
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from urllib.parse import parse_qs

import anthropic
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from slack_sdk.signature import SignatureVerifier

from app.approvals import decide
from app.config import get_settings
from app.db import init_db, session
from app.integrations.base import Services
from app import pages
from app.pipeline import process_inbox
from app.store import seed

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("support-agent")

state = {"last_poll_ok": None, "last_poll_error": None, "handled_total": 0}


def build_services() -> Services:
    from app.integrations.google import GmailInbox, SheetsLedger
    from app.integrations.slack import SlackNotifier

    s = get_settings()
    return Services(inbox=GmailInbox(s), ledger=SheetsLedger(s), notifier=SlackNotifier(s))


def anthropic_client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=get_settings().anthropic_api_key, timeout=30.0, max_retries=2)


class Poller(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True, name="gmail-poller")
        self.stop = threading.Event()

    def run(self):
        s = get_settings()
        services = None
        client = anthropic_client()
        while not self.stop.is_set():
            try:
                services = services or build_services()
                db = session()
                try:
                    state["handled_total"] += process_inbox(db, services, client, s, own_address=services.inbox.address)
                finally:
                    db.close()
                state["last_poll_ok"] = datetime.now(timezone.utc).isoformat()
                state["last_poll_error"] = None
            except Exception as e:  # keep polling; rebuild clients next time
                logger.exception("poll failed")
                state["last_poll_error"] = f"{type(e).__name__}: {e}"[:300]
                services = None
            self.stop.wait(s.gmail_poll_seconds)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    db = session()
    try:
        seed(db)
    finally:
        db.close()
    poller = None
    if get_settings().poller_enabled:
        poller = Poller()
        poller.start()
        logger.info("gmail poller started")
    yield
    if poller:
        poller.stop.set()


app = FastAPI(title="Support Inbox Agent", lifespan=lifespan)
app.include_router(pages.router)


@app.get("/health")
def health():
    # Monitors can alert if last_poll_ok goes stale or last_poll_error is set.
    return {"status": "ok", **state}


def _handle_click(approval_id: int, approve: bool, user: str):
    db = session()
    try:
        result = decide(db, approval_id, approve, user, build_services())
        logger.info("approval %s by %s: %s", approval_id, user, result)
    except Exception:
        logger.exception("approval %s failed", approval_id)
    finally:
        db.close()


@app.post("/slack/actions")
async def slack_actions(request: Request, background: BackgroundTasks):
    body = await request.body()
    verifier = SignatureVerifier(get_settings().slack_signing_secret)
    # Rejects forged requests and replays older than 5 minutes.
    if not get_settings().slack_signing_secret or not verifier.is_valid_request(body, dict(request.headers)):
        raise HTTPException(status_code=401, detail="invalid signature")

    payload = json.loads(parse_qs(body.decode())["payload"][0])
    action = (payload.get("actions") or [{}])[0]
    if action.get("action_id") not in ("approve_refund", "reject_refund"):
        return {}
    user = payload.get("user", {}).get("name") or payload.get("user", {}).get("id", "unknown")
    # Slack wants a reply within 3 seconds; sending the email can take longer.
    background.add_task(_handle_click, int(action["value"]), action["action_id"] == "approve_refund", user)
    return {}

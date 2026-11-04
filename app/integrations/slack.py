"""Slack: escalations and refund approvals with Approve / Reject buttons.

Button clicks come back to POST /slack/actions, which verifies Slack's
request signature before doing anything (see app/main.py).
"""
from __future__ import annotations

from slack_sdk import WebClient

from app.config import Settings


class SlackNotifier:
    def __init__(self, s: Settings):
        self.client = WebClient(token=s.slack_bot_token)
        self.channel = s.slack_channel_id

    def post_approval(self, approval_id: int, text: str) -> str | None:
        resp = self.client.chat_postMessage(
            channel=self.channel,
            text=text,
            blocks=[
                {"type": "section", "text": {"type": "mrkdwn", "text": text}},
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button", "style": "primary", "action_id": "approve_refund",
                            "value": str(approval_id), "text": {"type": "plain_text", "text": "Approve refund"},
                            "confirm": {
                                "title": {"type": "plain_text", "text": "Issue this refund?"},
                                "text": {"type": "mrkdwn", "text": "The customer is emailed right away."},
                                "confirm": {"type": "plain_text", "text": "Approve"},
                                "deny": {"type": "plain_text", "text": "Cancel"},
                            },
                        },
                        {
                            "type": "button", "style": "danger", "action_id": "reject_refund",
                            "value": str(approval_id), "text": {"type": "plain_text", "text": "Reject"},
                        },
                    ],
                },
            ],
        )
        return resp["ts"]

    def post_escalation(self, text: str) -> None:
        self.client.chat_postMessage(channel=self.channel, text=text)

    def update_approval(self, ts: str | None, text: str) -> None:
        if ts:
            # Replace the buttons with the decision so nobody clicks twice.
            self.client.chat_update(
                channel=self.channel, ts=ts, text=text,
                blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": text}}],
            )
        else:
            self.post_escalation(text)

"""Tools for one inbound email.

A ``Toolbox`` is created per email and bound to its sender, so:

- order lookups only ever return the *sender's* orders (someone emailing an
  order number they found can't learn about another customer's order);
- replies always go back to the sender, in the same thread (there is no
  "to" parameter for a prompt injection to redirect);
- refund amounts and eligibility are checked here, in code, against the
  order. The model can only *request* a refund; a human approves it in Slack.

Every email must end in exactly one terminal action: send_reply,
request_refund_approval, escalate_to_human or mark_no_action. The pipeline
escalates automatically if the model finishes without one.
"""
from __future__ import annotations

import json
from datetime import timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.approvals import request_text
from app.config import Settings
from app.db import Customer, Order, ProcessedEmail, RefundApproval, utcnow
from app.integrations.base import Email, Services
from app.store import POLICIES

CATEGORIES = [
    "order_status", "shipping", "refund_request", "product_question",
    "subscription", "complaint", "other", "spam_or_automated",
]
_CATEGORY = {"type": "string", "enum": CATEGORIES, "description": "What this email is about."}

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "lookup_orders",
        "description": (
            "Look up the sender's orders. Pass an order id like BRW-1042 if the customer gave one, "
            "or null to list their recent orders. Only the sender's own orders are visible."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"order_id": {"type": ["string", "null"]}},
            "required": ["order_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_policy",
        "description": "Read the store's official policy text for a topic. Quote it instead of guessing.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"topic": {"type": "string", "enum": list(POLICIES)}},
            "required": ["topic"],
            "additionalProperties": False,
        },
    },
    {
        "name": "send_reply",
        "description": (
            "FINAL ACTION. Send a reply to the customer in this email thread. Use it for questions "
            "you can fully answer from order data and policies. For refund requests, only to decline "
            "one the checks show is not eligible (category refund_request with that order_id); "
            "eligible refunds go through request_refund_approval. Not for complaints that need a "
            "person (use escalate_to_human)."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "category": _CATEGORY,
                "body": {"type": "string", "description": "Plain-text reply, signed by the support team."},
                "order_id": {"type": ["string", "null"]},
            },
            "required": ["category", "body", "order_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "request_refund_approval",
        "description": (
            "FINAL ACTION. Ask a team member to approve a refund. Eligibility and the amount are "
            "checked against the order; if it fails you get an error explaining why. The holding "
            "reply is sent now; the approved reply is sent only if a person approves."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string"},
                "amount": {"type": "number", "description": "Refund amount in USD."},
                "reason": {"type": "string", "description": "One line for the approver."},
                "holding_reply": {"type": "string", "description": "Sent now: we're reviewing it."},
                "reply_if_approved": {"type": "string", "description": "Sent after approval."},
            },
            "required": ["order_id", "amount", "reason", "holding_reply", "reply_if_approved"],
            "additionalProperties": False,
        },
    },
    {
        "name": "escalate_to_human",
        "description": (
            "FINAL ACTION. Hand the email to a person: angry customers, legal or safety issues, "
            "anything you can't resolve from data and policy, or refunds that fail the checks but "
            "deserve a look. No reply is sent to the customer."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "category": _CATEGORY,
                "reason": {"type": "string"},
                "summary": {"type": "string", "description": "2-3 lines for the team."},
                "order_id": {"type": ["string", "null"]},
            },
            "required": ["category", "reason", "summary", "order_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "mark_no_action",
        "description": (
            "FINAL ACTION. For spam, newsletters, automatic replies and anything that needs no "
            "response. Nothing is sent."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"reason": {"type": "string"}},
            "required": ["reason"],
            "additionalProperties": False,
        },
    },
]


class ToolError(Exception):
    pass


class _OrderQ(BaseModel):
    order_id: str | None


class _Policy(BaseModel):
    topic: Literal[tuple(POLICIES)]  # type: ignore[valid-type]


class _Reply(BaseModel):
    category: Literal[tuple(CATEGORIES)]  # type: ignore[valid-type]
    body: str = Field(min_length=10, max_length=4000)
    order_id: str | None


class _Refund(BaseModel):
    order_id: str
    amount: float
    reason: str = Field(min_length=3, max_length=500)
    holding_reply: str = Field(min_length=10, max_length=4000)
    reply_if_approved: str = Field(min_length=10, max_length=4000)


class _Escalate(BaseModel):
    category: Literal[tuple(CATEGORIES)]  # type: ignore[valid-type]
    reason: str = Field(min_length=3, max_length=500)
    summary: str = Field(min_length=3, max_length=2000)
    order_id: str | None


class _NoAction(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


def _reply_subject(subject: str) -> str:
    return subject if subject.lower().startswith("re:") else f"Re: {subject}"


class Toolbox:
    def __init__(self, db: Session, email: Email, record: ProcessedEmail, services: Services, settings: Settings):
        self.db = db
        self.email = email
        self.record = record
        self.services = services
        self.settings = settings
        self.final_action: str | None = None  # replied | approval_requested | escalated | skipped
        self.category: str | None = None
        self.order_id: str | None = None
        self.summary: str | None = None

    def execute(self, name: str, raw_input: Any) -> tuple[str, bool]:
        handlers = {
            "lookup_orders": (_OrderQ, self._lookup_orders),
            "get_policy": (_Policy, self._get_policy),
            "send_reply": (_Reply, self._send_reply),
            "request_refund_approval": (_Refund, self._request_refund),
            "escalate_to_human": (_Escalate, self._escalate),
            "mark_no_action": (_NoAction, self._no_action),
        }
        if name not in handlers:
            return f"Unknown tool: {name}", True
        model, handler = handlers[name]
        try:
            args = model.model_validate(raw_input or {})
            return json.dumps(handler(args)), False
        except ValidationError as e:
            return f"Invalid input: {e.errors(include_url=False)}", True
        except ToolError as e:
            return str(e), True

    # -- helpers -----------------------------------------------------------------
    def _customer(self) -> Customer | None:
        return self.db.scalar(select(Customer).where(Customer.email == self.email.sender_email.lower()))

    def _own_order(self, order_id: str) -> Order:
        customer = self._customer()
        order = self.db.get(Order, order_id.strip().upper())
        if order is None or customer is None or order.customer_id != customer.id:
            # Same message whether it doesn't exist or belongs to someone else.
            raise ToolError(f"No order {order_id} was found for {self.email.sender_email}.")
        return order

    def _require_open(self):
        if self.final_action:
            raise ToolError(f"This email is already handled ({self.final_action}). Stop now.")

    def _order_view(self, o: Order) -> dict:
        return {
            "order_id": o.id,
            "items": json.loads(o.items),
            "total": o.total,
            "status": o.status,
            "carrier": o.carrier,
            "tracking_number": o.tracking_number,
            "ordered_on": o.ordered_at.date().isoformat(),
            "delivered_on": o.delivered_at.date().isoformat() if o.delivered_at else None,
            "already_refunded": o.refunded_amount,
        }

    def _refund_blocker(self, o: Order) -> str | None:
        """Why this order can't be refunded, or None if it can."""
        if o.status != "delivered":
            return f"Order {o.id} is '{o.status}', not delivered; refunds apply to delivered orders."
        window = timedelta(days=self.settings.refund_window_days)
        if o.delivered_at is None or utcnow() - _aware(o.delivered_at) > window:
            return (f"Order {o.id} was delivered more than {self.settings.refund_window_days} days ago, "
                    "outside the refund window.")
        if o.total - o.refunded_amount <= 0:
            return f"Order {o.id} has already been fully refunded."
        return None

    # -- read tools ----------------------------------------------------------------
    def _lookup_orders(self, args: _OrderQ) -> dict:
        if args.order_id:
            return {"orders": [self._order_view(self._own_order(args.order_id))]}
        customer = self._customer()
        if customer is None:
            return {"orders": [], "note": f"{self.email.sender_email} is not a known customer."}
        orders = self.db.scalars(
            select(Order).where(Order.customer_id == customer.id).order_by(Order.ordered_at.desc()).limit(5)
        ).all()
        return {"customer_name": customer.name, "orders": [self._order_view(o) for o in orders]}

    def _get_policy(self, args: _Policy) -> dict:
        return {"topic": args.topic, "policy": POLICIES[args.topic]}

    # -- final actions ---------------------------------------------------------------
    def _send_reply(self, args: _Reply) -> dict:
        self._require_open()
        order = self._own_order(args.order_id) if args.order_id else None
        if args.category == "refund_request":
            # Declining is allowed only when the code itself finds the order ineligible;
            # anything that could be refunded must go to a person for approval.
            if order is None or self._refund_blocker(order) is None:
                raise ToolError(
                    "This refund may be eligible, so it needs approval: use request_refund_approval "
                    "(or escalate_to_human). Replies on refund requests are only for declining an "
                    "order_id that fails the refund checks."
                )
        self.services.inbox.send_reply(
            to=self.email.sender_email,
            subject=_reply_subject(self.email.subject),
            body=args.body,
            thread_id=self.email.thread_id,
            in_reply_to=self.email.rfc822_id,
        )
        self.final_action, self.category, self.order_id = "replied", args.category, args.order_id
        self.summary = args.body[:300]
        return {"status": "sent"}

    def _request_refund(self, args: _Refund) -> dict:
        self._require_open()
        order = self._own_order(args.order_id)
        blocker = self._refund_blocker(order)
        if blocker:
            raise ToolError(
                f"{blocker} Decline kindly with send_reply (category refund_request, order_id "
                f"{order.id}), quoting the policy, or escalate if there is a good reason."
            )
        remaining = round(order.total - order.refunded_amount, 2)
        if not 0 < args.amount <= remaining:
            raise ToolError(f"Amount must be between 0 and {remaining:.2f} (order total minus earlier refunds).")

        approval = RefundApproval(
            email_id=self.record.id, order_id=order.id, amount=round(args.amount, 2), reason=args.reason,
            reply_if_approved=args.reply_if_approved, status="pending",
        )
        self.db.add(approval)
        self.db.commit()
        approval.slack_ts = self.services.notifier.post_approval(
            approval.id,
            request_text(order, approval.amount, args.reason,
                         f"{self.email.sender_name} <{self.email.sender_email}>", self.email.subject),
        )
        self.db.commit()
        self.services.inbox.send_reply(
            to=self.email.sender_email, subject=_reply_subject(self.email.subject), body=args.holding_reply,
            thread_id=self.email.thread_id, in_reply_to=self.email.rfc822_id,
        )
        self.final_action, self.category, self.order_id = "approval_requested", "refund_request", order.id
        self.summary = f"Refund ${approval.amount:.2f} on {order.id} awaiting approval: {args.reason}"
        return {"status": "awaiting_approval", "approval_id": approval.id}

    def _escalate(self, args: _Escalate) -> dict:
        self._require_open()
        self.services.notifier.post_escalation(
            f"*Needs a person* ({args.category})\nFrom: {self.email.sender_name} <{self.email.sender_email}>\n"
            f"Subject: {self.email.subject}\nWhy: {args.reason}\n{args.summary}"
        )
        self.final_action, self.category, self.order_id = "escalated", args.category, args.order_id
        self.summary = args.summary[:300]
        return {"status": "escalated"}

    def _no_action(self, args: _NoAction) -> dict:
        self._require_open()
        self.final_action, self.category, self.summary = "skipped", "spam_or_automated", args.reason[:300]
        return {"status": "no_action"}


def _aware(dt):
    from datetime import timezone

    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

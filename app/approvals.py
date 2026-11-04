"""Human decisions on refund requests (the Slack Approve / Reject buttons).

Idempotent: Slack can deliver the same click twice, and two people can click
at once. Only the first decision on a pending approval has any effect.
Eligibility is re-checked at approval time, because the order may have
changed since the agent asked (for example, a refund issued by hand).
"""
from __future__ import annotations

import logging

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.db import Order, ProcessedEmail, RefundApproval, utcnow
from app.integrations.base import Services

logger = logging.getLogger(__name__)


def decide(db: Session, approval_id: int, approve: bool, decided_by: str, services: Services) -> str:
    """Apply a decision. Returns a short status message for the person who clicked."""
    new_status = "approved" if approve else "rejected"
    # Atomic claim: only flips a *pending* row, so a double click changes nothing.
    claimed = db.execute(
        update(RefundApproval)
        .where(RefundApproval.id == approval_id, RefundApproval.status == "pending")
        .values(status=new_status, decided_by=decided_by, decided_at=utcnow())
    ).rowcount
    db.commit()
    approval = db.get(RefundApproval, approval_id)
    if approval is None:
        return "That refund request no longer exists."
    if not claimed:
        return f"Already {approval.status} by {approval.decided_by}."

    email = db.get(ProcessedEmail, approval.email_id)
    order = db.get(Order, approval.order_id)

    if approve:
        remaining = round(order.total - order.refunded_amount, 2)
        if approval.amount > remaining:
            approval.status = "rejected"
            db.commit()
            msg = f"Not applied: only ${remaining:.2f} is still refundable on {order.id}."
            services.notifier.update_approval(approval.slack_ts, f":warning: {msg}")
            return msg
        order.refunded_amount = round(order.refunded_amount + approval.amount, 2)
        if order.refunded_amount >= order.total:
            order.status = "refunded"
        db.commit()
        services.inbox.send_reply(
            to=email.sender,
            subject=email.subject if email.subject.lower().startswith("re:") else f"Re: {email.subject}",
            body=approval.reply_if_approved,
            thread_id=email.thread_id,
            in_reply_to=email.rfc822_id,
        )
        text = f":white_check_mark: Refunded ${approval.amount:.2f} on {order.id}, approved by {decided_by}. Customer notified."
    else:
        text = f":x: Refund on {order.id} rejected by {decided_by}. No email sent; please follow up with the customer."

    services.notifier.update_approval(approval.slack_ts, text)
    try:
        services.ledger.append_row({
            "received": utcnow().isoformat(), "from": email.sender, "subject": email.subject,
            "category": "refund_request", "action": f"refund_{new_status}", "order": order.id,
            "summary": f"${approval.amount:.2f} {new_status} by {decided_by}", "tokens": 0, "seconds": 0,
        })
    except Exception:
        logger.exception("ledger append failed for approval %s", approval_id)
    return text

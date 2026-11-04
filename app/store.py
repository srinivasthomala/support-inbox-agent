"""The fictional demo store: policies the agent can quote and seed data.

Brewline Coffee Co. is made up for this demo. The orders below are designed so
each demo scenario has an order to find: in transit, delivered (refundable),
delivered too long ago (outside the refund window), still processing, etc.
"""
from __future__ import annotations

import json
from datetime import timedelta

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import Customer, Order, utcnow

POLICIES: dict[str, str] = {
    "shipping": (
        "Orders ship within 1-2 business days. Standard shipping takes 3-5 business days "
        "after dispatch; express takes 1-2. Customers get a tracking link by email when the "
        "order ships. Free standard shipping on orders over $40."
    ),
    "returns_refunds": (
        "Unopened items can be returned within 30 days of delivery for a full refund. "
        "Damaged or wrong items are refunded or replaced at no cost within 30 days of delivery; "
        "a photo helps but is not required. Opened coffee cannot be returned unless it was "
        "damaged or wrong. Refunds go back to the original payment method in 5-7 business days. "
        "All refunds are reviewed by a team member before they are issued."
    ),
    "products": (
        "Beans are roasted to order every Monday and Thursday and are best within 4 weeks of "
        "roasting. Whole bean or ground (espresso, drip, French press). Store beans in an airtight "
        "container away from light; do not freeze daily-use beans."
    ),
    "subscriptions": (
        "Subscriptions can be paused, skipped or cancelled any time from the account page, at least "
        "2 days before the next roast date. No cancellation fees."
    ),
    "contact": (
        "Support replies within one business day, Monday to Friday. Phone support is not available."
    ),
}


def seed(db: Session, demo_emails: str | None = None) -> None:
    """Idempotent: creates the demo customers and orders if they don't exist,
    and (re)applies DEMO_CUSTOMER_EMAILS so you can email in as a customer."""
    raw = get_settings().demo_customer_emails if demo_emails is None else demo_emails
    overrides = [e.strip().lower() for e in raw.split(",") if e.strip()]
    if db.query(Customer).count():
        for customer, email in zip(db.query(Customer).order_by(Customer.id), overrides):
            customer.email = email
        db.commit()
        return
    now = utcnow()
    customers = [
        Customer(id=1, email="maya.demo@example.com", name="Maya Patel"),
        Customer(id=2, email="leo.demo@example.com", name="Leo Garcia"),
        Customer(id=3, email="sam.demo@example.com", name="Sam Okafor"),
        Customer(id=4, email="ana.demo@example.com", name="Ana Lima"),
    ]
    for customer, email in zip(customers, overrides):
        customer.email = email
    db.add_all(customers)

    def items(*rows):
        return json.dumps([{"name": n, "qty": q, "price": p} for n, q, p in rows])

    db.add_all(
        [
            # In transit: "where is my order?"
            Order(id="BRW-1042", customer_id=1, items=items(("Ethiopia Guji, whole bean 340g", 2, 18.0)),
                  total=36.0, status="shipped", carrier="UPS", tracking_number="1Z999AA10123456784",
                  ordered_at=now - timedelta(days=3)),
            # Delivered recently: eligible for a refund (damaged bag scenario)
            Order(id="BRW-1038", customer_id=2, items=items(("Colombia Huila, ground espresso 340g", 1, 17.0),
                                                            ("Ceramic pour-over dripper", 1, 28.0)),
                  total=45.0, status="delivered", carrier="USPS", tracking_number="9400111202555842761234",
                  ordered_at=now - timedelta(days=9), delivered_at=now - timedelta(days=5)),
            # Delivered long ago: outside the 30-day refund window
            Order(id="BRW-0911", customer_id=3, items=items(("House Blend, whole bean 1kg", 1, 42.0)),
                  total=42.0, status="delivered", carrier="UPS", tracking_number="1Z999AA10123450001",
                  ordered_at=now - timedelta(days=70), delivered_at=now - timedelta(days=65)),
            # Still processing: no tracking yet
            Order(id="BRW-1047", customer_id=4, items=items(("Kenya AA, whole bean 340g", 1, 21.0)),
                  total=21.0, status="processing", ordered_at=now - timedelta(hours=20)),
        ]
    )
    db.commit()

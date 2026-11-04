"""Database models: the demo store (customers, orders) and the agent's own
records (processed emails, runs, pending approvals)."""
from __future__ import annotations

from collections.abc import Generator
from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --- Demo store ---------------------------------------------------------------
class Customer(Base):
    __tablename__ = "customers"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # e.g. BRW-1042
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    items: Mapped[str] = mapped_column(Text)  # JSON list of {name, qty, price}
    total: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(32))  # processing|shipped|delivered|refunded
    carrier: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tracking_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ordered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    refunded_amount: Mapped[float] = mapped_column(Float, default=0.0)


# --- Agent records ------------------------------------------------------------
class ProcessedEmail(Base):
    """One row per inbound email. The unique message id is what guarantees an
    email is never handled (or answered) twice, even across restarts."""

    __tablename__ = "processed_emails"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    thread_id: Mapped[str] = mapped_column(String(255))
    rfc822_id: Mapped[str] = mapped_column(String(512), default="")  # Message-ID header
    sender: Mapped[str] = mapped_column(String(255))
    subject: Mapped[str] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    outcome: Mapped[str] = mapped_column(String(32), default="processing")
    # replied | approval_requested | escalated | skipped | failed
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    order_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    steps: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    trace: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class RefundApproval(Base):
    """A refund the agent wants to issue. Only a human click in Slack applies it."""

    __tablename__ = "refund_approvals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email_id: Mapped[int] = mapped_column(ForeignKey("processed_emails.id"))
    order_id: Mapped[str] = mapped_column(String(32))
    amount: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(Text)
    reply_if_approved: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|approved|rejected
    decided_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    slack_ts: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


_engine = None
_Session = None


def engine():
    global _engine, _Session
    if _engine is None:
        url = get_settings().sync_database_url
        kwargs = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {"pool_pre_ping": True}
        _engine = create_engine(url, **kwargs)
        _Session = sessionmaker(bind=_engine, autoflush=False)
    return _engine


def session() -> Session:
    engine()
    return _Session()


def get_db() -> Generator[Session, None, None]:
    db = session()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    Base.metadata.create_all(bind=engine())

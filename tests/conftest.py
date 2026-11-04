from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.db import Base
from app.integrations.base import Email, fake_services
from app.store import seed


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    seed(s, demo_emails="")
    yield s
    s.close()


@pytest.fixture
def settings():
    return Settings(anthropic_api_key="test", _env_file=None)


@pytest.fixture
def services():
    return fake_services()


_counter = {"n": 0}


def make_email(sender="maya.demo@example.com", name="Maya Patel", subject="Where is my order?",
               body="Hi, where is order BRW-1042?", headers=None, message_id=None):
    _counter["n"] += 1
    n = _counter["n"]
    return Email(
        message_id=message_id or f"m{n}", thread_id=f"t{n}", sender_email=sender, sender_name=name,
        subject=subject, body=body, received_at=datetime.now(timezone.utc), rfc822_id=f"<m{n}@mail>",
        headers=headers or {},
    )


# -- scripted fake Claude client -------------------------------------------------
def text(t):
    return SimpleNamespace(type="text", text=t)


def tool_use(name, input, id="tu_1"):
    return SimpleNamespace(type="tool_use", id=id, name=name, input=input)


def reply(stop_reason, *content, inp=100, out=50):
    return SimpleNamespace(stop_reason=stop_reason, content=list(content),
                           usage=SimpleNamespace(input_tokens=inp, output_tokens=out))


class FakeClient:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []
        self.messages = self

    def create(self, **kwargs):
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        if not self.responses:
            raise AssertionError("Claude was called but no response was scripted")
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

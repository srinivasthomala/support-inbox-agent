"""A hand-written tool-use loop with the reliability layer around it.

The loop itself is small: call Claude, run any tools it asks for, send the
results back, repeat until it answers. Everything else is the part that makes
it safe to run unsupervised:

- **Bounded:** a step limit, a wall-clock deadline and an output-token budget,
  so a confused run can't loop forever or run up a bill.
- **Every stop_reason handled:** end_turn, tool_use, max_tokens (truncated),
  refusal, pause_turn, and anything unexpected.
- **Tool failures stay local:** a bad tool call becomes an ``is_error`` result
  Claude can correct, not an exception that kills the run.
- **API failures degrade:** typed SDK errors become a friendly status instead
  of a 500. The SDK already retries 429/5xx/connection errors.
- **Traced:** every model call and tool call is recorded (name, input, ok,
  latency, tokens) for debugging and for the audit row.

When a run stops early (limits, refusal, API error), the status says so and
the caller hands the work to a human: that's the escalation path.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

import anthropic

logger = logging.getLogger(__name__)


class ToolExecutor(Protocol):
    def execute(self, name: str, raw_input: Any) -> tuple[str, bool]: ...


@dataclass
class AgentLimits:
    max_steps: int = 6
    deadline_seconds: float = 45.0
    max_output_tokens: int = 12000
    max_tokens_per_call: int = 4000


@dataclass
class AgentResult:
    reply: str
    status: str  # ok | limit | truncated | refusal | error
    steps: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    trace: list[dict] = field(default_factory=list)


# Internal notes only: callers act on ``status``; these are never sent to customers.
LIMIT_REPLY = "Stopped at the step, time or token limit before finishing."
ERROR_REPLY = "The AI service could not be reached."
REFUSAL_REPLY = "The model declined this request."


def _text(content) -> str:
    return "".join(b.text for b in content if b.type == "text").strip()


def run_agent(
    client: anthropic.Anthropic,
    *,
    model: str,
    system: str,
    tools: list[dict],
    messages: list[dict],
    executor: ToolExecutor,
    limits: AgentLimits,
    effort: str = "medium",
    clock: Callable[[], float] = time.monotonic,
) -> AgentResult:
    """Run one question to completion. ``messages`` is extended in place."""
    started = clock()
    result = AgentResult(reply="", status="ok")

    for step in range(1, limits.max_steps + 1):
        elapsed = clock() - started
        if elapsed > limits.deadline_seconds:
            logger.warning("agent deadline hit after %d steps (%.1fs)", step - 1, elapsed)
            result.status, result.reply = "limit", LIMIT_REPLY
            return result
        budget_left = limits.max_output_tokens - result.output_tokens
        if budget_left <= 0:
            result.status, result.reply = "limit", LIMIT_REPLY
            return result

        call_started = clock()
        try:
            response = client.messages.create(
                model=model,
                max_tokens=min(limits.max_tokens_per_call, budget_left),
                system=system,
                tools=tools,
                messages=messages,
                output_config={"effort": effort},
            )
        except anthropic.APIStatusError as e:  # 4xx/5xx after the SDK's retries
            logger.error("agent model call failed: %s %s", e.status_code, e.message)
            result.trace.append(
                {"type": "model", "step": step, "error": e.status_code, "message": str(e.message)[:300]}
            )
            result.status, result.reply = "error", ERROR_REPLY
            return result
        except anthropic.APIConnectionError as e:  # includes timeouts
            logger.error("agent model call could not connect: %s", e)
            result.trace.append({"type": "model", "step": step, "error": "connection"})
            result.status, result.reply = "error", ERROR_REPLY
            return result

        result.steps = step
        result.input_tokens += response.usage.input_tokens
        result.output_tokens += response.usage.output_tokens
        result.trace.append(
            {
                "type": "model",
                "step": step,
                "stop_reason": response.stop_reason,
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
                "ms": int((clock() - call_started) * 1000),
            }
        )
        # Append the full content (thinking + tool_use blocks included); the
        # API needs it back unchanged on the next call.
        messages.append({"role": "assistant", "content": response.content})
        stop = response.stop_reason

        if stop in ("end_turn", "stop_sequence"):
            result.reply = _text(response.content) or "Done."
            return result

        if stop == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                t0 = clock()
                content, is_error = executor.execute(block.name, block.input)
                result.trace.append(
                    {
                        "type": "tool",
                        "step": step,
                        "name": block.name,
                        "input": block.input,
                        "ok": not is_error,
                        "ms": int((clock() - t0) * 1000),
                        **({"error": content[:300]} if is_error else {}),
                    }
                )
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": content,
                        **({"is_error": True} if is_error else {}),
                    }
                )
            # All results go back in ONE user message, matching parallel calls.
            messages.append({"role": "user", "content": tool_results})
            continue

        if stop == "pause_turn":
            continue  # resend as-is to let the model resume

        if stop == "max_tokens":
            partial = _text(response.content)
            result.status = "truncated"
            result.reply = (partial + " ...") if partial else LIMIT_REPLY
            return result

        if stop == "refusal":
            result.status, result.reply = "refusal", REFUSAL_REPLY
            return result

        logger.error("agent got unexpected stop_reason %r", stop)
        result.status, result.reply = "error", ERROR_REPLY
        return result

    logger.warning("agent step limit (%d) hit", limits.max_steps)
    result.status, result.reply = "limit", LIMIT_REPLY
    return result

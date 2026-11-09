"""
The agent loop, as a reusable function instead of a script.

`run_agent()` is the same tool-calling loop agent_demo.py always had --
model sees TOOLS, picks one, TOOL_FUNCTIONS[name](**input) runs it, result
goes back, repeat until the model answers in plain text -- but packaged so
more than one front end can drive it:

    agent_demo.py   -> prints tool calls to stderr, final answer to stdout
    app.py          -> Streamlit chat UI that shows each tool call live
                       (the SQL, the rows, the chart) as it happens

Two things it adds over the original script loop:

  * Multi-turn memory: it takes and returns the full message history, so a
    follow-up like "now split that by state" works.
  * Guard rails on the loop itself: a tool that raises becomes an error
    result the model can read (not a crash), an unknown tool name is
    reported back the same way, and `max_steps` stops a model that keeps
    calling tools forever.

The Anthropic client is injected (`client=`), so tests can drive the whole
loop with a fake client and no network or API key.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .tools import TOOL_FUNCTIONS, TOOLS

DEFAULT_MODEL = os.getenv("BI_AGENT_MODEL", "claude-haiku-4-5-20251001")
DEFAULT_MAX_STEPS = 10

SYSTEM_PROMPT = (
    "You are a BI analyst assistant for an e-commerce marketplace (Olist, Brazil, 2016-2018). "
    "You have tools to inspect the database schema, run read-only SQL, render charts, "
    "and read customer review sentiment. Always check the schema before writing SQL if "
    "you haven't already in this conversation. Prefer v_order_items_full for order/revenue "
    "questions. When the user asks to see, chart, or visualize something, use "
    "create_chart_from_query instead of just describing numbers. If a query errors, read the "
    "error, fix the SQL, and try again. Finish with a short, stakeholder-readable summary of "
    "what you found -- key numbers and what they mean, not a dump of raw rows. Currency is "
    "Brazilian reais (R$)."
)


@dataclass
class ToolCall:
    """One tool invocation, kept for display: what was asked, what came back."""

    id: str
    name: str
    input: dict[str, Any]
    result: dict[str, Any] | None = None
    # Chart PNG bytes captured at call time. Chart files are named after
    # their title, so a later chart with the same title would overwrite the
    # file on disk -- holding the bytes keeps earlier answers intact.
    chart_png: bytes | None = None

    @property
    def is_error(self) -> bool:
        return bool(self.result and "error" in self.result)


@dataclass
class AgentTurn:
    """Everything one user question produced."""

    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list)  # full history incl. this turn
    stopped_early: bool = False  # hit max_steps before the model gave a final answer


def _block_to_dict(block: Any) -> dict[str, Any]:
    """Anthropic SDK content block (or a plain dict) -> plain dict for the history."""
    if isinstance(block, dict):
        return block
    btype = getattr(block, "type", None)
    if btype == "text":
        return {"type": "text", "text": block.text}
    if btype == "tool_use":
        return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
    if hasattr(block, "model_dump"):
        return block.model_dump(exclude_none=True)
    raise TypeError(f"Unrecognized content block: {block!r}")


def execute_tool(name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
    """Dispatch one tool call. Never raises: failures become {'error': ...}."""
    fn = TOOL_FUNCTIONS.get(name)
    if fn is None:
        return {"error": f"Unknown tool '{name}'. Available: {sorted(TOOL_FUNCTIONS)}"}
    try:
        return fn(**tool_input)
    except TypeError as exc:  # wrong/missing arguments from the model
        return {"error": f"Bad arguments for {name}: {exc}"}
    except Exception as exc:  # noqa: BLE001 -- the model should see it, not the user crash
        return {"error": f"{name} failed: {type(exc).__name__}: {exc}"}


def run_agent(
    question: str,
    history: list[dict[str, Any]] | None = None,
    *,
    client: Any = None,
    model: str = DEFAULT_MODEL,
    max_steps: int = DEFAULT_MAX_STEPS,
    max_tokens: int = 2000,
    on_tool_start: Callable[[ToolCall], None] | None = None,
    on_tool_end: Callable[[ToolCall], None] | None = None,
) -> AgentTurn:
    """Answer one question, calling tools as the model decides.

    Args:
        question: the user's message.
        history: prior messages from earlier turns (AgentTurn.messages), for
            follow-up questions. Not mutated.
        client: an anthropic.Anthropic-compatible client. Created from the
            environment (ANTHROPIC_API_KEY) if omitted.
        on_tool_start / on_tool_end: optional callbacks so a UI can show
            progress while the loop runs.
    """
    if client is None:
        import anthropic

        client = anthropic.Anthropic()

    messages: list[dict[str, Any]] = list(history or [])
    messages.append({"role": "user", "content": question})
    calls: list[ToolCall] = []

    for _ in range(max_steps):
        response = client.messages.create(
            model=model, max_tokens=max_tokens, system=SYSTEM_PROMPT,
            tools=TOOLS, messages=messages,
        )
        content = [_block_to_dict(b) for b in response.content]
        messages.append({"role": "assistant", "content": content})

        tool_uses = [b for b in content if b["type"] == "tool_use"]
        if response.stop_reason != "tool_use" or not tool_uses:
            text = "\n\n".join(b["text"] for b in content if b["type"] == "text").strip()
            return AgentTurn(text=text, tool_calls=calls, messages=messages)

        results = []
        for block in tool_uses:
            call = ToolCall(id=block["id"], name=block["name"], input=dict(block["input"] or {}))
            if on_tool_start:
                on_tool_start(call)
            call.result = execute_tool(call.name, call.input)
            chart_path = call.result.get("chart_path") if isinstance(call.result, dict) else None
            if chart_path and Path(chart_path).exists():
                call.chart_png = Path(chart_path).read_bytes()
            calls.append(call)
            if on_tool_end:
                on_tool_end(call)
            results.append({
                "type": "tool_result",
                "tool_use_id": call.id,
                "content": json.dumps(call.result, default=str),
                **({"is_error": True} if call.is_error else {}),
            })
        messages.append({"role": "user", "content": results})

    # Out of steps. The history now ends on tool results with no answer, so
    # append a short assistant note to keep it valid for the next turn.
    note = (
        f"I stopped after {max_steps} tool-calling steps without reaching a final answer. "
        "Try a narrower question, or ask me to continue."
    )
    messages.append({"role": "assistant", "content": [{"type": "text", "text": note}]})
    return AgentTurn(text=note, tool_calls=calls, messages=messages, stopped_early=True)

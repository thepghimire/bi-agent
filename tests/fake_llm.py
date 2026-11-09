"""
A scripted stand-in for anthropic.Anthropic, so the agent loop and the
Streamlit app can be tested offline. Each call to messages.create() pops
the next scripted response; every request is recorded for assertions.
"""
from __future__ import annotations

import copy
from types import SimpleNamespace
from typing import Any


def text(t: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=t)


def tool_use(id: str, name: str, **inputs: Any) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=id, name=name, input=inputs)


def response(*blocks: SimpleNamespace) -> SimpleNamespace:
    stop = "tool_use" if any(b.type == "tool_use" for b in blocks) else "end_turn"
    return SimpleNamespace(stop_reason=stop, content=list(blocks))


class FakeClient:
    def __init__(self, script: list[SimpleNamespace], repeat_last: bool = False):
        self._script = list(script)
        self._repeat_last = repeat_last
        self.requests: list[dict[str, Any]] = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs: Any) -> SimpleNamespace:
        self.requests.append(copy.deepcopy(kwargs))
        if len(self._script) == 1 and self._repeat_last:
            return self._script[0]
        if not self._script:
            raise AssertionError("FakeClient ran out of scripted responses")
        return self._script.pop(0)

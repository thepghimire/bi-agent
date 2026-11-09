"""
Run ONE agent turn outside Streamlit, so you can step through it in a
debugger and see every message that goes to and from the model.

    python scripts/debug_agent.py --offline
        A scripted fake model plays back realistic tool calls against the
        REAL database. No API key, no cost, same result every run --
        the easiest way to step through the whole pipeline.

    python scripts/debug_agent.py "Which states order the most?"
        Real Claude (needs ANTHROPIC_API_KEY in .env or the environment).

Either way it prints each tool call as it happens, then saves the full
message history to debug_last_turn.json so you can read exactly what the
model saw and said.

Good breakpoints (see the walkthrough for why):
    bi_agent/agent.py      run_agent()       -- the loop: model call, tool dispatch
    bi_agent/agent.py      execute_tool()    -- where a tool name becomes a Python call
    bi_agent/query.py      validate_query()  -- the SQL safety checks
    bi_agent/query.py      run_sql_query()   -- the read-only connection + row cap
    bi_agent/charts.py     create_chart()    -- rows -> PNG
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))           # so `import bi_agent` works from anywhere
sys.path.insert(0, str(ROOT / "tests"))  # for the fake model used by --offline

from bi_agent.agent import ToolCall, run_agent  # noqa: E402

REVENUE_SQL = (
    "SELECT category_english, ROUND(SUM(revenue), 0) AS total "
    "FROM v_order_items_full GROUP BY category_english ORDER BY total DESC LIMIT 5"
)


def offline_client():
    """A fake model that 'decides' the same steps a real one usually would."""
    from fake_llm import FakeClient, response, text, tool_use

    return FakeClient([
        response(text("Let me look at the schema first."), tool_use("t1", "get_schema")),
        response(tool_use("t2", "run_sql_query", query=REVENUE_SQL)),
        response(tool_use("t3", "create_chart_from_query", query=REVENUE_SQL, chart_type="hbar",
                          x="category_english", y="total", title="Top 5 categories by revenue",
                          y_label="Revenue (R$)")),
        response(text("(Scripted answer) Health & beauty is the top category by revenue.")),
    ])


def short(obj, limit: int = 300) -> str:
    s = json.dumps(obj, default=str)
    return s if len(s) <= limit else s[:limit] + f"... ({len(s):,} chars)"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("question", nargs="?", default="Which 5 product categories bring in the most revenue? Show me a chart.")
    parser.add_argument("--offline", action="store_true", help="use the scripted fake model (no API key)")
    parser.add_argument("--model", default=None, help="override the model name")
    args = parser.parse_args()

    client = offline_client() if args.offline else None  # None -> run_agent builds a real client
    kwargs = {"model": args.model} if args.model else {}

    def on_start(call: ToolCall) -> None:
        print(f"\n-> tool call: {call.name}({short(call.input)})")

    def on_end(call: ToolCall) -> None:
        print(f"<- result:    {short(call.result)}")

    print(f"Question: {args.question}  [{'offline fake model' if args.offline else 'real Claude'}]")
    turn = run_agent(args.question, client=client, on_tool_start=on_start, on_tool_end=on_end, **kwargs)

    print("\n=== Final answer ===\n" + turn.text)
    out = ROOT / "debug_last_turn.json"
    out.write_text(json.dumps(turn.messages, indent=2, default=str))
    print(f"\n{len(turn.tool_calls)} tool calls, {len(turn.messages)} messages. Full history saved to {out.name}")


if __name__ == "__main__":
    main()

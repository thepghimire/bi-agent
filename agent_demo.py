"""
Live agentic demo (command line): a text-to-SQL-and-chart BI assistant over
the Olist dataset. The model sees the schema tool, decides what SQL to
write, decides whether a chart is warranted, and can pull in review
sentiment -- all from one flat tool list, no hand-written NL-to-SQL parser.

The loop itself lives in bi_agent/agent.py so the Streamlit app (app.py)
and this script share it.

Requires:
    pip install -r requirements-agent.txt
    export ANTHROPIC_API_KEY=...
    python -m bi_agent.cli load-data
    python -m bi_agent.cli score-reviews   # optional, only needed for sentiment questions

Usage:
    python agent_demo.py "Which product categories bring in the most revenue?"
    python agent_demo.py "Chart monthly order volume over time"
    python agent_demo.py "How do customers feel about the health_beauty category?"
"""
from __future__ import annotations

import json
import sys


def main(question: str) -> None:
    try:
        import anthropic  # noqa: F401
    except ImportError:
        print(
            "The 'anthropic' package isn't installed. Run:\n"
            "  pip install -r requirements-agent.txt\n"
            "This demo is optional -- the pipeline and tools work without it."
        )
        sys.exit(1)

    from bi_agent.agent import run_agent

    def log_call(call) -> None:
        print(f"[tool call] {call.name}({json.dumps(call.input)})", file=sys.stderr)

    def log_result(call) -> None:
        if call.is_error:
            print(f"[tool error] {call.result['error']}", file=sys.stderr)
        elif call.result.get("chart_path"):
            print(f"[chart saved] {call.result['chart_path']}", file=sys.stderr)

    turn = run_agent(question, on_tool_start=log_call, on_tool_end=log_result)
    print(turn.text)


if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) or "Which product categories bring in the most revenue? Show me a chart."
    main(q)

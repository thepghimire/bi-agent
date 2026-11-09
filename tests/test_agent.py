"""
Tests for the reusable agent loop (bi_agent.agent), driven by a scripted
fake model -- no network, no API key. They check the loop mechanics the UI
depends on: tools really run against the DB, results go back to the model,
history carries across turns, and bad tool calls don't crash anything.
"""
import json

from bi_agent.agent import execute_tool, run_agent

from fake_llm import FakeClient, response, text, tool_use

REVENUE_SQL = (
    "SELECT category_english, SUM(revenue) AS total FROM v_order_items_full "
    "GROUP BY category_english ORDER BY total DESC"
)


def test_answers_directly_without_tools(seeded_db_path):
    client = FakeClient([response(text("Hello! Ask me about orders."))])
    turn = run_agent("hi", client=client)
    assert turn.text == "Hello! Ask me about orders."
    assert turn.tool_calls == []
    assert [m["role"] for m in turn.messages] == ["user", "assistant"]


def test_runs_sql_tool_and_feeds_result_back(seeded_db_path):
    client = FakeClient([
        response(text("Let me check."), tool_use("t1", "run_sql_query", query=REVENUE_SQL)),
        response(text("bed_bath_table leads with R$110.")),
    ])
    seen = []
    turn = run_agent("top categories?", client=client,
                     on_tool_start=lambda c: seen.append(("start", c.name)),
                     on_tool_end=lambda c: seen.append(("end", c.name)))

    assert turn.text == "bed_bath_table leads with R$110."
    assert seen == [("start", "run_sql_query"), ("end", "run_sql_query")]
    call = turn.tool_calls[0]
    assert call.result["rows"][0] == {"category_english": "bed_bath_table", "total": 110.0}

    # The second request must carry the tool result back to the model.
    tool_result = client.requests[1]["messages"][-1]["content"][0]
    assert tool_result["type"] == "tool_result" and tool_result["tool_use_id"] == "t1"
    assert json.loads(tool_result["content"])["rows"][0]["total"] == 110.0
    assert "is_error" not in tool_result


def test_chart_bytes_are_captured(seeded_db_path, monkeypatch, tmp_path):
    monkeypatch.setenv("BI_AGENT_CHARTS_DIR", str(tmp_path / "charts"))
    client = FakeClient([
        response(tool_use("t1", "create_chart_from_query", query=REVENUE_SQL,
                          chart_type="hbar", x="category_english", y="total", title="Revenue")),
        response(text("Here's the chart.")),
    ])
    turn = run_agent("chart revenue", client=client)
    assert turn.tool_calls[0].chart_png.startswith(b"\x89PNG")


def test_sql_error_is_reported_to_model_not_raised(seeded_db_path):
    client = FakeClient([
        response(tool_use("t1", "run_sql_query", query="DELETE FROM orders")),
        response(text("That query isn't allowed.")),
    ])
    turn = run_agent("delete everything", client=client)
    assert turn.tool_calls[0].is_error
    assert client.requests[1]["messages"][-1]["content"][0]["is_error"] is True


def test_unknown_tool_and_bad_arguments_become_errors():
    assert "Unknown tool" in execute_tool("drop_tables", {})["error"]
    assert "Bad arguments" in execute_tool("run_sql_query", {"sql": "SELECT 1"})["error"]


def test_history_carries_into_follow_up(seeded_db_path):
    client = FakeClient([response(text("First answer.")), response(text("Follow-up answer."))])
    first = run_agent("first question", client=client)
    second = run_agent("and by state?", first.messages, client=client)

    sent = client.requests[1]["messages"]
    assert [m["role"] for m in sent] == ["user", "assistant", "user"]
    assert sent[0]["content"] == "first question"
    assert second.text == "Follow-up answer."
    assert len(first.messages) == 2  # earlier turn's history wasn't mutated


def test_max_steps_stops_a_runaway_loop(seeded_db_path):
    loop = response(tool_use("t", "run_sql_query", query="SELECT 1 AS one"))
    client = FakeClient([loop], repeat_last=True)
    turn = run_agent("loop forever", client=client, max_steps=3)

    assert turn.stopped_early
    assert len(turn.tool_calls) == 3
    # History ends with an assistant message, so a follow-up turn is still valid.
    assert turn.messages[-1]["role"] == "assistant"

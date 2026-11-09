"""
Smoke tests for the Streamlit UI (app.py) using Streamlit's AppTest
harness and the scripted fake model -- no browser, no network.
"""
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from fake_llm import FakeClient, response, text, tool_use  # noqa: E402

APP = str(Path(__file__).resolve().parent.parent / "app.py")


def _app(client=None) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=30)
    if client is not None:
        at.session_state["client_override"] = client
    return at


def test_shows_build_button_when_no_database(tmp_path, monkeypatch):
    monkeypatch.setenv("BI_AGENT_DB_PATH", str(tmp_path / "missing.db"))
    at = _app().run()
    assert not at.exception
    assert any("Build database" in b.label for b in at.sidebar.button)


def test_question_runs_agent_and_shows_sql_and_answer(seeded_db_path, monkeypatch, tmp_path):
    monkeypatch.setenv("BI_AGENT_CHARTS_DIR", str(tmp_path / "charts"))
    client = FakeClient([
        response(tool_use("t1", "run_sql_query", query="SELECT COUNT(*) AS n FROM orders")),
        response(text("There are **2 orders** in the data.")),
    ])
    at = _app(client).run()
    assert not at.exception
    assert len(at.button) >= 4  # example questions shown on an empty chat

    at.chat_input[0].set_value("How many orders are there?").run()
    assert not at.exception
    markdown = " ".join(m.value for m in at.markdown)
    assert "How many orders are there?" in markdown
    assert "There are **2 orders** in the data." in markdown
    assert any("SELECT COUNT(*) AS n FROM orders" in c.value for c in at.code)
    assert len(at.session_state["history"]) == 4  # user, tool_use, tool_result, answer


def test_example_button_asks_that_question(seeded_db_path):
    client = FakeClient([response(text("Revenue answer."))])
    at = _app(client).run()
    at.button(key="example_0").click().run()
    assert not at.exception
    assert at.session_state["chat"][0]["text"].startswith("Which product categories")
    assert at.session_state["chat"][1]["text"] == "Revenue answer."


def test_missing_api_key_gives_a_clear_message(seeded_db_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    at = _app().run()
    at.chat_input[0].set_value("hello").run()
    assert not at.exception
    assert "API key" in at.session_state["chat"][-1]["text"]

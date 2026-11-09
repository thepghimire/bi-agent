"""
Tests for the agent-facing tool functions in bi_agent.tools -- exercised
the way an LLM tool-calling loop would call them: plain kwargs in, plain
JSON-serializable dict out.
"""
import json
from pathlib import Path

from bi_agent import db, sentiment


def test_get_schema_lists_queryable_objects(seeded_db_path):
    from bi_agent.tools import get_schema

    result = get_schema()
    names = {t["name"] for t in result["tables"]}
    assert "v_order_items_full" in names
    assert "orders" in names


def test_run_sql_query_tool(seeded_db_path):
    from bi_agent.tools import run_sql_query

    result = run_sql_query(query="SELECT COUNT(*) AS n FROM orders")
    assert json.dumps(result)
    assert result["rows"][0]["n"] == 2


def test_create_chart_from_query_tool(seeded_db_path, monkeypatch, tmp_path):
    monkeypatch.setenv("BI_AGENT_CHARTS_DIR", str(tmp_path / "charts"))
    from bi_agent.tools import create_chart_from_query

    result = create_chart_from_query(
        query="SELECT category_english, SUM(revenue) AS total FROM v_order_items_full GROUP BY category_english",
        chart_type="bar", x="category_english", y="total", title="Revenue by category",
    )
    assert "error" not in result
    assert Path(result["chart_path"]).exists()


def test_create_chart_from_query_tool_propagates_sql_error(seeded_db_path):
    from bi_agent.tools import create_chart_from_query

    result = create_chart_from_query(
        query="DROP TABLE orders", chart_type="bar", x="a", y="b",
    )
    assert "error" in result


def test_get_review_sentiment_summary_before_scoring(seeded_db_path):
    from bi_agent.tools import get_review_sentiment_summary

    result = get_review_sentiment_summary()
    assert "error" in result  # nothing scored yet


def test_get_review_sentiment_summary_after_scoring(seeded_db_path):
    from bi_agent.tools import get_review_sentiment_summary

    conn = db.connect(seeded_db_path)
    analyzer = sentiment.LexiconSentimentAnalyzer()
    sentiment.score_reviews(conn, analyzer, limit=10)
    conn.close()

    result = get_review_sentiment_summary()
    assert json.dumps(result)
    assert result["reviews_scored_in_scope"] == 2
    assert result["counts"]["positive"] == 1
    assert result["counts"]["negative"] == 1


def test_get_review_sentiment_summary_filters_by_category(seeded_db_path):
    from bi_agent.tools import get_review_sentiment_summary

    conn = db.connect(seeded_db_path)
    analyzer = sentiment.LexiconSentimentAnalyzer()
    sentiment.score_reviews(conn, analyzer, limit=10)
    conn.close()

    result = get_review_sentiment_summary(category="bed_bath_table")
    assert result["reviews_scored_in_scope"] == 1
    assert result["counts"]["positive"] == 1
    assert result["counts"]["negative"] == 0


def test_tool_schemas_match_functions():
    from bi_agent.tools import TOOL_FUNCTIONS, TOOLS

    names_in_schema = {t["name"] for t in TOOLS}
    assert names_in_schema == set(TOOL_FUNCTIONS.keys())
    for schema in TOOLS:
        assert schema["input_schema"]["type"] == "object"

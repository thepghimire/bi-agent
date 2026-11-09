"""
Agent-facing tools: plain Python functions + Anthropic-format tool schemas,
same pattern as football_agent.tools. TOOLS goes straight into a
`tools=` kwarg; TOOL_FUNCTIONS maps name -> callable for a dispatch loop.

Four tools, each doing one job:
    get_schema              -- "what can I even query?"
    run_sql_query            -- read-only SQL -> rows (guarded, see query.py)
    create_chart_from_query  -- read-only SQL -> rendered PNG (guarded + charted)
    get_review_sentiment_summary -- pre-scored NLP sentiment, aggregated

create_chart_from_query takes a SQL string rather than inline row data on
purpose: an LLM re-typing a large JSON result back into its own next tool
call is slow, expensive, and error-prone. Handing it a SQL string instead
means the chart tool re-runs the (cheap, already-validated) query itself.
"""
from __future__ import annotations

from typing import Any

from . import db
from .charts import create_chart as _create_chart
from .config import get_settings
from .query import get_schema as _get_schema
from .query import run_sql_query as _run_sql_query


def get_schema() -> dict[str, Any]:
    """List every table/view available to query, with columns and a short description of each."""
    return _get_schema()


def run_sql_query(query: str) -> dict[str, Any]:
    """Run a read-only SQL SELECT against the BI database and return the rows.

    Args:
        query: A single SELECT (or WITH ... SELECT) statement. No writes,
            no DDL, no multiple statements. Results are capped at the
            server's configured row limit (truncated=true if you hit it --
            add your own LIMIT/aggregation to narrow it down).
    """
    return _run_sql_query(query)


def create_chart_from_query(
    query: str,
    chart_type: str,
    x: str,
    y: str,
    series: str | None = None,
    title: str = "",
    x_label: str | None = None,
    y_label: str | None = None,
) -> dict[str, Any]:
    """Run a read-only SQL query and render the results as a chart PNG.

    Args:
        query: SELECT statement whose result columns include `x`, `y`, and
            (if given) `series`. Aggregate/GROUP BY in the SQL itself --
            this does not re-aggregate.
        chart_type: "bar" (vertical columns), "hbar" (horizontal bars --
            better for many or long category names), "line" (trend over
            time/ordered axis), or "stacked_bar" (part-to-whole across
            `series`).
        x: result column to use for the category/time axis.
        y: result column to use for the numeric value.
        series: optional result column to split into multiple series
            (grouped for bar/line, stacked for stacked_bar). Capped at 8
            distinct values; extras fold into "Other".
        title, x_label, y_label: chart text.
    """
    result = _run_sql_query(query)
    if "error" in result:
        return result
    return _create_chart(
        data=result["rows"], chart_type=chart_type, x=x, y=y, series=series,
        title=title, x_label=x_label, y_label=y_label,
    )


def get_review_sentiment_summary(
    category: str | None = None,
    state: str | None = None,
) -> dict[str, Any]:
    """Summarize customer review sentiment (already scored by the NLP
    pipeline -- see bi_agent.sentiment / `cli.py score-reviews`), optionally
    filtered to a product category or customer state.

    Args:
        category: English product category (e.g. "health_beauty"). Optional.
        state: 2-letter Brazilian customer state code (e.g. "SP"). Optional.
    """
    settings = get_settings()
    conn = db.connect(settings.db_path)
    try:
        filters = []
        params: list[Any] = []
        if category:
            filters.append("v.category_english = ?")
            params.append(category)
        if state:
            filters.append("v.customer_state = ?")
            params.append(state)
        where = f"WHERE {' AND '.join(filters)}" if filters else ""

        scored = conn.execute("SELECT COUNT(*) AS c FROM review_sentiment").fetchone()["c"]
        if scored == 0:
            return {
                "error": "No reviews have been sentiment-scored yet. Run: "
                         "python -m bi_agent.cli score-reviews"
            }

        counts = conn.execute(
            f"""
            SELECT rs.label, COUNT(DISTINCT rs.review_id) AS n
            FROM review_sentiment rs
            JOIN order_reviews r ON r.review_id = rs.review_id
            JOIN v_order_items_full v ON v.order_id = r.order_id
            {where}
            GROUP BY rs.label
            """,
            params,
        ).fetchall()
        count_map = {"positive": 0, "neutral": 0, "negative": 0}
        for row in counts:
            count_map[row["label"]] = row["n"]
        total = sum(count_map.values())

        examples = conn.execute(
            f"""
            SELECT DISTINCT rs.label, r.review_comment_message
            FROM review_sentiment rs
            JOIN order_reviews r ON r.review_id = rs.review_id
            JOIN v_order_items_full v ON v.order_id = r.order_id
            {where}
            ORDER BY rs.score DESC
            LIMIT 6
            """,
            params,
        ).fetchall()

        return {
            "filters": {"category": category, "state": state},
            "reviews_scored_in_scope": total,
            "counts": count_map,
            "example_comments": [
                {"label": r["label"], "comment": r["review_comment_message"][:200]}
                for r in examples
            ],
        }
    finally:
        conn.close()


TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_schema",
        "description": "List every table/view available to query in the BI database, with columns and a short description of each. Call this before writing SQL if you haven't already seen the schema.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "run_sql_query",
        "description": "Run a read-only SQL SELECT against the e-commerce BI database and get back the rows. Use v_order_items_full for most revenue/order questions -- it's already a wide, pre-joined view.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "A single SELECT (or WITH ... SELECT) statement."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "create_chart_from_query",
        "description": "Run a read-only SQL query and render the result as a chart image (PNG). Use this whenever the user wants to 'see', 'visualize', 'chart', or 'plot' something, instead of just describing numbers in text.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "SELECT statement whose result columns include x, y, and (if given) series."},
                "chart_type": {"type": "string", "enum": ["bar", "hbar", "line", "stacked_bar"]},
                "x": {"type": "string", "description": "Result column for the category/time axis."},
                "y": {"type": "string", "description": "Result column for the numeric value."},
                "series": {"type": "string", "description": "Optional result column to split into multiple series."},
                "title": {"type": "string"},
                "x_label": {"type": "string"},
                "y_label": {"type": "string"},
            },
            "required": ["query", "chart_type", "x", "y"],
        },
    },
    {
        "name": "get_review_sentiment_summary",
        "description": "Get a summary of customer review sentiment (positive/neutral/negative counts + example comments), computed by a transformer NLP model over review text. Optionally filter by product category or customer state.",
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "description": "English product category, e.g. 'health_beauty'."},
                "state": {"type": "string", "description": "2-letter Brazilian state code, e.g. 'SP'."},
            },
            "required": [],
        },
    },
]

TOOL_FUNCTIONS = {
    "get_schema": get_schema,
    "run_sql_query": run_sql_query,
    "create_chart_from_query": create_chart_from_query,
    "get_review_sentiment_summary": get_review_sentiment_summary,
}

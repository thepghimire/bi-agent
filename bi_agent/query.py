"""
Schema introspection and a guarded, read-only SQL execution tool.

This is the part of an agentic BI system that actually has to be defensive:
an LLM is going to write arbitrary SQL text and we're going to run it. The
guardrails here are layered on purpose -- no single check is trusted alone:

  1. Statement shape: must be exactly one SELECT/WITH statement (no chained
     statements via ';', no comments hiding a second statement).
  2. Keyword blocklist: reject any write/DDL/pragma/attach keyword appearing
     anywhere in the text, not just at the start (catches subqueries like
     `SELECT * FROM x; DROP TABLE y` as well as `SELECT (SELECT ... ATTACH ...)`).
  3. The connection itself is opened read-only at the OS/SQLite level
     (`mode=ro` + `PRAGMA query_only=ON`), so even a guard that missed
     something still can't write.
  4. A hard row cap, enforced in Python after fetch, independent of
     whatever LIMIT (if any) the query itself specified.

Defense in depth: if you only had step 3, a crafted query could still hang
the process reading millions of rows; if you only had steps 1-2, a bug in
the regex is a straight path to disaster. Layering means one weak link
doesn't sink the tool.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

from .config import Settings, get_settings
from .db import QUERYABLE_OBJECTS

_ALLOWED_START = re.compile(r"^\s*(SELECT|WITH)\b", re.IGNORECASE)
_FORBIDDEN_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|ATTACH|DETACH|PRAGMA|VACUUM|REPLACE|"
    r"CREATE|TRIGGER|REINDEX|ANALYZE|BEGIN|COMMIT|ROLLBACK|SAVEPOINT)\b",
    re.IGNORECASE,
)

TABLE_DESCRIPTIONS = {
    "customers": "One row per customer. customer_unique_id identifies the same person across repeat orders.",
    "sellers": "One row per seller (marketplace vendor).",
    "category_translation": "Maps product_category_name (Portuguese) to English.",
    "products": "One row per product, including category and physical dimensions.",
    "orders": "One row per order: status and the full timestamp lifecycle (purchase -> approved -> delivered).",
    "order_items": "One row per line item within an order (an order can have multiple items/sellers).",
    "order_payments": "One row per payment installment/method applied to an order.",
    "order_reviews": "One row per customer review: review_score (1-5) and free-text comments in Portuguese.",
    "review_sentiment": "Sentiment score for order_reviews.review_comment_message, populated by bi_agent.sentiment.",
    "v_order_items_full": (
        "Wide, pre-joined view: order_items + orders + products + category_translation "
        "+ customers + sellers, with a computed `revenue` column (price + freight_value). "
        "Start here for most business questions instead of re-joining from scratch."
    ),
}


class QueryError(ValueError):
    """Raised when a query is rejected before ever touching the database."""


def validate_query(sql: str) -> str:
    """Raise QueryError with a specific reason, or return the cleaned SQL."""
    cleaned = sql.strip()
    if not cleaned:
        raise QueryError("Empty query.")

    # Allow at most one trailing semicolon; reject anything after it.
    body = cleaned[:-1].strip() if cleaned.endswith(";") else cleaned
    if ";" in body:
        raise QueryError("Only a single statement is allowed (no ';' inside the query).")

    if not _ALLOWED_START.match(body):
        raise QueryError("Only SELECT (or WITH ... SELECT) statements are allowed.")

    if _FORBIDDEN_KEYWORDS.search(body):
        raise QueryError("Query contains a disallowed keyword (writes/DDL/pragma are not permitted).")

    return body


def get_schema(settings: Settings | None = None) -> dict[str, Any]:
    """Describe every table/view an agent is allowed to query."""
    settings = settings or get_settings()
    conn = sqlite3.connect(settings.db_path)
    try:
        tables = []
        for name in QUERYABLE_OBJECTS:
            cols = conn.execute(f"PRAGMA table_info({name})").fetchall()
            tables.append(
                {
                    "name": name,
                    "description": TABLE_DESCRIPTIONS.get(name, ""),
                    "columns": [{"name": c[1], "type": c[2]} for c in cols],
                }
            )
        return {"tables": tables}
    finally:
        conn.close()


def run_sql_query(
    query: str,
    settings: Settings | None = None,
    max_rows: int | None = None,
) -> dict[str, Any]:
    """Run a validated, read-only SELECT and return rows as plain dicts.

    Returns an 'error' key instead of raising, so this is safe to call
    directly from an LLM tool-calling loop -- a rejected or failing query
    becomes a message the model can read and react to, not a crash.
    """
    settings = settings or get_settings()
    cap = max_rows or settings.max_query_rows

    try:
        body = validate_query(query)
    except QueryError as exc:
        return {"error": str(exc)}

    if not Path(settings.db_path).exists():
        return {"error": f"No database found at {settings.db_path}. Run: python -m bi_agent.cli load-data"}

    uri = f"file:{Path(settings.db_path).as_posix()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        cursor = conn.execute(body)
        columns = [d[0] for d in cursor.description] if cursor.description else []
        rows = cursor.fetchmany(cap + 1)
    except sqlite3.Error as exc:
        return {"error": f"SQL error: {exc}"}
    finally:
        conn.close()

    truncated = len(rows) > cap
    rows = rows[:cap]
    return {
        "sql": body,
        "columns": columns,
        "rows": [dict(r) for r in rows],
        "row_count": len(rows),
        "truncated": truncated,
    }

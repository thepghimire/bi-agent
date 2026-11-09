"""
Streamlit chat UI for bi-agent.

    pip install -r requirements.txt -r requirements-agent.txt -r requirements-app.txt
    python -m bi_agent.cli load-data          # or use the button in the sidebar
    export ANTHROPIC_API_KEY=...              # or paste it in the sidebar
    streamlit run app.py

Ask a question in plain English; the agent picks tools, writes its own SQL,
and the UI shows its work: every query it ran (with the rows it got back),
every chart it rendered, and the final written answer. Follow-up questions
keep the conversation context ("now break that down by state").
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st

from bi_agent import db
from bi_agent.agent import DEFAULT_MODEL, ToolCall, run_agent
from bi_agent.config import get_settings

EXAMPLE_QUESTIONS = [
    "Which product categories bring in the most revenue? Show me a chart.",
    "Chart monthly order volume over time.",
    "Which states have the highest average delivery delay?",
    "How do customers feel about the health_beauty category?",
]

TOOL_LABELS = {
    "get_schema": "Looked up the database schema",
    "run_sql_query": "Ran a SQL query",
    "create_chart_from_query": "Built a chart",
    "get_review_sentiment_summary": "Read review sentiment",
}

st.set_page_config(page_title="BI Agent · Olist", page_icon="📊", layout="wide")


# -- Data status helpers --------------------------------------------------


def db_status() -> dict:
    """Is the database built, and how many reviews are sentiment-scored?"""
    path = Path(get_settings().db_path)
    if not path.exists():
        return {"exists": False}
    try:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        orders = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        scored = conn.execute("SELECT COUNT(*) FROM review_sentiment").fetchone()[0]
        conn.close()
    except sqlite3.Error:
        return {"exists": False}
    return {"exists": orders > 0, "orders": orders, "scored": scored}


def build_database() -> dict:
    from bi_agent.load_data import load_all

    return load_all()


def score_some_reviews(limit: int) -> dict:
    from bi_agent import sentiment

    conn = db.connect(get_settings().db_path)
    try:
        analyzer = sentiment.get_sentiment_analyzer(get_settings().sentiment_backend)
        counts = sentiment.score_reviews(conn, analyzer, limit=limit)
        counts["backend"] = analyzer.backend_name
        return counts
    finally:
        conn.close()


def get_client(api_key: str):
    # Tests inject a fake client through session state; real runs build one.
    if "client_override" in st.session_state:
        return st.session_state["client_override"]
    import anthropic

    return anthropic.Anthropic(api_key=api_key)


# -- Rendering one tool call ---------------------------------------------


def render_tool_call(call: ToolCall, *, show_chart: bool = True) -> None:
    label = TOOL_LABELS.get(call.name, call.name)
    if call.is_error:
        st.markdown(f"**{label}**: failed")
        if "query" in call.input:
            st.code(call.input["query"], language="sql", wrap_lines=True)
        st.caption(f"⚠️ {call.result['error']}")
        return

    st.markdown(f"**{label}**")
    result = call.result or {}

    if call.name == "get_schema":
        names = [t["name"] for t in result.get("tables", [])]
        st.caption(", ".join(names) if names else "No tables found.")

    elif call.name == "run_sql_query":
        st.code(result.get("sql") or call.input.get("query", ""), language="sql", wrap_lines=True)
        rows = result.get("rows", [])
        if rows:
            df = pd.DataFrame(rows, columns=result.get("columns"))
            numeric = {c: st.column_config.NumberColumn(format="localized")
                       for c in df.select_dtypes("number").columns}
            st.dataframe(df, hide_index=True, width="stretch", column_config=numeric,
                         height=min(38 + 35 * len(rows), 320))
        note = f"{result.get('row_count', len(rows))} rows"
        if result.get("truncated"):
            note += " (truncated at the row limit)"
        st.caption(note)

    elif call.name == "create_chart_from_query":
        st.code(call.input.get("query", ""), language="sql", wrap_lines=True)
        if show_chart and call.chart_png:
            st.image(call.chart_png)

    elif call.name == "get_review_sentiment_summary":
        counts = result.get("counts", {})
        scope = [f"{k}={v}" for k, v in (result.get("filters") or {}).items() if v]
        st.caption(f"Scope: {', '.join(scope) or 'all reviews'} · "
                   f"{result.get('reviews_scored_in_scope', 0)} scored reviews")
        cols = st.columns(3)
        for col, key in zip(cols, ("positive", "neutral", "negative")):
            col.metric(key.title(), counts.get(key, 0))
        for ex in result.get("example_comments", [])[:3]:
            st.caption(f"*{ex['label']}*: “{ex['comment']}”")


def render_assistant(text: str, calls: list[ToolCall]) -> None:
    charts = [c for c in calls if c.chart_png and not c.is_error]
    for c in charts:
        st.image(c.chart_png)
    st.markdown(text or "_(no answer text)_")
    if calls:
        with st.expander(f"Show work: {len(calls)} step{'s' if len(calls) != 1 else ''}"):
            for i, call in enumerate(calls):
                if i:
                    st.divider()
                render_tool_call(call, show_chart=False)


# -- Session state --------------------------------------------------------

ss = st.session_state
ss.setdefault("chat", [])        # display log: {"role", "text", "calls"}
ss.setdefault("history", [])     # Anthropic-format message history
ss.setdefault("pending", None)   # question queued by an example button


# -- Sidebar ---------------------------------------------------------------

with st.sidebar:
    st.header("Settings")
    env_key = os.getenv("ANTHROPIC_API_KEY", "")
    api_key = env_key or st.text_input(
        "Anthropic API key", type="password",
        help="Used only for this session. Set ANTHROPIC_API_KEY to skip this.",
    )
    if env_key:
        st.caption("Using ANTHROPIC_API_KEY from the environment.")
    model = st.text_input("Model", value=DEFAULT_MODEL)

    st.header("Data")
    status = db_status()
    if not status["exists"]:
        st.warning("The database hasn't been built yet.")
        if st.button("Build database from raw_data/", type="primary"):
            with st.spinner("Loading the Olist CSVs into SQLite…"):
                result = build_database()
            st.success(f"Loaded {result['total_rows']:,} rows.")
            st.rerun()
    else:
        st.caption(f"✅ {status['orders']:,} orders loaded")
        st.caption(f"💬 {status['scored']:,} reviews sentiment-scored")
        if st.button("Score 1,000 more reviews",
                     help="Needed for sentiment questions. Uses the transformer model if "
                          "installed, otherwise the fast lexicon fallback."):
            with st.spinner("Scoring reviews…"):
                counts = score_some_reviews(1000)
            st.success(f"Scored {counts['scored']:,} with the {counts['backend']} backend.")
            st.rerun()

    st.divider()
    if st.button("New conversation", disabled=not ss.chat):
        ss.chat, ss.history = [], []
        st.rerun()


# -- Main panel ------------------------------------------------------------

st.title("📊 BI Agent")
st.caption("Ask questions about ~99k real Brazilian e-commerce orders (Olist, 2016–2018). "
           "The agent writes its own SQL, charts the results, and reads customer reviews.")

for msg in ss.chat:
    with st.chat_message(msg["role"]):
        if msg["role"] == "user":
            st.markdown(msg["text"])
        else:
            render_assistant(msg["text"], msg["calls"])

typed = st.chat_input("Ask about revenue, orders, delivery, reviews…")
question = typed or ss.pending
ss.pending = None

if not ss.chat and not question:
    st.markdown("**Try one of these:**")
    cols = st.columns(2)
    for i, q in enumerate(EXAMPLE_QUESTIONS):
        if cols[i % 2].button(q, key=f"example_{i}", width="stretch"):
            ss.pending = q
            st.rerun()

if question:
    ss.chat.append({"role": "user", "text": question, "calls": []})
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        if not status["exists"]:
            reply = "The database isn't built yet. Use **Build database** in the sidebar first."
            st.markdown(reply)
            ss.chat.append({"role": "assistant", "text": reply, "calls": []})
            st.stop()
        if not api_key and "client_override" not in ss:
            reply = "Add your Anthropic API key in the sidebar so I can answer."
            st.markdown(reply)
            ss.chat.append({"role": "assistant", "text": reply, "calls": []})
            st.stop()

        progress = st.status("Working on it…", expanded=True)

        def on_start(call: ToolCall) -> None:
            progress.update(label=f"{TOOL_LABELS.get(call.name, call.name)}…")

        def on_end(call: ToolCall) -> None:
            with progress:
                render_tool_call(call)

        try:
            turn = run_agent(question, ss.history, client=get_client(api_key), model=model,
                             on_tool_start=on_start, on_tool_end=on_end)
        except Exception as exc:  # noqa: BLE001 -- API/auth/network problems
            progress.update(label="Something went wrong", state="error", expanded=False)
            reply = f"I couldn't reach the model: `{type(exc).__name__}: {exc}`"
            st.markdown(reply)
            ss.chat.append({"role": "assistant", "text": reply, "calls": []})
            st.stop()

        steps = len(turn.tool_calls)
        progress.update(label=f"Done: {steps} step{'s' if steps != 1 else ''}",
                        state="complete", expanded=False)
        for c in turn.tool_calls:
            if c.chart_png and not c.is_error:
                st.image(c.chart_png)
        st.markdown(turn.text)

    ss.history = turn.messages
    ss.chat.append({"role": "assistant", "text": turn.text, "calls": turn.tool_calls})

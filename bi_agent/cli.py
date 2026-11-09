"""
Command-line entry point.

Usage:
    python -m bi_agent.cli load-data
    python -m bi_agent.cli schema
    python -m bi_agent.cli query "SELECT category_english, COUNT(*) FROM v_order_items_full GROUP BY 1"
    python -m bi_agent.cli chart --query "..." --type bar --x category_english --y total --title "..."
    python -m bi_agent.cli score-reviews --limit 200 --backend auto
    python -m bi_agent.cli sentiment --category health_beauty
"""
from __future__ import annotations

import argparse
import json
import sys


def cmd_load_data(_args: argparse.Namespace) -> None:
    from .load_data import load_all

    print(json.dumps(load_all(), indent=2))


def cmd_schema(_args: argparse.Namespace) -> None:
    from .tools import get_schema

    print(json.dumps(get_schema(), indent=2))


def cmd_query(args: argparse.Namespace) -> None:
    from .tools import run_sql_query

    print(json.dumps(run_sql_query(query=args.sql), indent=2))


def cmd_chart(args: argparse.Namespace) -> None:
    from .tools import create_chart_from_query

    result = create_chart_from_query(
        query=args.query, chart_type=args.type, x=args.x, y=args.y,
        series=args.series, title=args.title or "", x_label=args.x_label, y_label=args.y_label,
    )
    print(json.dumps(result, indent=2))


def cmd_score_reviews(args: argparse.Namespace) -> None:
    from . import db, sentiment
    from .config import get_settings

    settings = get_settings()
    conn = db.connect(settings.db_path)
    try:
        analyzer = sentiment.get_sentiment_analyzer(args.backend)
        print(f"Using backend: {analyzer.backend_name}", file=sys.stderr)
        counts = sentiment.score_reviews(conn, analyzer, limit=args.limit, rescore=args.rescore)
    finally:
        conn.close()
    print(json.dumps(counts, indent=2))


def cmd_sentiment(args: argparse.Namespace) -> None:
    from .tools import get_review_sentiment_summary

    result = get_review_sentiment_summary(category=args.category, state=args.state)
    print(json.dumps(result, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bi_agent", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("load-data", help="Load the Olist CSVs (raw_data/) into SQLite.").set_defaults(func=cmd_load_data)
    sub.add_parser("schema", help="Print the queryable schema.").set_defaults(func=cmd_schema)

    p_query = sub.add_parser("query", help="Run a read-only SQL query.")
    p_query.add_argument("sql")
    p_query.set_defaults(func=cmd_query)

    p_chart = sub.add_parser("chart", help="Run a query and render a chart PNG.")
    p_chart.add_argument("--query", required=True)
    p_chart.add_argument("--type", required=True, choices=["bar", "hbar", "line", "stacked_bar"])
    p_chart.add_argument("--x", required=True)
    p_chart.add_argument("--y", required=True)
    p_chart.add_argument("--series", default=None)
    p_chart.add_argument("--title", default="")
    p_chart.add_argument("--x-label", dest="x_label", default=None)
    p_chart.add_argument("--y-label", dest="y_label", default=None)
    p_chart.set_defaults(func=cmd_chart)

    p_score = sub.add_parser("score-reviews", help="Batch-score review sentiment into the DB.")
    p_score.add_argument("--limit", type=int, default=200)
    p_score.add_argument("--backend", default="auto", choices=["auto", "transformer", "lexicon"])
    p_score.add_argument("--rescore", action="store_true", help="Re-score reviews that already have a sentiment row.")
    p_score.set_defaults(func=cmd_score_reviews)

    p_sent = sub.add_parser("sentiment", help="Summarize already-scored review sentiment.")
    p_sent.add_argument("--category", default=None)
    p_sent.add_argument("--state", default=None)
    p_sent.set_defaults(func=cmd_sentiment)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

"""
ETL: raw Olist CSVs (raw_data/) -> SQLite (bi_agent.db).

This is a full-refresh loader (truncate + reload), which is the right model
for a mostly-static historical dataset like this one -- there's no live API
to incrementally sync against, unlike the football-agent pipeline. Each
`load_*` function is one table so a failure on one file doesn't require
re-reading the others, and so each step is independently testable.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd

from . import db
from .config import Settings, get_settings

# Order matters: children before parents would violate foreign keys.
LOAD_ORDER = [
    "customers",
    "sellers",
    "category_translation",
    "products",
    "orders",
    "order_items",
    "order_payments",
    "order_reviews",
]

_FILENAMES = {
    "customers": "olist_customers_dataset.csv",
    "sellers": "olist_sellers_dataset.csv",
    "category_translation": "product_category_name_translation.csv",
    "products": "olist_products_dataset.csv",
    "orders": "olist_orders_dataset.csv",
    "order_items": "olist_order_items_dataset.csv",
    "order_payments": "olist_order_payments_dataset.csv",
    "order_reviews": "olist_order_reviews_dataset.csv",
}


def _load_table(conn: sqlite3.Connection, raw_data_dir: Path, table: str) -> int:
    csv_path = raw_data_dir / _FILENAMES[table]
    if not csv_path.exists():
        raise FileNotFoundError(
            f"Missing {csv_path}. See raw_data/SOURCES.md for where this file comes from."
        )
    df = pd.read_csv(csv_path)

    if table == "order_reviews":
        # The raw file has a handful of (review_id, order_id) duplicates;
        # our primary key is on that pair, so de-dupe before inserting.
        df = df.drop_duplicates(subset=["review_id", "order_id"])

    conn.execute(f"DELETE FROM {table}")
    df.to_sql(table, conn, if_exists="append", index=False)
    return len(df)


def load_all(settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    conn = db.connect(settings.db_path)
    counts: dict[str, int] = {}
    try:
        # Foreign keys off during bulk load -- children can be re-inserted
        # before their parents are re-populated without tripping FK checks
        # mid-refresh; schema integrity is still enforced at table-creation
        # time and by the application code, just not instant-by-instant here.
        conn.execute("PRAGMA foreign_keys = OFF")
        for table in LOAD_ORDER:
            counts[table] = _load_table(conn, settings.raw_data_dir, table)
        conn.commit()
    finally:
        conn.close()
    return {
        "db_path": str(settings.db_path),
        "tables_loaded": counts,
        "total_rows": sum(counts.values()),
    }


if __name__ == "__main__":
    import json

    print(json.dumps(load_all(), indent=2))

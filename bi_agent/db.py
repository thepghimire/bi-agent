"""
SQLite schema for the Olist e-commerce data, plus one convenience view.

Design note: `v_order_items_full` flattens order_items + orders + products +
category translation + customers + sellers into one wide fact table. Real
BI tools almost always give an agent (or an analyst) a small number of wide,
pre-joined views rather than forcing every question through a 6-table join
-- it's the difference between the agent writing

    SELECT category, SUM(revenue) FROM v_order_items_full GROUP BY category

and re-deriving the same 6-way join from scratch on every question.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    customer_id TEXT PRIMARY KEY,
    customer_unique_id TEXT,
    customer_zip_code_prefix TEXT,
    customer_city TEXT,
    customer_state TEXT
);

CREATE TABLE IF NOT EXISTS sellers (
    seller_id TEXT PRIMARY KEY,
    seller_zip_code_prefix TEXT,
    seller_city TEXT,
    seller_state TEXT
);

CREATE TABLE IF NOT EXISTS category_translation (
    product_category_name TEXT PRIMARY KEY,
    product_category_name_english TEXT
);

CREATE TABLE IF NOT EXISTS products (
    product_id TEXT PRIMARY KEY,
    product_category_name TEXT,
    product_name_lenght REAL,
    product_description_lenght REAL,
    product_photos_qty REAL,
    product_weight_g REAL,
    product_length_cm REAL,
    product_height_cm REAL,
    product_width_cm REAL
);

CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    order_status TEXT,
    order_purchase_timestamp TEXT,
    order_approved_at TEXT,
    order_delivered_carrier_date TEXT,
    order_delivered_customer_date TEXT,
    order_estimated_delivery_date TEXT,
    FOREIGN KEY (customer_id) REFERENCES customers (customer_id)
);
CREATE INDEX IF NOT EXISTS idx_orders_customer ON orders (customer_id);
CREATE INDEX IF NOT EXISTS idx_orders_purchase_ts ON orders (order_purchase_timestamp);

CREATE TABLE IF NOT EXISTS order_items (
    order_id TEXT NOT NULL,
    order_item_id INTEGER NOT NULL,
    product_id TEXT,
    seller_id TEXT,
    price REAL,
    freight_value REAL,
    PRIMARY KEY (order_id, order_item_id),
    FOREIGN KEY (order_id) REFERENCES orders (order_id),
    FOREIGN KEY (product_id) REFERENCES products (product_id),
    FOREIGN KEY (seller_id) REFERENCES sellers (seller_id)
);
CREATE INDEX IF NOT EXISTS idx_items_order ON order_items (order_id);
CREATE INDEX IF NOT EXISTS idx_items_product ON order_items (product_id);

CREATE TABLE IF NOT EXISTS order_payments (
    order_id TEXT NOT NULL,
    payment_sequential INTEGER NOT NULL,
    payment_type TEXT,
    payment_installments INTEGER,
    payment_value REAL,
    PRIMARY KEY (order_id, payment_sequential),
    FOREIGN KEY (order_id) REFERENCES orders (order_id)
);
CREATE INDEX IF NOT EXISTS idx_payments_order ON order_payments (order_id);

CREATE TABLE IF NOT EXISTS order_reviews (
    review_id TEXT,
    order_id TEXT NOT NULL,
    review_score INTEGER,
    review_comment_title TEXT,
    review_comment_message TEXT,
    review_creation_date TEXT,
    review_answer_timestamp TEXT,
    PRIMARY KEY (review_id, order_id),
    FOREIGN KEY (order_id) REFERENCES orders (order_id)
);
CREATE INDEX IF NOT EXISTS idx_reviews_order ON order_reviews (order_id);

-- Populated separately by the sentiment tool (bi_agent.sentiment), keyed by
-- review_id so re-running analysis is an upsert, not a re-score-everything.
CREATE TABLE IF NOT EXISTS review_sentiment (
    review_id TEXT PRIMARY KEY,
    label TEXT NOT NULL,       -- 'positive' | 'neutral' | 'negative'
    score REAL NOT NULL,       -- confidence/strength, 0..1
    backend TEXT NOT NULL      -- 'transformer' | 'lexicon'
);

CREATE VIEW IF NOT EXISTS v_order_items_full AS
SELECT
    oi.order_id,
    oi.order_item_id,
    oi.price,
    oi.freight_value,
    (oi.price + oi.freight_value) AS revenue,
    o.order_status,
    o.order_purchase_timestamp,
    o.order_delivered_customer_date,
    o.order_estimated_delivery_date,
    p.product_id,
    p.product_category_name,
    COALESCE(ct.product_category_name_english, p.product_category_name) AS category_english,
    s.seller_id,
    s.seller_state,
    c.customer_id,
    c.customer_state,
    c.customer_city
FROM order_items oi
JOIN orders o ON o.order_id = oi.order_id
JOIN customers c ON c.customer_id = o.customer_id
LEFT JOIN products p ON p.product_id = oi.product_id
LEFT JOIN category_translation ct ON ct.product_category_name = p.product_category_name
LEFT JOIN sellers s ON s.seller_id = oi.seller_id;
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


# Tables an agent is allowed to query / describe. Deliberately a fixed list
# (not "whatever's in sqlite_master") so a future migration table or scratch
# table doesn't silently become queryable by the LLM.
QUERYABLE_OBJECTS = [
    "customers",
    "sellers",
    "category_translation",
    "products",
    "orders",
    "order_items",
    "order_payments",
    "order_reviews",
    "review_sentiment",
    "v_order_items_full",
]

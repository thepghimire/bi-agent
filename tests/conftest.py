import sqlite3
from pathlib import Path

import pytest

from bi_agent import db


def seed_database(conn: sqlite3.Connection) -> None:
    """Insert a small, hand-crafted dataset with known expected aggregates,
    so tests can assert exact numbers instead of just 'it didn't crash'."""
    conn.executescript(
        """
        INSERT INTO customers (customer_id, customer_unique_id, customer_zip_code_prefix, customer_city, customer_state)
        VALUES
            ('cust1', 'u1', '01000', 'Sao Paulo', 'SP'),
            ('cust2', 'u2', '20000', 'Rio de Janeiro', 'RJ');

        INSERT INTO sellers (seller_id, seller_zip_code_prefix, seller_city, seller_state)
        VALUES ('sell1', '01000', 'Sao Paulo', 'SP');

        INSERT INTO category_translation (product_category_name, product_category_name_english)
        VALUES
            ('cama_mesa_banho', 'bed_bath_table'),
            ('beleza_saude', 'health_beauty');

        INSERT INTO products (product_id, product_category_name)
        VALUES
            ('prod1', 'cama_mesa_banho'),
            ('prod2', 'beleza_saude');

        INSERT INTO orders (order_id, customer_id, order_status, order_purchase_timestamp,
                             order_approved_at, order_delivered_carrier_date,
                             order_delivered_customer_date, order_estimated_delivery_date)
        VALUES
            ('order1', 'cust1', 'delivered', '2024-01-05 10:00:00', '2024-01-05 11:00:00',
             '2024-01-06 09:00:00', '2024-01-10 14:00:00', '2024-01-12 00:00:00'),
            ('order2', 'cust2', 'delivered', '2024-02-10 10:00:00', '2024-02-10 11:00:00',
             '2024-02-11 09:00:00', '2024-02-15 14:00:00', '2024-02-18 00:00:00');

        INSERT INTO order_items (order_id, order_item_id, product_id, seller_id, price, freight_value)
        VALUES
            ('order1', 1, 'prod1', 'sell1', 100.0, 10.0),
            ('order2', 1, 'prod2', 'sell1', 50.0, 5.0);

        INSERT INTO order_payments (order_id, payment_sequential, payment_type, payment_installments, payment_value)
        VALUES
            ('order1', 1, 'credit_card', 1, 110.0),
            ('order2', 1, 'boleto', 1, 55.0);

        INSERT INTO order_reviews (review_id, order_id, review_score, review_comment_title,
                                    review_comment_message, review_creation_date, review_answer_timestamp)
        VALUES
            ('rev1', 'order1', 5, NULL, 'Otimo produto, adorei, recomendo!', '2024-01-11', '2024-01-11'),
            ('rev2', 'order2', 1, NULL, 'Pessimo, nao chegou, produto quebrado', '2024-02-16', '2024-02-16');
        """
    )
    conn.commit()


@pytest.fixture
def memory_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(db.SCHEMA)
    yield conn
    conn.close()


@pytest.fixture
def seeded_conn(memory_conn):
    seed_database(memory_conn)
    return memory_conn


@pytest.fixture
def seeded_db_path(tmp_path, monkeypatch) -> Path:
    """A real on-disk DB (query.py/tools.py open their own connection by
    path, independent of any connection a test already holds), seeded with
    the same fixture data, with BI_AGENT_DB_PATH pointed at it."""
    db_path = tmp_path / "test_bi_agent.db"
    conn = db.connect(db_path)
    seed_database(conn)
    conn.close()
    monkeypatch.setenv("BI_AGENT_DB_PATH", str(db_path))
    return db_path

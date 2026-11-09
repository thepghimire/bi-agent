def test_v_order_items_full_joins_and_computes_revenue(seeded_conn):
    rows = seeded_conn.execute(
        "SELECT * FROM v_order_items_full ORDER BY order_id"
    ).fetchall()
    assert len(rows) == 2

    o1 = rows[0]
    assert o1["order_id"] == "order1"
    assert o1["category_english"] == "bed_bath_table"
    assert o1["customer_state"] == "SP"
    assert o1["revenue"] == 110.0  # price 100 + freight 10

    o2 = rows[1]
    assert o2["category_english"] == "health_beauty"
    assert o2["customer_state"] == "RJ"
    assert o2["revenue"] == 55.0


def test_category_translation_falls_back_to_raw_name_when_missing(seeded_conn):
    seeded_conn.execute(
        "INSERT INTO products (product_id, product_category_name) VALUES ('prod3', 'unmapped_category')"
    )
    seeded_conn.execute(
        """INSERT INTO order_items (order_id, order_item_id, product_id, seller_id, price, freight_value)
           VALUES ('order1', 2, 'prod3', 'sell1', 20.0, 2.0)"""
    )
    seeded_conn.commit()
    row = seeded_conn.execute(
        "SELECT category_english FROM v_order_items_full WHERE product_id = 'prod3'"
    ).fetchone()
    assert row["category_english"] == "unmapped_category"


def test_queryable_objects_all_exist(seeded_conn):
    from bi_agent.db import QUERYABLE_OBJECTS

    names = {
        r["name"]
        for r in seeded_conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
        ).fetchall()
    }
    for obj in QUERYABLE_OBJECTS:
        assert obj in names

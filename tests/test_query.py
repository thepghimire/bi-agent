import pytest

from bi_agent.query import QueryError, run_sql_query, validate_query


@pytest.mark.parametrize(
    "bad_sql",
    [
        "DROP TABLE orders",
        "DELETE FROM orders",
        "UPDATE orders SET order_status = 'x'",
        "SELECT * FROM orders; DROP TABLE orders",
        "ATTACH DATABASE 'x.db' AS x",
        "PRAGMA table_info(orders)",
        "",
        "   ",
    ],
)
def test_validate_query_rejects_unsafe_sql(bad_sql):
    with pytest.raises(QueryError):
        validate_query(bad_sql)


@pytest.mark.parametrize(
    "good_sql",
    [
        "SELECT * FROM orders",
        "select * from orders;",
        "WITH t AS (SELECT 1 AS x) SELECT * FROM t",
    ],
)
def test_validate_query_accepts_safe_sql(good_sql):
    validate_query(good_sql)  # should not raise


def test_run_sql_query_returns_rows(seeded_db_path):
    result = run_sql_query("SELECT order_id, revenue FROM v_order_items_full ORDER BY order_id")
    assert "error" not in result
    assert result["row_count"] == 2
    assert result["rows"][0]["order_id"] == "order1"
    assert result["rows"][0]["revenue"] == 110.0


def test_run_sql_query_rejects_write(seeded_db_path):
    result = run_sql_query("DELETE FROM orders")
    assert "error" in result
    # and confirm nothing was actually touched
    check = run_sql_query("SELECT COUNT(*) AS n FROM orders")
    assert check["rows"][0]["n"] == 2


def test_run_sql_query_respects_row_cap(seeded_db_path):
    result = run_sql_query("SELECT * FROM orders", max_rows=1)
    assert result["row_count"] == 1
    assert result["truncated"] is True


def test_run_sql_query_surfaces_sql_errors_without_raising(seeded_db_path):
    result = run_sql_query("SELECT nonexistent_column FROM orders")
    assert "error" in result

from pathlib import Path

import pytest

from bi_agent.charts import create_chart
from bi_agent.config import Settings


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        db_path=tmp_path / "unused.db",
        raw_data_dir=tmp_path,
        charts_dir=tmp_path / "charts",
        max_query_rows=500,
        sentiment_backend="auto",
    )


PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


@pytest.mark.parametrize("chart_type", ["bar", "hbar", "line", "stacked_bar"])
def test_create_chart_single_series_renders_a_valid_png(tmp_path, chart_type):
    data = [
        {"category": "A", "value": 10},
        {"category": "B", "value": 30},
        {"category": "C", "value": 20},
    ]
    result = create_chart(
        data, chart_type=chart_type, x="category", y="value", title="Test chart",
        settings=_settings(tmp_path),
    )
    assert "error" not in result
    path = Path(result["chart_path"])
    assert path.exists()
    assert path.read_bytes()[:8] == PNG_MAGIC


def test_create_chart_multi_series_grouped(tmp_path):
    data = [
        {"state": "SP", "payment": "credit_card", "total": 100},
        {"state": "SP", "payment": "boleto", "total": 20},
        {"state": "RJ", "payment": "credit_card", "total": 50},
    ]
    result = create_chart(
        data, chart_type="stacked_bar", x="state", y="total", series="payment",
        title="By state", settings=_settings(tmp_path),
    )
    assert "error" not in result
    assert Path(result["chart_path"]).exists()


def test_create_chart_folds_series_past_eight(tmp_path):
    data = [{"cat": "X", "series": f"s{i}", "val": i + 1} for i in range(12)]
    result = create_chart(
        data, chart_type="bar", x="cat", y="val", series="series",
        title="Many series", settings=_settings(tmp_path),
    )
    assert "error" not in result  # must not crash with >8 series


def test_create_chart_empty_data_is_an_error(tmp_path):
    result = create_chart([], chart_type="bar", x="a", y="b", settings=_settings(tmp_path))
    assert "error" in result


def test_create_chart_unknown_type_is_an_error(tmp_path):
    data = [{"a": 1, "b": 2}]
    result = create_chart(data, chart_type="pie_3d", x="a", y="b", settings=_settings(tmp_path))
    assert "error" in result


def test_create_chart_missing_field_is_an_error(tmp_path):
    data = [{"a": 1, "b": 2}]
    result = create_chart(data, chart_type="bar", x="a", y="nope", settings=_settings(tmp_path))
    assert "error" in result


def test_compact_number_axis_labels():
    from bi_agent.charts import _compact_number

    assert _compact_number(1_441_248) == "1.4M"
    assert _compact_number(902_512) == "902.5K"
    assert _compact_number(1_000_000) == "1M"
    assert _compact_number(0) == "0"
    assert _compact_number(12.5) == "12.5"

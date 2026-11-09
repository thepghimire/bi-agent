"""
create_chart(): turn a query result (list of dicts) into a saved PNG.

Color and form choices here follow a validated design-system palette
(categorical hues ordered and checked for colorblind-safe adjacent
contrast; sequential = one hue, light "near zero" -> dark "high"; never a
dual axis; never more than ~8 categorical series without folding the tail
into "Other") rather than whatever matplotlib defaults to. See the
docstring on each constant for where its value comes from.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")  # headless -- this runs as a backend tool, not a notebook
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

from .config import Settings, get_settings

# -- Palette (validated: adjacent-pair colorblind-safe in fixed order; do
#    not reorder or cycle past 8 without re-validating) --------------------
CATEGORICAL = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
SEQUENTIAL_BLUE = "#2a78d6"  # single-hue default for magnitude-only (one series) charts
SURFACE = "#fcfcfb"
INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRIDLINE = "#e1e0d9"
BASELINE = "#c3c2b7"

MAX_SERIES = 8  # beyond this, fold the tail into "Other" rather than generate a 9th hue


class ChartError(ValueError):
    pass


def _slugify(text: str) -> str:
    keep = [c.lower() if c.isalnum() else "_" for c in text]
    slug = "".join(keep).strip("_")
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug or "chart"


def _style_axes(ax, horizontal_grid: bool = True) -> None:
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(BASELINE)
    ax.tick_params(colors=INK_MUTED, labelsize=9)
    if horizontal_grid:
        ax.yaxis.grid(True, color=GRIDLINE, linewidth=1, zorder=0)
        ax.xaxis.grid(False)
    ax.set_axisbelow(True)


def _compact_number(v: float, _pos=None) -> str:
    """Axis tick text: 1,441,248 -> '1.4M', 902,512 -> '903K', 12.5 -> '12.5'.
    Replaces matplotlib's default offset notation (a lone '1e6' in the corner)."""
    a = abs(v)
    for div, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if a >= div:
            out = f"{v / div:.1f}".rstrip("0").rstrip(".")
            return f"{out}{suffix}"
    return f"{v:g}"


def _thin_category_ticks(ax, n: int, max_labels: int = 12) -> None:
    """Long ordered axes (e.g. 24 months) get every k-th label so they don't collide."""
    if n <= max_labels:
        return
    step = -(-n // max_labels)  # ceil
    for i, label in enumerate(ax.get_xticklabels()):
        label.set_visible(i % step == 0)


def _fold_series(rows: list[dict], series_field: str, y_field: str) -> list[str]:
    """Return the series values to actually plot, folding anything past
    MAX_SERIES-1 into an 'Other' bucket (by total magnitude, ascending)."""
    totals: dict[str, float] = {}
    for r in rows:
        key = str(r.get(series_field))
        totals[key] = totals.get(key, 0.0) + float(r.get(y_field) or 0)
    ordered = sorted(totals, key=lambda k: totals[k], reverse=True)
    if len(ordered) <= MAX_SERIES:
        return ordered
    return ordered[: MAX_SERIES - 1] + ["Other"]


def create_chart(
    data: list[dict[str, Any]],
    chart_type: str,
    x: str,
    y: str,
    series: str | None = None,
    title: str = "",
    x_label: str | None = None,
    y_label: str | None = None,
    out_name: str | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """
    Args:
        data: rows from run_sql_query()['rows'] (or any list of dicts).
        chart_type: "bar" | "hbar" | "line" | "stacked_bar".
        x: field name for the category/time axis.
        y: field name for the numeric value.
        series: optional field name to split into multiple series
            (grouped for bar/line, stacked for stacked_bar). Capped at 8
            distinct values; the rest fold into "Other".
        title, x_label, y_label: chart text. x_label/y_label default to x/y.
        out_name: filename (without extension) under charts_dir. Defaults
            to a slug of the title.

    Returns a dict with 'chart_path' on success, or 'error' on failure --
    safe to call directly from an LLM tool-calling loop.
    """
    settings = settings or get_settings()
    if not data:
        return {"error": "No data to chart (empty result set)."}
    if chart_type not in ("bar", "hbar", "line", "stacked_bar"):
        return {"error": f"Unknown chart_type '{chart_type}'. Use bar, hbar, line, or stacked_bar."}
    if x not in data[0] or y not in data[0]:
        return {"error": f"Fields '{x}' and/or '{y}' not found in the data's columns: {list(data[0].keys())}"}

    try:
        settings.charts_dir.mkdir(parents=True, exist_ok=True)
        if series:
            fig = _render_multi_series(data, chart_type, x, y, series, title, x_label, y_label)
        else:
            fig = _render_single_series(data, chart_type, x, y, title, x_label, y_label)
    except Exception as exc:  # noqa: BLE001 -- surface as a tool error, not a crash
        return {"error": f"Failed to render chart: {exc}"}

    name = out_name or _slugify(title or f"{chart_type}_{x}_{y}")
    path = settings.charts_dir / f"{name}.png"
    fig.savefig(path, dpi=150, facecolor=SURFACE, bbox_inches="tight")
    plt.close(fig)
    return {"chart_path": str(path), "chart_type": chart_type}


def _render_single_series(data, chart_type, x, y, title, x_label, y_label):
    xs = [r[x] for r in data]
    ys = [float(r[y]) if r[y] is not None else 0.0 for r in data]
    label_values = len(xs) <= 10

    if chart_type == "hbar":
        height = max(3, 0.4 * len(xs))
        fig, ax = plt.subplots(figsize=(8, height))
        bars = ax.barh(xs, ys, color=SEQUENTIAL_BLUE)
        ax.invert_yaxis()
        _style_axes(ax, horizontal_grid=False)
        ax.xaxis.grid(True, color=GRIDLINE, linewidth=1, zorder=0)
        if label_values:
            for bar, v in zip(bars, ys):
                ax.text(
                    bar.get_width(), bar.get_y() + bar.get_height() / 2,
                    f" {v:,.0f}", va="center", ha="left", color=INK_SECONDARY, fontsize=8,
                )
    elif chart_type == "line":
        fig, ax = plt.subplots(figsize=(8, 5))
        ax.plot(xs, ys, color=SEQUENTIAL_BLUE, linewidth=2, marker="o", markersize=4)
        _style_axes(ax)
        plt.setp(ax.get_xticklabels(), rotation=30, ha="right")
        _thin_category_ticks(ax, len(xs))
    else:  # "bar" and "stacked_bar" with no series collapse to a plain bar
        fig, ax = plt.subplots(figsize=(8, 5))
        bars = ax.bar(xs, ys, color=SEQUENTIAL_BLUE)
        _style_axes(ax)
        plt.setp(ax.get_xticklabels(), rotation=30, ha="right")
        if label_values:
            for bar, v in zip(bars, ys):
                ax.text(
                    bar.get_x() + bar.get_width() / 2, bar.get_height(),
                    f"{v:,.0f}", va="bottom", ha="center", color=INK_SECONDARY, fontsize=8,
                )

    value_axis = ax.xaxis if chart_type == "hbar" else ax.yaxis
    value_axis.set_major_formatter(FuncFormatter(_compact_number))
    ax.set_title(title, color=INK_PRIMARY, fontsize=13, pad=12, loc="left")
    if chart_type == "hbar":
        # barh puts categories on the y-axis and the numeric field on the x-axis --
        # the opposite of every other chart_type here, so the label assignment flips too.
        ax.set_xlabel(y_label or y, color=INK_MUTED, fontsize=9)
        ax.set_ylabel(x_label or x, color=INK_MUTED, fontsize=9)
    else:
        ax.set_xlabel(x_label or x, color=INK_MUTED, fontsize=9)
        ax.set_ylabel(y_label or y, color=INK_MUTED, fontsize=9)
    fig.tight_layout()
    return fig


def _render_multi_series(data, chart_type, x, y, series, title, x_label, y_label):
    series_values = _fold_series(data, series, y)
    categories = sorted({r[x] for r in data}, key=lambda v: (v is None, v))

    # pivot: {series_value: {category: total_y}}
    pivot: dict[str, dict[Any, float]] = {sv: {c: 0.0 for c in categories} for sv in series_values}
    kept = set(series_values) - {"Other"}
    for r in data:
        key = str(r.get(series))
        bucket = key if key in kept else "Other"
        if bucket not in pivot:
            continue
        pivot[bucket][r[x]] = pivot[bucket].get(r[x], 0.0) + float(r.get(y) or 0)

    colors = dict(zip(series_values, CATEGORICAL))

    fig, ax = plt.subplots(figsize=(max(8, 0.6 * len(categories)), 5))

    if chart_type == "line":
        for sv in series_values:
            ys = [pivot[sv][c] for c in categories]
            ax.plot(categories, ys, label=sv, color=colors[sv], linewidth=2, marker="o", markersize=4)
    elif chart_type == "stacked_bar":
        bottoms = [0.0] * len(categories)
        for sv in series_values:
            ys = [pivot[sv][c] for c in categories]
            ax.bar(categories, ys, bottom=bottoms, label=sv, color=colors[sv],
                   edgecolor=SURFACE, linewidth=1)
            bottoms = [b + v for b, v in zip(bottoms, ys)]
    else:  # grouped "bar" or "hbar" collapse to grouped vertical bars
        n = len(series_values)
        width = 0.8 / max(n, 1)
        offsets = [(-0.4 + width / 2) + i * width for i in range(n)]
        x_idx = range(len(categories))
        for sv, off in zip(series_values, offsets):
            ys = [pivot[sv][c] for c in categories]
            ax.bar([i + off for i in x_idx], ys, width=width, label=sv, color=colors[sv])
        ax.set_xticks(list(x_idx))
        ax.set_xticklabels(categories)

    _style_axes(ax)
    plt.setp(ax.get_xticklabels(), rotation=30, ha="right")
    _thin_category_ticks(ax, len(categories))
    ax.yaxis.set_major_formatter(FuncFormatter(_compact_number))
    ax.set_title(title, color=INK_PRIMARY, fontsize=13, pad=12, loc="left")
    ax.set_xlabel(x_label or x, color=INK_MUTED, fontsize=9)
    ax.set_ylabel(y_label or y, color=INK_MUTED, fontsize=9)
    legend = ax.legend(frameon=False, fontsize=9, labelcolor=INK_SECONDARY)
    fig.tight_layout()
    return fig

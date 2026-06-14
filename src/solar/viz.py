"""Altair (Vega-Lite) charts. Each function returns a chart; `dashboard` composes
them and `save` writes a self-contained HTML file."""
from __future__ import annotations

from pathlib import Path

import altair as alt
import pandas as pd

from . import analyze

alt.data_transformers.enable("default", max_rows=100_000)

# Shared cell size so the dashboard tiles line up in a 2-column grid.
CELL_WIDTH = 520
CELL_HEIGHT = 280


def calendar_heatmap(daily: pd.DataFrame, year: int | None = None) -> alt.Chart:
    df = analyze.add_calendar(daily)
    if year is not None:
        df = df[df["year"] == year]
    df = df.assign(week=df["date"].dt.isocalendar().week.astype(int))
    return (
        alt.Chart(df)
        .mark_rect()
        .encode(
            x=alt.X("week:O", title="ISO week"),
            y=alt.Y("dow:O", title="day of week",
                    sort=[0, 1, 2, 3, 4, 5, 6]),
            color=alt.Color("kwh:Q", title="kWh", scale=alt.Scale(scheme="yelloworangered")),
            tooltip=["date:T", "kwh:Q"],
        )
        .properties(width=CELL_WIDTH, height=CELL_HEIGHT, title="Daily production")
    )


def monthly_bars(daily: pd.DataFrame) -> alt.Chart:
    m = analyze.monthly_totals(daily)
    return (
        alt.Chart(m)
        .mark_bar()
        .encode(
            x=alt.X("period:T", title="month"),
            y=alt.Y("kwh_total:Q", title="kWh"),
            tooltip=["period:T", "kwh_total:Q"],
        )
        .properties(width=CELL_WIDTH, height=CELL_HEIGHT, title="Monthly production")
    )


def rolling_line(daily: pd.DataFrame, window: int = 30) -> alt.Chart:
    r = analyze.rolling(daily, window)
    base = alt.Chart(r).encode(x=alt.X("date:T", title=None))
    pts = base.mark_circle(size=8, opacity=0.25).encode(y=alt.Y("kwh:Q", title="kWh/day"))
    line = base.mark_line(color="firebrick").encode(y=f"kwh_{window}d_avg:Q")
    return (pts + line).properties(
        width=CELL_WIDTH, height=CELL_HEIGHT, title=f"Daily kWh with {window}-day average"
    )


def daily_profile(intraday: pd.DataFrame) -> alt.Chart:
    prof = analyze.average_daily_profile(intraday)
    return (
        alt.Chart(prof)
        .mark_area(opacity=0.6, color="goldenrod")
        .encode(
            x=alt.X("hour:Q", title="hour of day", scale=alt.Scale(domain=[0, 24])),
            y=alt.Y("kwh:Q", title="avg kWh / 15-min"),
            tooltip=["hour:Q", "kwh:Q"],
        )
        .properties(width=CELL_WIDTH, height=CELL_HEIGHT, title="Average daily production profile")
    )


def dashboard(daily: pd.DataFrame, intraday: pd.DataFrame | None = None) -> alt.ConcatChart:
    charts = [calendar_heatmap(daily), monthly_bars(daily), rolling_line(daily)]
    if intraday is not None and not intraday.empty:
        charts.append(daily_profile(intraday))
    # Two-column grid: with all four charts this lays out as a 2x2 grid.
    return alt.concat(*charts, columns=2).resolve_scale(color="independent").properties(
        title="Home solar production"
    )


def save(chart: alt.TopLevelMixin, path: str | Path) -> Path:
    path = Path(path)
    chart.save(str(path))
    return path

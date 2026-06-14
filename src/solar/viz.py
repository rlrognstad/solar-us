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
            # Ordinal month bands give full-width bars and one tick per month,
            # instead of thin slivers on a continuous time axis.
            x=alt.X(
                "yearmonth(period):O",
                title="month",
                axis=alt.Axis(format="%b %Y", labelAngle=-45),
            ),
            y=alt.Y("kwh_total:Q", title="kWh"),
            tooltip=[alt.Tooltip("yearmonth(period):T", title="month"), "kwh_total:Q"],
        )
        .properties(width=CELL_WIDTH, height=CELL_HEIGHT, title="Monthly production")
    )


def rolling_line(daily: pd.DataFrame, window: int = 30) -> alt.Chart:
    r = analyze.rolling(daily, window)
    base = alt.Chart(r).encode(
        x=alt.X("date:T", title=None, axis=alt.Axis(format="%b %d", tickCount="month"))
    )
    pts = base.mark_circle(size=18, opacity=0.55, color="steelblue").encode(
        y=alt.Y("kwh:Q", title="kWh/day")
    )
    line = base.mark_line(color="firebrick").encode(y=f"kwh_{window}d_avg:Q")
    return (pts + line).properties(
        width=CELL_WIDTH, height=CELL_HEIGHT, title=f"Daily kWh with {window}-day average"
    )


def daily_profile(intraday: pd.DataFrame) -> alt.LayerChart:
    prof = analyze.average_daily_profile(intraday)

    raw = intraday.copy()
    raw["hour"] = (raw["ts"].dt.hour * 60 + raw["ts"].dt.minute) / 60.0

    x = alt.X("hour:Q", title="hour of day", scale=alt.Scale(domain=[0, 24]))
    y = alt.Y("kwh:Q", title="kWh / 15-min")

    # Every interval across all days as faint points...
    points = (
        alt.Chart(raw)
        .mark_circle(size=16, opacity=0.4, color="steelblue")
        .encode(x=x, y=y, tooltip=[alt.Tooltip("ts:T", title="time"), "kwh:Q"])
    )
    # ...with the mean profile drawn boldly on top.
    average = (
        alt.Chart(prof)
        .mark_line(color="goldenrod", strokeWidth=3)
        .encode(x=x, y=y, tooltip=["hour:Q", "kwh:Q"])
    )
    return (points + average).properties(
        width=CELL_WIDTH,
        height=CELL_HEIGHT,
        title="Daily production profile — actual (points) vs average (line)",
    )


def weather_fit(scored: pd.DataFrame) -> alt.LayerChart:
    """Actual vs weather-expected daily kWh, with a 1:1 reference line. Points below
    the line produced less than the weather predicted."""
    lim = float(max(scored["kwh"].max(), scored["expected_kwh"].max())) * 1.05
    ref = alt.Chart(pd.DataFrame({"v": [0, lim]})).mark_line(
        color="gray", strokeDash=[4, 4]
    ).encode(x="v:Q", y="v:Q")
    pts = (
        alt.Chart(scored)
        .mark_circle(size=40, opacity=0.6)
        .encode(
            x=alt.X("expected_kwh:Q", title="weather-expected kWh", scale=alt.Scale(domain=[0, lim])),
            y=alt.Y("kwh:Q", title="actual kWh", scale=alt.Scale(domain=[0, lim])),
            color=alt.Color(
                "residual_kwh:Q", title="residual",
                scale=alt.Scale(scheme="redblue", domainMid=0),
            ),
            tooltip=["date:T", "kwh:Q", "expected_kwh:Q", "residual_pct:Q"],
        )
    )
    return (ref + pts).properties(
        width=CELL_WIDTH, height=CELL_HEIGHT, title="Actual vs weather-expected"
    )


def weather_residual(scored: pd.DataFrame) -> alt.LayerChart:
    """Daily residual (actual - expected) over time; red bars are underperformance."""
    bars = (
        alt.Chart(scored)
        .mark_bar()
        .encode(
            x=alt.X("date:T", title=None, axis=alt.Axis(format="%b %d", tickCount="month")),
            y=alt.Y("residual_kwh:Q", title="kWh vs expected"),
            color=alt.condition(
                alt.datum.residual_kwh < 0, alt.value("indianred"), alt.value("seagreen")
            ),
            tooltip=["date:T", "kwh:Q", "expected_kwh:Q", "residual_kwh:Q", "residual_pct:Q"],
        )
    )
    zero = alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(color="black").encode(y="y:Q")
    return (bars + zero).properties(
        width=CELL_WIDTH, height=CELL_HEIGHT, title="Daily production residual vs weather"
    )


def energy_flows_daily(daily_bal: pd.DataFrame) -> alt.Chart:
    """Daily energy flows: self-consumed + exported above zero (= production),
    grid import drawn below zero."""
    long = pd.DataFrame(
        {
            "date": pd.concat([daily_bal["date"]] * 3, ignore_index=True),
            "flow": (
                ["self-consumed"] * len(daily_bal)
                + ["exported"] * len(daily_bal)
                + ["imported"] * len(daily_bal)
            ),
            "kwh": pd.concat(
                [daily_bal["self_kwh"], daily_bal["export_kwh"], -daily_bal["import_kwh"]],
                ignore_index=True,
            ),
        }
    )
    color = alt.Color(
        "flow:N",
        title=None,
        scale=alt.Scale(
            domain=["self-consumed", "exported", "imported"],
            range=["goldenrod", "steelblue", "indianred"],
        ),
    )
    return (
        alt.Chart(long)
        .mark_bar()
        .encode(
            x=alt.X(
                "monthdate(date):O",
                title=None,
                axis=alt.Axis(format="%b %d", labelAngle=-45),
            ),
            y=alt.Y("kwh:Q", title="kWh  (− = grid import)", stack="zero"),
            color=color,
            tooltip=["date:T", "flow:N", "kwh:Q"],
        )
        .properties(
            width=CELL_WIDTH, height=CELL_HEIGHT, title="Daily energy flows (solar use vs grid import)"
        )
    )


def balance_profile_chart(profile: pd.DataFrame) -> alt.LayerChart:
    """Average day: solar production (area) vs household load (line). The gap is
    import (load above solar) or export (solar above load)."""
    base = alt.Chart(profile).encode(
        x=alt.X("hour:Q", title="hour of day", scale=alt.Scale(domain=[0, 24]))
    )
    solar = base.mark_area(color="goldenrod", opacity=0.5).encode(
        y=alt.Y("solar_kwh:Q", title="avg kWh / 15-min")
    )
    load = base.mark_line(color="firebrick", strokeWidth=2).encode(y="load_kwh:Q")
    return (solar + load).properties(
        width=CELL_WIDTH, height=CELL_HEIGHT, title="Average day: solar (area) vs load (line)"
    )


def dashboard(
    daily: pd.DataFrame,
    intraday: pd.DataFrame | None = None,
    weather: pd.DataFrame | None = None,
    cons_intraday: pd.DataFrame | None = None,
) -> alt.ConcatChart:
    charts = [calendar_heatmap(daily), monthly_bars(daily), rolling_line(daily)]
    if intraday is not None and not intraday.empty:
        charts.append(daily_profile(intraday))
    if weather is not None and not weather.empty:
        try:
            scored, _ = analyze.weather_model(daily, weather)
            charts += [weather_fit(scored), weather_residual(scored)]
        except ValueError:
            pass  # not enough overlapping days yet — skip the weather panels
    if (
        intraday is not None and not intraday.empty
        and cons_intraday is not None and not cons_intraday.empty
    ):
        balance = analyze.energy_balance(intraday, cons_intraday)
        if not balance.empty:
            charts += [
                energy_flows_daily(analyze.daily_balance(balance)),
                balance_profile_chart(analyze.balance_profile(balance)),
            ]
    # Two-column grid.
    return alt.concat(*charts, columns=2).resolve_scale(color="independent").properties(
        title="Home solar production"
    )


def save(chart: alt.TopLevelMixin, path: str | Path) -> Path:
    path = Path(path)
    chart.save(str(path))
    return path

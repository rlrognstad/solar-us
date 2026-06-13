"""Analysis over the cached daily / intraday frames.

Everything here is source-agnostic: feed it the tidy frames from `ingest`
(or from a future local-Envoy adapter) and it works unchanged.
"""
from __future__ import annotations

import pandas as pd


def add_calendar(df: pd.DataFrame) -> pd.DataFrame:
    """Add year/month/dow columns to a daily frame."""
    out = df.copy()
    out["year"] = out["date"].dt.year
    out["month"] = out["date"].dt.month
    out["month_name"] = out["date"].dt.strftime("%b")
    out["dow"] = out["date"].dt.dayofweek
    return out


def monthly_totals(daily: pd.DataFrame) -> pd.DataFrame:
    out = (
        daily.assign(period=daily["date"].dt.to_period("M").dt.to_timestamp())
        .groupby("period", as_index=False)["kwh"]
        .sum()
        .rename(columns={"kwh": "kwh_total"})
    )
    return out


def rolling(daily: pd.DataFrame, window: int = 30) -> pd.DataFrame:
    out = daily.sort_values("date").copy()
    out[f"kwh_{window}d_avg"] = out["kwh"].rolling(window, min_periods=1).mean()
    return out


def capacity_factor(daily: pd.DataFrame, system_size_kw: float) -> pd.DataFrame:
    """Daily capacity factor = produced kWh / (size_kW * 24h). A rough but useful
    normalization for comparing days/seasons against nameplate."""
    out = daily.copy()
    out["capacity_factor"] = out["kwh"] / (system_size_kw * 24.0)
    return out


def best_worst(daily: pd.DataFrame, n: int = 5) -> dict[str, pd.DataFrame]:
    s = daily.sort_values("kwh", ascending=False)
    return {"best": s.head(n).reset_index(drop=True), "worst": s.tail(n).reset_index(drop=True)}


def average_daily_profile(intraday: pd.DataFrame) -> pd.DataFrame:
    """Mean production by time-of-day (the classic solar 'duck' arc)."""
    if intraday.empty:
        return intraday
    df = intraday.copy()
    df["minute_of_day"] = df["ts"].dt.hour * 60 + df["ts"].dt.minute
    prof = df.groupby("minute_of_day", as_index=False)["kwh"].mean()
    prof["hour"] = prof["minute_of_day"] / 60.0
    return prof

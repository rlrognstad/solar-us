"""Analysis over the cached daily / intraday frames.

Everything here is source-agnostic: feed it the tidy frames from `ingest`
(or from a future local-Envoy adapter) and it works unchanged.
"""
from __future__ import annotations

import numpy as np
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


def join_weather(daily: pd.DataFrame, weather: pd.DataFrame) -> pd.DataFrame:
    """Inner-join daily production with daily weather on date."""
    d = daily[["date", "kwh"]]
    w = weather[["date", "ghi_kwh_m2", "temp_c"]]
    return d.merge(w, on="date", how="inner").dropna(subset=["kwh", "ghi_kwh_m2"])


def weather_model(
    daily: pd.DataFrame, weather: pd.DataFrame, use_temp: bool = True, min_days: int = 20
) -> tuple[pd.DataFrame, dict]:
    """Regress daily production on irradiance (and optionally temperature) to get a
    weather-expected output per day. Returns (scored_frame, model_stats).

    The scored frame adds expected_kwh, residual_kwh (actual - expected), and
    residual_pct. A persistently negative residual is real underperformance
    (soiling, new shading, a fault) rather than just a cloudy stretch.
    """
    df = join_weather(daily, weather).sort_values("date").reset_index(drop=True)
    if len(df) < min_days:
        raise ValueError(
            f"Need at least {min_days} overlapping days to fit a weather model; "
            f"have {len(df)}. Fetch more daily/weather history first."
        )

    cols = ["ghi_kwh_m2"]
    if use_temp and df["temp_c"].notna().all():
        cols.append("temp_c")
    X = np.column_stack([np.ones(len(df)), *(df[c].to_numpy() for c in cols)])
    y = df["kwh"].to_numpy()
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)

    expected = X @ coef
    df["expected_kwh"] = expected
    df["residual_kwh"] = y - expected
    df["residual_pct"] = df["residual_kwh"] / df["expected_kwh"].replace(0, np.nan) * 100

    ss_res = float(((y - expected) ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum())
    stats = {
        "coef": dict(zip(["intercept", *cols], (round(float(c), 4) for c in coef))),
        "r2": 1 - ss_res / ss_tot if ss_tot > 0 else float("nan"),
        "rmse_kwh": (ss_res / len(df)) ** 0.5,
        "n_days": len(df),
        "used_temp": "temp_c" in cols,
    }
    return df, stats


def weather_anomalies(scored: pd.DataFrame, z: float = 2.0) -> pd.DataFrame:
    """Days whose residual is z standard deviations or more *below* expected —
    candidate fault/soiling/shading days, worst first."""
    resid = scored["residual_kwh"]
    sigma = resid.std(ddof=1)
    if not sigma or np.isnan(sigma):
        return scored.iloc[0:0].assign(resid_z=[])
    out = scored.assign(resid_z=(resid - resid.mean()) / sigma)
    return out[out["resid_z"] <= -z].sort_values("resid_z").reset_index(drop=True)


def energy_balance(prod_intraday: pd.DataFrame, cons_intraday: pd.DataFrame) -> pd.DataFrame:
    """Per-interval solar/load split from aligned 15-min production & consumption.

    Without a battery, at each interval: self-used = min(solar, load), the surplus
    is exported and the shortfall is imported. Interval resolution matters — daily
    totals would overstate self-use (you can't run the dishwasher at night on noon
    sun), so this joins on the timestamp, not the day.
    """
    p = prod_intraday[["ts", "kwh"]].rename(columns={"kwh": "solar_kwh"})
    c = cons_intraday[["ts", "kwh"]].rename(columns={"kwh": "load_kwh"})
    df = p.merge(c, on="ts", how="inner").dropna(subset=["solar_kwh", "load_kwh"])
    df["self_kwh"] = df[["solar_kwh", "load_kwh"]].min(axis=1)
    df["export_kwh"] = (df["solar_kwh"] - df["load_kwh"]).clip(lower=0)
    df["import_kwh"] = (df["load_kwh"] - df["solar_kwh"]).clip(lower=0)
    return df.sort_values("ts").reset_index(drop=True)


def daily_balance(balance: pd.DataFrame) -> pd.DataFrame:
    """Daily totals of the per-interval energy balance."""
    cols = ["solar_kwh", "load_kwh", "self_kwh", "export_kwh", "import_kwh"]
    out = (
        balance.groupby(balance["ts"].dt.normalize().rename("date"), as_index=False)[cols]
        .sum()
    )
    return out


def balance_summary(balance: pd.DataFrame) -> dict:
    """Headline ratios over the balance window.

    self-consumption = solar used on-site / solar produced (how much of your solar
    you keep). self-sufficiency = solar used on-site / load (how much of your usage
    solar covers). Both are 0-100%.
    """
    solar = float(balance["solar_kwh"].sum())
    load = float(balance["load_kwh"].sum())
    self_ = float(balance["self_kwh"].sum())
    return {
        "days": int(balance["ts"].dt.normalize().nunique()),
        "produced_kwh": solar,
        "consumed_kwh": load,
        "self_consumed_kwh": self_,
        "exported_kwh": float(balance["export_kwh"].sum()),
        "imported_kwh": float(balance["import_kwh"].sum()),
        "self_consumption_pct": 100 * self_ / solar if solar else float("nan"),
        "self_sufficiency_pct": 100 * self_ / load if load else float("nan"),
    }


def balance_profile(balance: pd.DataFrame) -> pd.DataFrame:
    """Average day: mean solar/load/import/export by time-of-day."""
    df = balance.copy()
    df["minute_of_day"] = df["ts"].dt.hour * 60 + df["ts"].dt.minute
    cols = ["solar_kwh", "load_kwh", "import_kwh", "export_kwh"]
    prof = df.groupby("minute_of_day", as_index=False)[cols].mean()
    prof["hour"] = prof["minute_of_day"] / 60.0
    return prof


def average_daily_profile(intraday: pd.DataFrame) -> pd.DataFrame:
    """Mean production by time-of-day (the classic solar 'duck' arc)."""
    if intraday.empty:
        return intraday
    df = intraday.copy()
    df["minute_of_day"] = df["ts"].dt.hour * 60 + df["ts"].dt.minute
    prof = df.groupby("minute_of_day", as_index=False)["kwh"].mean()
    prof["hour"] = prof["minute_of_day"] / 60.0
    return prof

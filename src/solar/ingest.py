"""Pull Enphase data into tidy pandas DataFrames and cache to parquet.

The cache is the source of truth for analysis, so you only spend API calls on
days you don't already have. `energy_lifetime` returns the full daily series in
a single call, which keeps you well under the free-plan budget.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from .client import EnphaseClient, EnphaseError

DAILY_PARQUET = "daily_production.parquet"
INTRADAY_PARQUET = "intraday_production.parquet"


def _energy_lifetime_to_frame(payload: dict) -> pd.DataFrame:
    start = pd.to_datetime(payload["start_date"])
    values = payload.get("production", [])
    dates = pd.date_range(start=start, periods=len(values), freq="D")
    df = pd.DataFrame({"date": dates, "wh": values})
    df["kwh"] = df["wh"] / 1000.0
    return df


def daily_production(client: EnphaseClient, system_id: str, refresh_tail_days: int = 3) -> pd.DataFrame:
    """Full daily-production series, cached. Refetches the last few days each run
    so late-reporting micros get corrected."""
    path = client.s.cache_dir / DAILY_PARQUET
    cached = pd.read_parquet(path) if path.exists() else None

    if cached is not None and not cached.empty:
        last = cached["date"].max().date()
        start = (last - dt.timedelta(days=refresh_tail_days)).isoformat()
        fresh = _energy_lifetime_to_frame(client.energy_lifetime(system_id, start_date=start))
        df = (
            pd.concat([cached[cached["date"].dt.date < pd.to_datetime(start).date()], fresh])
            .drop_duplicates("date", keep="last")
            .sort_values("date")
            .reset_index(drop=True)
        )
    else:
        df = _energy_lifetime_to_frame(client.energy_lifetime(system_id))

    df.to_parquet(path, index=False)
    return df


def _telemetry_to_frame(payload: dict) -> pd.DataFrame:
    rows = []
    for itv in payload.get("intervals", []):
        rows.append({"ts": dt.datetime.fromtimestamp(itv["end_at"]), "wh": itv.get("enwh")})
    df = pd.DataFrame(rows)
    if not df.empty:
        df["kwh"] = df["wh"] / 1000.0
    return df


def intraday_production(
    client: EnphaseClient, system_id: str, start: dt.date, end: dt.date
) -> pd.DataFrame:
    """15-min production telemetry over [start, end], chunked into 7-day windows
    to respect the per-request limit. Result is cached/merged."""
    path = client.s.cache_dir / INTRADAY_PARQUET
    cached = pd.read_parquet(path) if path.exists() else pd.DataFrame()

    frames = [cached] if not cached.empty else []
    window = start
    try:
        while window <= end:
            start_at = int(dt.datetime.combine(window, dt.time.min).timestamp())
            frames.append(_telemetry_to_frame(client.production_meter(system_id, start_at=start_at)))
            window += dt.timedelta(days=7)
    except EnphaseError:
        # Persist the chunks we already paid for before surfacing the failure,
        # so a mid-fetch rate limit doesn't waste successful calls.
        _merge_and_cache(frames, path)
        raise

    return _merge_and_cache(frames, path)


def _merge_and_cache(frames: list[pd.DataFrame], path) -> pd.DataFrame:
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if not df.empty:
        df = df.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
        df.to_parquet(path, index=False)
    return df

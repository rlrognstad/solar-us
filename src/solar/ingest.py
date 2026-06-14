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
DAILY_CONSUMPTION_PARQUET = "daily_consumption.parquet"
INTRADAY_CONSUMPTION_PARQUET = "intraday_consumption.parquet"


def _load(settings, filename: str) -> pd.DataFrame:
    path = settings.cache_dir / filename
    return pd.read_parquet(path) if path.exists() else pd.DataFrame()


def load_daily(settings) -> pd.DataFrame:
    """Cached daily production, no API call (empty if none yet)."""
    return _load(settings, DAILY_PARQUET)


def load_intraday(settings) -> pd.DataFrame:
    """Cached intraday production, no API call (empty if none yet)."""
    return _load(settings, INTRADAY_PARQUET)


def load_daily_consumption(settings) -> pd.DataFrame:
    """Cached daily consumption, no API call (empty if none yet)."""
    return _load(settings, DAILY_CONSUMPTION_PARQUET)


def load_intraday_consumption(settings) -> pd.DataFrame:
    """Cached intraday consumption, no API call (empty if none yet)."""
    return _load(settings, INTRADAY_CONSUMPTION_PARQUET)


def _lifetime_to_frame(payload: dict, value_key: str) -> pd.DataFrame:
    """Daily lifetime series (energy_lifetime / consumption_lifetime) -> frame."""
    start = pd.to_datetime(payload["start_date"])
    values = payload.get(value_key, [])
    dates = pd.date_range(start=start, periods=len(values), freq="D")
    df = pd.DataFrame({"date": dates, "wh": values})
    df["kwh"] = df["wh"] / 1000.0
    return df


def _daily_lifetime(
    client: EnphaseClient, path, fetch_fn, value_key: str, refresh_tail_days: int
) -> pd.DataFrame:
    """Cache a daily lifetime series, refetching only a recent tail each run so
    late-reporting values get corrected. `fetch_fn(start_date=...)` -> payload."""
    cached = pd.read_parquet(path) if path.exists() else None

    if cached is not None and not cached.empty:
        last = cached["date"].max().date()
        start = (last - dt.timedelta(days=refresh_tail_days)).isoformat()
        fresh = _lifetime_to_frame(fetch_fn(start_date=start), value_key)
        df = (
            pd.concat([cached[cached["date"].dt.date < pd.to_datetime(start).date()], fresh])
            .drop_duplicates("date", keep="last")
            .sort_values("date")
            .reset_index(drop=True)
        )
    else:
        df = _lifetime_to_frame(fetch_fn(), value_key)

    df.to_parquet(path, index=False)
    return df


def daily_production(client: EnphaseClient, system_id: str, refresh_tail_days: int = 3) -> pd.DataFrame:
    """Full daily-production series, cached (refetches a recent tail each run)."""
    return _daily_lifetime(
        client,
        client.s.cache_dir / DAILY_PARQUET,
        lambda **k: client.energy_lifetime(system_id, **k),
        "production",
        refresh_tail_days,
    )


def daily_consumption(client: EnphaseClient, system_id: str, refresh_tail_days: int = 3) -> pd.DataFrame:
    """Full daily-consumption series, cached (needs consumption CTs)."""
    return _daily_lifetime(
        client,
        client.s.cache_dir / DAILY_CONSUMPTION_PARQUET,
        lambda **k: client.consumption_lifetime(system_id, **k),
        "consumption",
        refresh_tail_days,
    )


# Different v4 telemetry endpoints name the per-interval energy field differently:
# production_meter uses `wh_del`, the microinverter feed uses `enwh`. Check both so
# the same frame builder works regardless of source.
_ENERGY_KEYS = ("wh_del", "enwh", "wh")


def _interval_wh(itv: dict) -> float | None:
    for key in _ENERGY_KEYS:
        if itv.get(key) is not None:
            return itv[key]
    return None


def _telemetry_to_frame(payload: dict) -> pd.DataFrame:
    rows = []
    for itv in payload.get("intervals", []):
        rows.append({"ts": dt.datetime.fromtimestamp(itv["end_at"]), "wh": _interval_wh(itv)})
    df = pd.DataFrame(rows)
    if not df.empty:
        df["kwh"] = df["wh"] / 1000.0
    return df


def _chunked_telemetry(fetch_fn, start: dt.date, end: dt.date, path) -> pd.DataFrame:
    """Pull 15-min telemetry over [start, end] in 7-day chunks (the per-request
    cap), merging into the cache. `fetch_fn(start_at=...)` -> telemetry payload.
    Persists chunks already fetched before re-raising on a mid-run failure."""
    cached = pd.read_parquet(path) if path.exists() else pd.DataFrame()
    frames = [cached] if not cached.empty else []
    window = start
    try:
        while window <= end:
            start_at = int(dt.datetime.combine(window, dt.time.min).timestamp())
            frames.append(_telemetry_to_frame(fetch_fn(start_at=start_at)))
            window += dt.timedelta(days=7)
    except EnphaseError:
        _merge_and_cache(frames, path)  # don't waste the calls we already paid for
        raise
    return _merge_and_cache(frames, path)


def intraday_production(
    client: EnphaseClient, system_id: str, start: dt.date, end: dt.date
) -> pd.DataFrame:
    """15-min production telemetry over [start, end], cached/merged."""
    return _chunked_telemetry(
        lambda **k: client.production_meter(system_id, granularity="week", **k),
        start,
        end,
        client.s.cache_dir / INTRADAY_PARQUET,
    )


def intraday_consumption(
    client: EnphaseClient, system_id: str, start: dt.date, end: dt.date
) -> pd.DataFrame:
    """15-min consumption telemetry over [start, end], cached/merged (needs CTs)."""
    return _chunked_telemetry(
        lambda **k: client.consumption_meter(system_id, granularity="week", **k),
        start,
        end,
        client.s.cache_dir / INTRADAY_CONSUMPTION_PARQUET,
    )


def _merge_and_cache(frames: list[pd.DataFrame], path) -> pd.DataFrame:
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if not df.empty:
        # cached frame is first, freshly-fetched chunks follow: keep="last" lets a
        # re-fetch overwrite stale rows (e.g. earlier nulls) for the same interval.
        df = df.drop_duplicates("ts", keep="last").sort_values("ts").reset_index(drop=True)
        df.to_parquet(path, index=False)
    return df

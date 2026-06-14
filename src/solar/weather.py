"""Open-Meteo irradiance adapter.

Pulls historical daily shortwave irradiance (GHI) and temperature for the array's
location and caches it to parquet, mirroring the ingest cache pattern. This feeds
the weather-normalized model in `analyze`: production is regressed on irradiance so
"underperformance" means a residual below what the weather predicts.

Open-Meteo's archive API is free and needs no key. It serves ERA5 reanalysis with a
few days' latency, so the most recent 1-5 days may be missing until they land.
Docs: https://open-meteo.com/en/docs/historical-weather-api
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import requests

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
WEATHER_PARQUET = "weather_daily.parquet"

MJ_TO_KWH = 1.0 / 3.6  # 1 MJ = 0.277… kWh; shortwave_radiation_sum is MJ/m²


def _archive_to_frame(payload: dict) -> pd.DataFrame:
    daily = payload.get("daily", {})
    df = pd.DataFrame(
        {
            "date": pd.to_datetime(daily.get("time", [])),
            "ghi_kwh_m2": pd.Series(daily.get("shortwave_radiation_sum", []), dtype="float64")
            * MJ_TO_KWH,
            "temp_c": pd.Series(daily.get("temperature_2m_mean", []), dtype="float64"),
        }
    )
    return df.dropna(subset=["ghi_kwh_m2"]).reset_index(drop=True)


def fetch_daily_weather(lat: float, lon: float, start: dt.date, end: dt.date) -> pd.DataFrame:
    """Daily GHI (kWh/m²) and mean temperature (°C) over [start, end]."""
    r = requests.get(
        ARCHIVE_URL,
        params={
            "latitude": lat,
            "longitude": lon,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "daily": "shortwave_radiation_sum,temperature_2m_mean",
            "timezone": "auto",
        },
        timeout=30,
    )
    if not r.ok:
        raise RuntimeError(f"Open-Meteo {r.status_code}: {r.text[:300]}")
    return _archive_to_frame(r.json())


def daily_weather(
    settings,
    lat: float,
    lon: float,
    start: dt.date,
    end: dt.date,
    refresh_tail_days: int = 7,
) -> pd.DataFrame:
    """Cached daily weather. Only fetches dates not already cached (plus a tail
    refresh, since recent reanalysis values get backfilled)."""
    path = settings.cache_dir / WEATHER_PARQUET
    cached = pd.read_parquet(path) if path.exists() else None

    if cached is not None and not cached.empty:
        last = cached["date"].max().date()
        fetch_start = max(start, last - dt.timedelta(days=refresh_tail_days))
    else:
        fetch_start = start

    if fetch_start <= end:
        fresh = fetch_daily_weather(lat, lon, fetch_start, end)
        frames = [cached, fresh] if cached is not None else [fresh]
        df = (
            pd.concat(frames, ignore_index=True)
            .drop_duplicates("date", keep="last")
            .sort_values("date")
            .reset_index(drop=True)
        )
        df.to_parquet(path, index=False)
    else:
        df = cached

    return df[(df["date"].dt.date >= start) & (df["date"].dt.date <= end)].reset_index(drop=True)


def load_weather(settings) -> pd.DataFrame:
    """Read the cached weather frame without hitting the network (empty if none)."""
    path = settings.cache_dir / WEATHER_PARQUET
    return pd.read_parquet(path) if path.exists() else pd.DataFrame()

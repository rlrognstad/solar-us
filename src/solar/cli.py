"""Command-line entry points (wired up in pyproject [project.scripts]).

  solar-authorize                 one-time OAuth: prints URL, takes the code
  solar-fetch systems             list systems and their ids
  solar-fetch daily               refresh the daily cache, print a summary
  solar-fetch intraday DAYS       pull last DAYS of 15-min telemetry
  solar-fetch weather             fetch/cache Open-Meteo irradiance for the daily span
  solar-fetch anomalies [Z]       list days underperforming the weather model (default 2σ)
  solar-fetch dashboard [OUT]     build the HTML dashboard (default: dashboard.html)
"""
from __future__ import annotations

import datetime as dt
import json
import sys

from . import analyze, auth, ingest, viz, weather
from .client import EnphaseClient
from .config import load_settings


def authorize() -> None:
    s = load_settings()
    print("1) Open this URL, sign in as the system owner, and approve:\n")
    print("   " + auth.authorize_url(s) + "\n")
    print("2) After approving you'll be redirected to a URL containing ?code=...")
    code = input("   Paste the code value here: ").strip()
    auth.exchange_code(s, code)
    print(f"\nTokens saved to {s.token_path}. You're ready to fetch.")


def _system_id(s, client: EnphaseClient) -> str:
    if s.system_id:
        return s.system_id
    systems = client.systems().get("systems", [])
    if len(systems) == 1:
        return str(systems[0]["system_id"])
    raise SystemExit("Set ENPHASE_SYSTEM_ID in .env (run `solar-fetch systems` to find it).")


def _resolve_location(s, client: EnphaseClient) -> tuple[float, float]:
    """Lat/lon from .env if set, else best-effort from Enphase, else a clear error."""
    if s.latitude is not None and s.longitude is not None:
        return s.latitude, s.longitude
    loc = client.system_location(s.system_id) if s.system_id else None
    if loc:
        return loc
    raise SystemExit(
        "No location available. Enphase didn't return coordinates for this plan — "
        "add SOLAR_LAT and SOLAR_LON to .env (your array's latitude/longitude)."
    )


def _print_anomalies(s, args: list[str]) -> None:
    daily = ingest.load_daily(s)
    wx = weather.load_weather(s)
    if daily.empty or wx.empty:
        raise SystemExit(
            "Need cached daily + weather. Run `solar-fetch daily` and `solar-fetch weather` first."
        )
    scored, stats = analyze.weather_model(daily, wx)
    print(
        f"weather model: R²={stats['r2']:.2f}  rmse={stats['rmse_kwh']:.1f} kWh  "
        f"n={stats['n_days']} days  coef={stats['coef']}"
    )
    z = float(args[1]) if len(args) > 1 else 2.0
    anom = analyze.weather_anomalies(scored, z=z)
    print(f"{len(anom)} day(s) at least {z:g}σ below weather-expected:")
    for _, r in anom.iterrows():
        print(
            f"  {r['date'].date()}  actual {r['kwh']:6.1f}  expected {r['expected_kwh']:6.1f}  "
            f"({r['residual_pct']:+.0f}%)  z={r['resid_z']:+.1f}"
        )


def fetch() -> None:
    args = sys.argv[1:]
    cmd = args[0] if args else "daily"
    s = load_settings()

    # Offline commands render from the local cache only — no API calls, no client.
    # Re-run `solar-fetch daily`/`intraday`/`weather` when you want fresh data.
    if cmd == "dashboard":
        out = args[1] if len(args) > 1 else "dashboard.html"
        daily = ingest.load_daily(s)
        if daily.empty:
            raise SystemExit("No cached daily data yet. Run `solar-fetch daily` first.")
        intraday = ingest.load_intraday(s)
        wx = weather.load_weather(s)
        chart = viz.dashboard(
            daily,
            intraday if not intraday.empty else None,
            wx if not wx.empty else None,
        )
        path = viz.save(chart, out)
        print(f"Wrote {path.resolve()}")
        return
    if cmd == "anomalies":
        _print_anomalies(s, args)
        return

    client = EnphaseClient(s)

    if cmd == "systems":
        print(json.dumps(client.systems(), indent=2))
        return

    sid = _system_id(s, client)

    if cmd == "daily":
        df = ingest.daily_production(client, sid)
        print(f"{len(df)} days cached, {df['date'].min().date()} -> {df['date'].max().date()}")
        print(f"lifetime: {df['kwh'].sum():,.0f} kWh   last 30d: {df['kwh'].tail(30).sum():,.0f} kWh")
    elif cmd == "intraday":
        days = int(args[1]) if len(args) > 1 else 7
        end = dt.date.today()
        df = ingest.intraday_production(client, sid, end - dt.timedelta(days=days), end)
        print(f"{len(df)} intervals cached through {df['ts'].max() if not df.empty else 'n/a'}")
    elif cmd == "weather":
        daily = ingest.load_daily(s)
        if daily.empty:
            raise SystemExit("No cached daily data yet. Run `solar-fetch daily` first.")
        lat, lon = _resolve_location(s, client)
        w = weather.daily_weather(
            s, lat, lon, daily["date"].min().date(), daily["date"].max().date()
        )
        print(
            f"{len(w)} weather days cached "
            f"({w['date'].min().date()} -> {w['date'].max().date()}) for ({lat:.4f}, {lon:.4f})"
        )
    else:
        raise SystemExit(__doc__)

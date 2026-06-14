"""Command-line entry points (wired up in pyproject [project.scripts]).

  solar-authorize                 one-time OAuth: prints URL, takes the code
  solar-fetch systems             list systems and their ids
  solar-fetch daily               refresh the daily cache, print a summary
  solar-fetch intraday DAYS       pull last DAYS of 15-min telemetry
  solar-fetch dashboard [OUT]     build the HTML dashboard (default: dashboard.html)
"""
from __future__ import annotations

import datetime as dt
import json
import sys

from . import auth, ingest, viz
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


def fetch() -> None:
    args = sys.argv[1:]
    cmd = args[0] if args else "daily"
    s = load_settings()

    # dashboard renders from the local cache only — no API calls, no client.
    # Re-run `solar-fetch daily`/`intraday` when you want fresh data.
    if cmd == "dashboard":
        out = args[1] if len(args) > 1 else "dashboard.html"
        daily = ingest.load_daily(s)
        if daily.empty:
            raise SystemExit("No cached daily data yet. Run `solar-fetch daily` first.")
        intraday = ingest.load_intraday(s)
        path = viz.save(viz.dashboard(daily, intraday if not intraday.empty else None), out)
        print(f"Wrote {path.resolve()}")
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
    else:
        raise SystemExit(__doc__)

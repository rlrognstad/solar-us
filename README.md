# solar-us

Analyze and visualize home solar production from the **Enphase Enlighten v4 API**.

The design keeps a local parquet cache as the source of truth, so you spend API
calls only on days you don't already have. The daily series comes back in a single
`energy_lifetime` call, which keeps you comfortably inside the free **Watt** plan
(1,000 calls/month).

```
src/solar/
  config.py    settings + paths (from .env)
  auth.py      OAuth2: authorize URL, code exchange, refresh, token storage
  client.py    v4 client (Bearer + api key, refresh-on-401), endpoint methods
  ingest.py    API JSON -> tidy DataFrames -> incremental parquet cache
  analyze.py   rollups, capacity factor, best/worst days, daily profile
  viz.py       Altair (Vega-Lite) charts + self-contained HTML dashboard
  cli.py       solar-authorize / solar-fetch entry points
```

## Setup

1. Create an app at https://developer-v4.enphase.com (Watt plan is free). You'll get
   an **API Key**, **Client ID**, and **Client Secret**.
2. Install and configure (uses [uv](https://docs.astral.sh/uv/)):
   ```bash
   uv sync                # creates .venv and installs deps from uv.lock
   cp .env.example .env   # then fill in your credentials
   ```
   Prefix the commands below with `uv run` (e.g. `uv run solar-authorize`), or
   activate the env first with `source .venv/bin/activate`.
3. Authorize (one time — the system owner must approve):
   ```bash
   solar-authorize
   ```
   This prints a URL; sign in, approve, and paste back the `code` from the redirect.
   Tokens are saved to `~/.local/share/solar/tokens.json` (0600).

## Use

```bash
solar-fetch systems          # find your system_id (put it in .env)
solar-fetch daily            # refresh daily cache + print a summary
solar-fetch intraday 14      # last 14 days of 15-min telemetry
solar-fetch dashboard        # write dashboard.html
```

Or drive it from a notebook / marimo:

```python
from solar import EnphaseClient, load_settings
from solar import ingest, analyze, viz

s = load_settings(); client = EnphaseClient(s)
daily = ingest.daily_production(client, s.system_id)
viz.save(viz.dashboard(daily), "dashboard.html")
```

## Notes on data sources

This uses the **cloud** v4 API. Endpoint granularity:

- `energy_lifetime` — daily Wh, unlimited date range, one call for the whole history.
- `telemetry/production_meter` — 15-min intervals, max 7 days per request (auto-chunked).
- `production_micro` / battery / consumption endpoints exist too; add methods in
  `client.py` following the same pattern.

If you later want **real-time, per-inverter** data with no rate limit, the local
IQ Gateway (Envoy) exposes `https://envoy.local/production.json` and
`/ivp/meters/readings` over your LAN with a bearer token. Only `ingest.py` would
need a new adapter that returns the same `date/wh/kwh` and `ts/wh/kwh` frames —
`analyze.py` and `viz.py` stay unchanged. The actively maintained `pyenphase`
library (used by Home Assistant) handles the local token/cert handshake.

Token TTLs and endpoint details are set by Enphase and have shifted over time;
verify against https://developer-v4.enphase.com/docs if anything misbehaves.
```

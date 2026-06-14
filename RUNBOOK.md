# solar-us runbook

Operational guide for running and maintaining the Enphase solar pipeline.
For architecture and design notes, see [README.md](README.md).

All commands assume uv is on PATH. uv installs to `~/.local/bin`, which may not be
on PATH by default — if `uv` is not found:

```bash
export PATH="$HOME/.local/bin:$PATH"   # add to ~/.bashrc to make it permanent
```

Run project commands with `uv run <cmd>` (uses the pinned .venv / Python 3.12), or
`source .venv/bin/activate` once per shell and drop the prefix.

---

## 1. First-time setup

```bash
uv sync                       # create .venv, install deps from uv.lock
cp .env.example .env          # then fill in credentials (see below)
```

Required in `.env` (get these by creating an app at https://developer-v4.enphase.com,
free "Watt" plan):

| Variable                 | Notes                                                |
|--------------------------|------------------------------------------------------|
| `ENPHASE_API_KEY`        | required                                             |
| `ENPHASE_CLIENT_ID`      | required                                             |
| `ENPHASE_CLIENT_SECRET`  | required                                             |
| `ENPHASE_REDIRECT_URI`   | optional; must match the app's registered URI        |
| `ENPHASE_SYSTEM_ID`      | optional; auto-detected if you own exactly one system|
| `SOLAR_DATA_DIR`         | optional; defaults to `~/.local/share/solar`         |

Then authorize once (the **system owner** must approve):

```bash
uv run solar-authorize
```

This prints a URL → sign in → approve → paste back the `code` from the redirect.
Tokens are saved to `~/.local/share/solar/tokens.json` (mode 0600).

Find your system id (only needed if you own more than one system):

```bash
uv run solar-fetch systems    # copy the system_id into ENPHASE_SYSTEM_ID
```

---

## 2. Routine operations

```bash
uv run solar-fetch meters           # one call: check whether consumption CTs report
uv run solar-fetch daily            # refresh daily cache, print lifetime + last-30d kWh
uv run solar-fetch intraday 14      # last 14 days of 15-min telemetry (default 7)
uv run solar-fetch weather          # cache Open-Meteo irradiance for the daily span
uv run solar-fetch anomalies        # list days >=2σ below the weather model (pass a Z to override)
uv run solar-fetch dashboard        # build dashboard.html (pass a path to override)
```

`weather`/`anomalies` use the free Open-Meteo API (no key, **does not** count against
the Enphase quota). `anomalies` and `dashboard` are offline — they read the cache and
make no network calls beyond the one-time `weather` fetch. Location comes from Enphase
when available, else `SOLAR_LAT`/`SOLAR_LON` in `.env`.

**Typical refresh:** run `daily` (one API call — `energy_lifetime` returns the full
history), then `dashboard` to regenerate the HTML. The parquet cache is the source of
truth, so repeat runs only spend calls on days not already cached.

`dashboard` makes **no API calls** — it renders entirely from the cache, so you can
rebuild it as often as you like (e.g. while tuning charts) without touching quota.
Run `daily`/`intraday` only when you want fresher data.

**Data locations** (under `SOLAR_DATA_DIR`, default `~/.local/share/solar`):
- `tokens.json` — OAuth tokens
- `cache/daily_production.parquet` — daily series
- `cache/intraday_production.parquet` — 15-min telemetry
- `cache/weather_daily.parquet` — Open-Meteo daily GHI + temperature

---

## 3. Automating a daily refresh

Example cron entry (adjust paths). Uses an absolute uv and the project dir so it works
without an interactive shell:

```cron
15 23 * * *  cd /home/rlrog/code/solar-us && /home/rlrog/.local/bin/uv run solar-fetch daily && /home/rlrog/.local/bin/uv run solar-fetch dashboard >> /home/rlrog/.local/share/solar/cron.log 2>&1
```

Stay within the free Watt plan (1,000 calls/month): `daily` is one call; `intraday N`
is one call per ~7-day chunk. A nightly `daily` + occasional `intraday` is well under budget.

---

## 4. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Missing required setting 'ENPHASE_...'` | `.env` missing or incomplete | Copy `.env.example` → `.env`, fill required keys |
| `No tokens found. Run solar-authorize first.` | Never authorized, or `tokens.json` deleted | `uv run solar-authorize` |
| `Set ENPHASE_SYSTEM_ID in .env ...` | You own >1 system | `uv run solar-fetch systems`, set `ENPHASE_SYSTEM_ID` |
| `Unable to find a usable engine ... pyarrow` | parquet engine missing | already fixed (pyarrow is a dep); re-run `uv sync` |
| `EnphaseError: 401 ...` persists | Refresh token expired/revoked | Re-run `uv run solar-authorize` to get fresh tokens |
| `EnphaseError: 429 ... plan quota exhausted` | Monthly call budget used up (Watt = 1,000/mo) | Wait for the monthly reset, fetch fewer days, or upgrade the plan |
| `uv: command not found` | uv not on PATH | `export PATH="$HOME/.local/bin:$PATH"` |

Notes:
- The client refreshes the access token automatically on a single 401 and retries.
  A *repeated* 401 means the refresh token itself is dead → re-authorize.
- A 429 *with* a `Retry-After` header (transient per-minute rate limit) is backed
  off and retried automatically; a 429 *without* one is treated as the monthly
  quota and fails fast. `intraday` persists the chunks it already fetched before a
  mid-run 429, so those calls aren't wasted — re-run later to fetch the rest.
- Token TTLs and endpoint details are set by Enphase and have shifted over time;
  verify against https://developer-v4.enphase.com/docs if something misbehaves.

---

## 5. Maintenance

```bash
uv sync                       # reinstall env to match uv.lock (after pulling changes)
uv add <package>              # add a dependency (updates pyproject.toml + uv.lock)
uv lock --upgrade             # bump locked versions, then commit uv.lock
```

- Commit `uv.lock` and `pyproject.toml` together; `.venv` is gitignored.
- To rebuild the cache from scratch, delete `~/.local/share/solar/cache/*.parquet`
  and re-run `solar-fetch daily` (and `intraday` if needed).

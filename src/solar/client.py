"""Thin client over the Enphase Enlighten v4 API.

Every request needs BOTH a Bearer access token (header) and the API key
(`key` query parameter). On a 401 we transparently refresh the access token
once and retry.
"""
from __future__ import annotations

import time
from typing import Any

import requests

from . import auth
from .config import API_BASE, Settings

# A 429 with a Retry-After header is a transient per-minute rate limit: wait and
# retry. A 429 without one is the plan's monthly quota — retrying won't help, so
# we fail fast with a clear message instead of burning the wait.
MAX_RATE_LIMIT_RETRIES = 3
MAX_RETRY_AFTER_SECONDS = 120


class EnphaseError(RuntimeError):
    pass


class EnphaseClient:
    def __init__(self, settings: Settings):
        self.s = settings
        self.tokens = auth.load_tokens(settings)
        if self.tokens is None:
            raise EnphaseError("No tokens found. Run `solar-authorize` first.")
        self._session = requests.Session()

    # ---- core request: refresh-on-401, backoff-on-429 ----------------------
    def _get(self, path: str, **params: Any) -> dict:
        params["key"] = self.s.api_key
        url = f"{API_BASE}{path}"
        refreshed = False
        for _ in range(MAX_RATE_LIMIT_RETRIES + 1):
            headers = {"Authorization": f"Bearer {self.tokens.access_token}"}
            r = self._session.get(url, params=params, headers=headers, timeout=30)

            if r.status_code == 401 and not refreshed:
                self.tokens = auth.refresh(self.s, self.tokens)
                refreshed = True
                continue

            if r.status_code == 429:
                retry_after = r.headers.get("Retry-After")
                if retry_after is None:
                    raise EnphaseError(
                        f"429 {path}: Enphase plan quota exhausted (free Watt plan is "
                        "1,000 calls/month). Wait for the monthly reset, fetch fewer "
                        f"days, or upgrade the plan. Server said: {r.text[:200]}"
                    )
                time.sleep(min(float(retry_after), MAX_RETRY_AFTER_SECONDS))
                continue

            if not r.ok:
                raise EnphaseError(f"{r.status_code} {path}: {r.text[:300]}")
            return r.json()

        raise EnphaseError(
            f"429 {path}: still rate-limited after {MAX_RATE_LIMIT_RETRIES} retries."
        )

    # ---- endpoints ---------------------------------------------------------
    def systems(self) -> dict:
        """List systems visible to the authorized user."""
        return self._get("/systems")

    def summary(self, system_id: str) -> dict:
        """Current/today summary: energy_today, energy_lifetime, system_size, status."""
        return self._get(f"/systems/{system_id}/summary")

    def energy_lifetime(
        self, system_id: str, start_date: str | None = None, end_date: str | None = None
    ) -> dict:
        """Daily produced energy (Wh). Dates are YYYY-MM-DD. No date-range cap."""
        params: dict[str, Any] = {}
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        return self._get(f"/systems/{system_id}/energy_lifetime", **params)

    def production_meter(self, system_id: str, start_at: int, granularity: str = "day") -> dict:
        """15-min production-meter telemetry. `start_at` is a unix timestamp.
        Max 7 days per request; start must be within ~2 years."""
        return self._get(
            f"/systems/{system_id}/telemetry/production_meter",
            start_at=start_at,
            granularity=granularity,
        )

    def consumption_lifetime(
        self, system_id: str, start_date: str | None = None, end_date: str | None = None
    ) -> dict:
        """Daily consumed energy (Wh), if a consumption meter is installed."""
        params: dict[str, Any] = {}
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        return self._get(f"/systems/{system_id}/consumption_lifetime", **params)

"""Thin client over the Enphase Enlighten v4 API.

Every request needs BOTH a Bearer access token (header) and the API key
(`key` query parameter). On a 401 we transparently refresh the access token
once and retry.
"""
from __future__ import annotations

from typing import Any

import requests

from . import auth
from .config import API_BASE, Settings


class EnphaseError(RuntimeError):
    pass


class EnphaseClient:
    def __init__(self, settings: Settings):
        self.s = settings
        self.tokens = auth.load_tokens(settings)
        if self.tokens is None:
            raise EnphaseError("No tokens found. Run `solar-authorize` first.")
        self._session = requests.Session()

    # ---- core request with one-shot refresh-on-401 -------------------------
    def _get(self, path: str, **params: Any) -> dict:
        params["key"] = self.s.api_key
        url = f"{API_BASE}{path}"
        headers = {"Authorization": f"Bearer {self.tokens.access_token}"}
        r = self._session.get(url, params=params, headers=headers, timeout=30)
        if r.status_code == 401:
            self.tokens = auth.refresh(self.s, self.tokens)
            headers = {"Authorization": f"Bearer {self.tokens.access_token}"}
            r = self._session.get(url, params=params, headers=headers, timeout=30)
        if not r.ok:
            raise EnphaseError(f"{r.status_code} {path}: {r.text[:300]}")
        return r.json()

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

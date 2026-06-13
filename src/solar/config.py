"""Configuration and paths, loaded from environment / .env."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

API_BASE = "https://api.enphaseenergy.com/api/v4"
OAUTH_AUTHORIZE = "https://api.enphaseenergy.com/oauth/authorize"
OAUTH_TOKEN = "https://api.enphaseenergy.com/oauth/token"


@dataclass(frozen=True)
class Settings:
    api_key: str
    client_id: str
    client_secret: str
    redirect_uri: str
    system_id: str | None
    data_dir: Path

    @property
    def token_path(self) -> Path:
        return self.data_dir / "tokens.json"

    @property
    def cache_dir(self) -> Path:
        d = self.data_dir / "cache"
        d.mkdir(parents=True, exist_ok=True)
        return d


def _require(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(
            f"Missing required setting {name!r}. Copy .env.example to .env and fill it in."
        )
    return val


def load_settings() -> Settings:
    data_dir = Path(os.path.expanduser(os.environ.get("SOLAR_DATA_DIR", "~/.local/share/solar")))
    data_dir.mkdir(parents=True, exist_ok=True)
    return Settings(
        api_key=_require("ENPHASE_API_KEY"),
        client_id=_require("ENPHASE_CLIENT_ID"),
        client_secret=_require("ENPHASE_CLIENT_SECRET"),
        redirect_uri=os.environ.get(
            "ENPHASE_REDIRECT_URI", "https://api.enphaseenergy.com/oauth/redirect_uri"
        ),
        system_id=os.environ.get("ENPHASE_SYSTEM_ID") or None,
        data_dir=data_dir,
    )

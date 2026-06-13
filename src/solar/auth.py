"""OAuth 2.0 token handling for the Enphase v4 API.

Flow (authorization code grant):
  1. Send the system owner to the authorize URL; they approve and you get a `code`.
  2. Exchange the code for access + refresh tokens (HTTP Basic = client_id:client_secret).
  3. Use the access token as a Bearer header; refresh it when it expires.

Token TTLs are set by Enphase and have changed over time, so this code does not
hardcode them: it refreshes reactively on a 401. Verify current behavior against
https://developer-v4.enphase.com/docs if you want proactive refresh.
"""
from __future__ import annotations

import base64
import json
import time
from dataclasses import asdict, dataclass
from urllib.parse import urlencode

import requests

from .config import OAUTH_AUTHORIZE, OAUTH_TOKEN, Settings


@dataclass
class Tokens:
    access_token: str
    refresh_token: str
    obtained_at: float

    @classmethod
    def from_response(cls, data: dict) -> "Tokens":
        return cls(
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            obtained_at=time.time(),
        )


def authorize_url(s: Settings) -> str:
    q = urlencode(
        {"response_type": "code", "client_id": s.client_id, "redirect_uri": s.redirect_uri}
    )
    return f"{OAUTH_AUTHORIZE}?{q}"


def _basic_auth_header(s: Settings) -> dict[str, str]:
    raw = f"{s.client_id}:{s.client_secret}".encode()
    return {"Authorization": "Basic " + base64.b64encode(raw).decode()}


def exchange_code(s: Settings, code: str) -> Tokens:
    params = {"grant_type": "authorization_code", "redirect_uri": s.redirect_uri, "code": code}
    r = requests.post(OAUTH_TOKEN, params=params, headers=_basic_auth_header(s), timeout=30)
    r.raise_for_status()
    tokens = Tokens.from_response(r.json())
    save_tokens(s, tokens)
    return tokens


def refresh(s: Settings, tokens: Tokens) -> Tokens:
    params = {"grant_type": "refresh_token", "refresh_token": tokens.refresh_token}
    r = requests.post(OAUTH_TOKEN, params=params, headers=_basic_auth_header(s), timeout=30)
    r.raise_for_status()
    new = Tokens.from_response(r.json())
    save_tokens(s, new)
    return new


def save_tokens(s: Settings, tokens: Tokens) -> None:
    s.token_path.parent.mkdir(parents=True, exist_ok=True)
    s.token_path.write_text(json.dumps(asdict(tokens), indent=2))
    s.token_path.chmod(0o600)


def load_tokens(s: Settings) -> Tokens | None:
    if not s.token_path.exists():
        return None
    return Tokens(**json.loads(s.token_path.read_text()))

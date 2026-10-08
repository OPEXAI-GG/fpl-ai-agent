"""FPL sign-in by refresh token, with rotation write-back.

FPL's sign-in is an OAuth2 flow; a browser session yields a long-lived refresh token that this
module exchanges for a short-lived access token. The refresh token *rotates*: every exchange
returns a new one and invalidates the old, so the new token is stored back in the repo secret
**before anything else happens**; if that fails the run aborts, because the next run would
otherwise hold a dead token.

Nothing here runs unless the agent is in live mode (see `execute.py`). The endpoint and client
id are configuration (`FPL_TOKEN_URL`, `FPL_CLIENT_ID`) because they must be copied from your own
browser session at setup; none of this has been run against the real service.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

import httpx

SecretWriter = Callable[[str, str], None]


class AuthError(Exception):
    pass


def gh_secret_writer(repo: str | None = None) -> SecretWriter:
    """Writer that stores a repo secret with the GitHub CLI (needs GH_TOKEN = a PAT that has
    'Secrets: read and write'; the workflow's own token cannot write secrets)."""

    def write(name: str, value: str) -> None:
        cmd = ["gh", "secret", "set", name]
        if repo:
            cmd += ["--repo", repo]
        res = subprocess.run(cmd, input=value, text=True, capture_output=True, timeout=60)
        if res.returncode != 0:
            raise AuthError(
                f"could not store the rotated refresh token: {res.stderr.strip()[:200]}"
            )

    return write


@dataclass
class Tokens:
    access: str
    refresh: str

    def __repr__(self) -> str:  # never print secrets
        return "Tokens(access=***, refresh=***)"


class TokenExchange:
    def __init__(self, refresh_token: str, token_url: str, client_id: str,
                 write_back: SecretWriter, secret_name: str = "FPL_REFRESH_TOKEN",
                 client: httpx.Client | None = None) -> None:  # fmt: skip
        if not (refresh_token and token_url and client_id):
            raise AuthError("refresh token, token URL and client id are all required")
        self._rt, self._url, self._cid = refresh_token, token_url, client_id
        self._write, self._name = write_back, secret_name
        self._http = client or httpx.Client(timeout=30)

    @classmethod
    def from_env(cls, write_back: SecretWriter | None = None) -> TokenExchange:
        env = os.environ.get
        return cls(
            env("FPL_REFRESH_TOKEN", ""),
            env("FPL_TOKEN_URL", ""),
            env("FPL_CLIENT_ID", ""),
            write_back or gh_secret_writer(),
        )

    def exchange(self) -> Tokens:
        try:
            r = self._http.post(self._url, data={
                "grant_type": "refresh_token", "refresh_token": self._rt,
                "client_id": self._cid})  # fmt: skip
            r.raise_for_status()
            body = r.json()
            tokens = Tokens(body["access_token"], body.get("refresh_token", self._rt))
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            raise AuthError(
                "FPL sign-in failed; paste a fresh refresh token into the FPL_REFRESH_TOKEN secret"
            ) from exc
        if tokens.refresh != self._rt:  # rotated: persist before anything else
            self._write(self._name, tokens.refresh)
            self._rt = tokens.refresh
        return tokens

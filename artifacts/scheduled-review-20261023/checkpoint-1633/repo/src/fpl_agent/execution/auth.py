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
from datetime import datetime

import httpx

SecretWriter = Callable[[str, str], None]


class AuthError(Exception):
    pass


def check_queued_token(client: httpx.Client, repository: str, run_id: str, token: str) -> None:
    """Under the shared Actions auth lease, reject a run queued before token rotation.

    Repository secrets are captured when queued. Serialization alone cannot make a queued
    token fresh. Only metadata is read here, never secret contents; uncertainty blocks auth.
    """
    import re

    if (
        not re.fullmatch(r"[\w.-]+/[\w.-]+", repository)
        or any(part in (".", "..") for part in repository.split("/"))
        or not run_id.isdigit()
        or not token
    ):
        raise AuthError("cannot verify queued token freshness: missing Actions identity")
    base = f"https://api.github.com/repos/{repository}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    try:
        run = client.get(f"{base}/actions/runs/{run_id}", headers=headers)
        run.raise_for_status()
        secret = client.get(f"{base}/actions/secrets/FPL_REFRESH_TOKEN", headers=headers)
        secret.raise_for_status()
        queued = datetime.fromisoformat(run.json()["created_at"].replace("Z", "+00:00"))
        rotated = datetime.fromisoformat(secret.json()["updated_at"].replace("Z", "+00:00"))
        if queued.tzinfo is None or rotated.tzinfo is None:
            raise ValueError("timestamps need timezone")
    except (httpx.HTTPError, KeyError, ValueError, TypeError, AttributeError) as exc:
        raise AuthError("cannot verify queued token freshness: metadata unavailable") from exc
    if rotated >= queued:
        raise AuthError("FPL token changed after this run was queued; start a new run")


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


def _why(r: httpx.Response) -> str:
    """The server's reason for refusing, from its standard error fields only (never a token)."""
    try:
        body = r.json()
        parts = [str(body.get(k, ""))[:80] for k in ("error", "error_description")]
    except ValueError:
        parts = []
    return f"HTTP {r.status_code} " + " ".join(p for p in parts if p).strip()


class TokenExchange:
    def __init__(self, refresh_token: str, token_url: str, client_id: str,
                 write_back: SecretWriter, secret_name: str = "FPL_REFRESH_TOKEN",
                 client: httpx.Client | None = None,
                 check_queue: bool = False) -> None:  # fmt: skip
        if not (refresh_token and token_url and client_id):
            raise AuthError("refresh token, token URL and client id are all required")
        self._rt, self._url, self._cid = refresh_token, token_url, client_id
        self._write, self._name = write_back, secret_name
        self._http = client or httpx.Client(timeout=30)
        self._check_queue = check_queue

    @classmethod
    def from_env(cls, write_back: SecretWriter | None = None) -> TokenExchange:
        env = os.environ.get
        return cls(
            env("FPL_REFRESH_TOKEN", ""),
            env("FPL_TOKEN_URL", ""),
            env("FPL_CLIENT_ID", ""),
            write_back or gh_secret_writer(),
            check_queue=env("FPL_AUTH_FRESHNESS_REQUIRED") == "true",
        )

    def exchange(self) -> Tokens:
        if self._check_queue:
            with httpx.Client(timeout=15) as metadata:
                check_queued_token(
                    metadata,
                    os.environ.get("GITHUB_REPOSITORY", ""),
                    os.environ.get("GITHUB_RUN_ID", ""),
                    os.environ.get("GH_TOKEN", ""),
                )
        why = ""
        try:
            r = self._http.post(self._url, data={
                "grant_type": "refresh_token", "refresh_token": self._rt,
                "client_id": self._cid})  # fmt: skip
            if r.is_error:
                why = _why(r)
            r.raise_for_status()
            body = r.json()
            tokens = Tokens(body["access_token"], body.get("refresh_token", self._rt))
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            raise AuthError(
                "FPL sign-in failed"
                + (f" ({why})" if why else f" ({type(exc).__name__})")
                + "; paste a fresh refresh token into the FPL_REFRESH_TOKEN secret"
            ) from exc
        if tokens.refresh != self._rt:  # rotated: persist before anything else
            self._write(self._name, tokens.refresh)
            self._rt = tokens.refresh
        return tokens

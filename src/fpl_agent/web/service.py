"""Shared service logic for the API and the worker: which gateway, revision and forecast a
request uses, and how plan and forecast jobs are keyed and queued."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fpl_agent.rules import Rules
from fpl_agent.web import forecasts, planning, snapshots
from fpl_agent.web.config import WebSettings
from fpl_agent.web.provider import Gateway, SampleGateway
from fpl_agent.web.store import Job, Store

SAMPLE = "sample"


class NoData(RuntimeError):
    """The service has no published data revision yet (message is user-facing)."""


@dataclass
class Service:
    settings: WebSettings
    store: Store
    gateway: Gateway

    @classmethod
    def create(cls, settings: WebSettings | None = None, gateway: Gateway | None = None) -> Service:
        s = settings or WebSettings.from_env()
        store = Store(s.store_path)
        return cls(s, store, gateway or Gateway(store))

    @property
    def rules(self) -> Rules:
        return Rules.nearest(self.settings.season)

    def gateway_for(self, entry: int | str) -> Gateway:
        return SampleGateway(self.settings.sample_dir) if entry == SAMPLE else self.gateway

    # ---- data and forecasts ----------------------------------------------------------------
    def revision(self) -> snapshots.Revision:
        rev = snapshots.current(self.store)
        if rev is None:
            raise NoData(
                "No football data has been published to the website yet. Run "
                "`fpl-agent web publish` (see docs/website.md)."
            )
        return rev

    def forecast_config(self, gw: int) -> dict:
        s = self.settings
        return forecasts.config_for(self.revision(), s.season, gw, s.horizon, s.samples)

    def forecast_for(self, gw: int) -> tuple[str, forecasts.Shared | None]:
        """(key, loaded forecast or None when it has not been built yet)."""
        return self.forecast_for_key(forecasts.key_of(self.forecast_config(gw)))

    def forecast_for_key(self, key: str) -> tuple[str, forecasts.Shared | None]:
        row = self.store.forecast(key)
        if row and (Path(row["path"]) / "manifest.json").exists():
            return key, forecasts.load(Path(row["path"]))
        return key, None

    def latest_forecast(self) -> forecasts.Shared | None:
        """The newest built forecast of the current revision (for browsing screens)."""
        rev = snapshots.current(self.store)
        row = self.store.latest_forecast(rev.id if rev else None)
        if row and (Path(row["path"]) / "manifest.json").exists():
            return forecasts.load(Path(row["path"]))
        return None

    def ensure_forecast(self, gw: int) -> tuple[str, forecasts.Shared | None, Job | None]:
        key, shared = self.forecast_for(gw)
        if shared is not None:
            return key, shared, None
        cfg = self.forecast_config(gw)
        job, _ = self.store.enqueue("forecast", key, {"config": cfg})
        return key, None, job

    # ---- plans -----------------------------------------------------------------------------
    def plan_request(
        self, entry: int | str, state: planning.PublicState, prefs: dict, session_id: str | None
    ) -> dict:
        """The cached plan for this squad state and preferences, or a queued job for it."""
        fkey, shared, fjob = self.ensure_forecast(state.gw)
        p = planning.prefs_of(prefs)
        key = planning.plan_key(fkey, planning.fingerprint(state.team), p)
        if entry == SAMPLE:
            key = "sample-" + key
        cached = self.store.result(key)
        if cached and cached["stage"] == "final":
            return {
                "status": "ready",
                "key": key,
                "result": cached["body"],
                "computed_at": cached["created_at"],
                "job": None,
            }
        job, created = self.store.enqueue(
            "plan",
            key,
            {
                "entry": entry,
                "prefs": p,
                "forecast_key": fkey,
                "fingerprint": planning.fingerprint(state.team),
            },
            session_id=session_id,
            entry_id=None if entry == SAMPLE else int(entry),
        )
        return {
            "status": job.status,
            "key": key,
            "created": created,
            "job": job.public(),
            "forecast_job": fjob.public() if fjob else None,
            "result": cached["body"] if cached else (job.result or None),
            "computed_at": cached["created_at"] if cached else None,
        }

    def plan_status_for(
        self, entry: int | str, state: planning.PublicState, prefs: dict
    ) -> dict | None:
        """The stored result and latest job for this squad state and preferences, if any."""
        fkey, shared = self.forecast_for(state.gw)
        key = planning.plan_key(fkey, planning.fingerprint(state.team), planning.prefs_of(prefs))
        if entry == SAMPLE:
            key = "sample-" + key
        st = self.plan_status(key)
        if st["result"] is None and st["job"] is None:
            return None
        status = st["job"]["status"] if st["job"] else "ready"
        if st["stage"] == "final":
            status = "ready"
        return {"status": status, "forecast_ready": shared is not None, **st}

    def plan_status(self, key: str) -> dict:
        cached = self.store.result(key)
        job = self.store.latest_job(key)
        return {
            "key": key,
            "result": cached["body"] if cached else None,
            "stage": cached["stage"] if cached else None,
            "computed_at": cached["created_at"] if cached else None,
            "job": job.public() if job else None,
        }


__all__ = ["Service", "NoData", "SAMPLE"]

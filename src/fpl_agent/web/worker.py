"""The background worker: forecasts and plans as persistent, bounded jobs.

Run one or more worker processes (`fpl-agent web worker`); each claims jobs atomically from
SQLite, so the number of processes is the concurrency limit. A plan job writes its preliminary
result as soon as the most likely free-transfer balance is planned, then the final one. A job
that exceeds its time budget ends as `timed_out` with its preliminary result kept and labelled;
it is never reported as a finished optimisation. A worker that dies mid-job stops sending
heartbeats and its job is requeued once, then failed.
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import time
import traceback

from fpl_agent.web import forecasts, planning
from fpl_agent.web.provider import EntryNotFound, ProviderUnavailable, RateLimited
from fpl_agent.web.service import SAMPLE, NoData, Service
from fpl_agent.web.store import Job

log = logging.getLogger(__name__)
STALE_AFTER = 1800.0  # seconds without a heartbeat before a running job counts as abandoned
IDLE_SLEEP = 1.0


def run_forecast(svc: Service, job: Job) -> dict:
    cfg = job.params["config"]
    rev = svc.revision()
    if rev.id != cfg["revision"]:
        raise NoData("the data revision changed while the forecast was queued")
    key, path, manifest = forecasts.build(
        rev,
        cfg,
        svc.settings.forecast_dir,
        progress=lambda stage, p: svc.store.progress(job.id, stage, p),
    )
    svc.store.put_forecast(key, rev.id, str(path), manifest)
    return {"forecast_key": key, "build_seconds": manifest.get("build_seconds")}


def run_plan(svc: Service, job: Job) -> dict | None:
    """The plan; None when it had to wait for its forecast (the job was requeued)."""
    entry = job.params["entry"]
    key, shared = svc.forecast_for_key(job.params["forecast_key"])
    if shared is None:
        fjob = svc.store.latest_job(job.params["forecast_key"])
        if fjob is None or fjob.status in ("failed", "timed_out"):
            raise NoData(
                "the shared forecast could not be built: "
                + (fjob.error if fjob and fjob.error else "no forecast job")
            )
        svc.store.requeue(job.id, "waiting for the shared forecast")
        return None
    gateway = svc.gateway_for(entry)
    state = planning.load_state(
        gateway, int(entry) if entry != SAMPLE else 0, svc.rules, svc.settings.season
    )
    if entry == SAMPLE:
        state.entry_id = 0

    def on_stage(stage: str, progress: float, body: dict) -> None:
        body = {**body, "sample": entry == SAMPLE}
        svc.store.progress(job.id, stage, progress, result=body)
        svc.store.put_result(
            job.dedup_key,
            "plan",
            body["stage"],
            body,
            entry_id=None if entry == SAMPLE else int(entry),
            job_id=job.id,
        )

    body = planning.run(
        shared,
        state,
        svc.rules,
        job.params["prefs"],
        gateway,
        on_stage=on_stage,
        budget=svc.settings.plan_budget_s,
    )
    body = {**body, "sample": entry == SAMPLE}
    svc.store.put_result(
        job.dedup_key,
        "plan",
        body["stage"],
        body,
        entry_id=None if entry == SAMPLE else int(entry),
        job_id=job.id,
    )
    return body


USER_ERRORS = (planning.PlanUnavailable, NoData, ProviderUnavailable, EntryNotFound, RateLimited)


def process(svc: Service, job: Job) -> str:
    """Run one claimed job to its end state; returns that state."""
    try:
        if job.kind == "forecast":
            svc.store.finish(job.id, "done", run_forecast(svc, job))
            return "done"
        body = run_plan(svc, job)
        if body is None:
            return "requeued"
        status = "timed_out" if body.get("timed_out") else "done"
        svc.store.finish(
            job.id,
            status,
            body,
            error="time budget reached: the result is preliminary"
            if status == "timed_out"
            else None,
        )
        return status
    except USER_ERRORS as exc:
        msg = str(exc) if not isinstance(exc, EntryNotFound) else "FPL has no team with that ID."
        svc.store.finish(job.id, "failed", error=msg)
        return "failed"
    except Exception as exc:  # noqa: BLE001  (a job failure never stops the worker)
        log.error("job %s failed: %s", job.id, traceback.format_exc())
        svc.store.finish(
            job.id, "failed", error=f"the engine could not finish this job ({type(exc).__name__})"
        )
        return "failed"


def loop(svc: Service, once: bool = False, max_jobs: int | None = None) -> int:
    name = f"{socket.gethostname()}:{os.getpid()}"
    stop = {"now": False}

    def _stop(*_):
        stop["now"] = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    done = 0
    while not stop["now"]:
        svc.store.recover_stale(STALE_AFTER)
        job = svc.store.claim(name)
        if job is None:
            if once:
                break
            time.sleep(IDLE_SLEEP)
            continue
        state = process(svc, job)
        log.info("job %s (%s): %s", job.id, job.kind, state)
        if state == "requeued":
            time.sleep(2.0)
            if once:
                break
            continue
        done += 1
        if max_jobs is not None and done >= max_jobs:
            break
    return done


__all__ = ["loop", "process", "run_plan", "run_forecast"]

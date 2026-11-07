"""`fpl-agent web ...`: operate the website service.

* `publish`: (optionally update the working database first, take the shared top-10k ownership
  sample,) copy the database into a new read-only revision with the FPL bootstrap and fixtures
  it matches, rebuild the labelled sample team and queue the shared forecast. This is the only
  command that writes the working database, and visitors cannot trigger it.
* `forecast`: build the shared forecast for the next gameweek now (instead of in the worker).
* `serve`: the HTTP API. `worker`: the background worker.
"""

from __future__ import annotations

import argparse
import logging
import time
from datetime import UTC


def add_parser(sub) -> None:
    w = sub.add_parser("web", help="website service: publish data, run the API and worker")
    w.add_argument(
        "action",
        choices=["publish", "forecast", "serve", "worker", "sample", "status", "challengers"],
    )
    w.add_argument("--update", action="store_true", help="publish: run db-update first")
    w.add_argument(
        "--ownership", action="store_true", help="publish: take the top-10k ownership sample"
    )
    w.add_argument(
        "--no-forecast", action="store_true", help="publish: do not queue the shared forecast"
    )
    w.add_argument("--host", default="127.0.0.1")
    w.add_argument("--port", type=int, default=8000)
    w.add_argument("--once", action="store_true", help="worker: stop when the queue is empty")


def run(args: argparse.Namespace) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    from fpl_agent.web.config import WebSettings

    s = WebSettings.from_env()
    if args.action == "serve":
        import uvicorn

        uvicorn.run(
            "fpl_agent.web.api:create_app",
            factory=True,
            host=args.host,
            port=args.port,
            log_level="info",
        )
        return 0
    from fpl_agent.web.service import Service

    svc = Service.create(s)
    if args.action == "worker":
        from fpl_agent.web.worker import loop

        n = loop(svc, once=args.once)
        print(f"worker stopped after {n} job(s)")
        return 0
    if args.action == "status":
        rev = svc.store.current_revision()
        fc = svc.latest_forecast()
        print(f"revision: {rev['id'] if rev else 'none'}")
        print(f"forecast: {fc.key + ' GW' + str(fc.proj.gws[0]) if fc else 'none'}")
        print(f"queue: {svc.store.queue_depth()} job(s)")
        return 0
    if args.action == "publish":
        return publish(svc, args)
    if args.action == "sample":
        return sample(svc)
    if args.action == "forecast":
        return forecast_now(svc)
    if args.action == "challengers":
        return challengers(svc)
    return 2


def publish(svc, args) -> int:
    from fpl_agent.db.database import FootballDB
    from fpl_agent.web import snapshots

    s = svc.settings
    if args.update:
        from fpl_agent.pipeline import UpdateOptions, run_update

        db = FootballDB(s.source_db)
        try:
            res = run_update(db, UpdateOptions(season=s.season, points=True), alert=False)
            print(res.message())
        finally:
            db.close()
    boot = svc.gateway.bootstrap(force=True)
    fx = svc.gateway.fixtures()
    if boot.stale:
        print("FPL bootstrap could not be refreshed; refusing to publish with a stale copy")
        return 2
    if args.ownership:
        from fpl_agent.data import ownership
        from fpl_agent.data.fpl_client import FPLClient

        nxt = next((e["id"] for e in boot.body["events"] if e.get("is_next")), None)
        db = FootballDB(s.source_db)
        try:
            df = ownership.field_ownership(db, FPLClient(), s.season, nxt) if nxt else None
            print(f"ownership sample: {0 if df is None else len(df)} players")
        finally:
            db.close()
    rev = snapshots.publish(s.source_db, s.data_dir, svc.store, s.season, boot.body, fx.body)
    print(f"published revision {rev.id} (cutoff {rev.meta['cutoff']})")
    sample(svc)
    if not args.no_forecast:
        nxt = next((e["id"] for e in boot.body["events"] if e.get("is_next")), None)
        if nxt:
            key, shared, job = svc.ensure_forecast(nxt)
            print(f"forecast {key}: " + ("ready" if shared else f"queued as job {job.id}"))
    return 0


def sample(svc) -> int:
    from fpl_agent.web import sample as sample_mod

    rev = svc.revision()
    boot, fx = rev.captured("bootstrap"), rev.captured("fixtures")
    if not boot or fx is None:
        print("the revision has no captured bootstrap/fixtures: sample data not built")
        return 2
    from datetime import datetime

    snap = rev.meta.get("fpl_snapshot_at") or rev.meta["cutoff"]
    captured = datetime.fromisoformat(snap).replace(tzinfo=UTC).timestamp()
    sample_mod.build(boot, fx, svc.settings.sample_dir, captured)  # dated by its data
    print(f"sample team written to {svc.settings.sample_dir}")
    return 0


def forecast_now(svc) -> int:
    from fpl_agent.web import forecasts

    rev = svc.revision()
    boot = rev.captured("bootstrap") or svc.gateway.bootstrap().body
    nxt = next((e["id"] for e in boot["events"] if e.get("is_next")), None)
    cfg = svc.forecast_config(nxt)
    t = time.time()
    key, path, manifest = forecasts.build(rev, cfg, svc.settings.forecast_dir)
    svc.store.put_forecast(key, rev.id, str(path), manifest)
    print(f"forecast {key} for GW{nxt} built in {time.time() - t:.0f}s")
    return 0


def challengers(svc) -> int:
    """Evaluate the shadow challengers on the current revision and store the reports, which the
    model page shows as backtests. Reads the revision only; changes no forecast or mode."""
    import json

    from fpl_agent.model import challengers as ch
    from fpl_agent.web import performance

    rev = svc.revision()
    db = rev.open()
    try:
        gws = [
            w["gw"]
            for w in performance.summary(db, svc.settings.season)["weeks"]
            if w.get("status") == "scored"
        ]
        reports = ch.run_all(db, svc.settings.season, gws)
    finally:
        db.close()
    out = svc.settings.eval_dir
    out.mkdir(parents=True, exist_ok=True)
    for r in reports:
        r["revision"] = rev.id
        (out / f"{r['name']}.json").write_text(json.dumps(r, indent=2, default=float))
        print(f"{r['title']}: {r['summary']}")
    return 0


__all__ = ["add_parser", "run"]

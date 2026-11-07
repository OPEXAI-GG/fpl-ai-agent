"""The website's HTTP API (FastAPI). Read-only with respect to FPL and the football database.

A visitor gets an anonymous session cookie. The session holds the selected entry and the
visitor's preferences; it is the only per-visitor state and it never authenticates ownership of
an FPL team. Expensive work (forecasts, plans) is queued for the worker; requests only read
cached public data, the shared forecast and stored results.
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import Annotated, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from fpl_agent.web import performance, planning, views
from fpl_agent.web.config import WebSettings
from fpl_agent.web.entry_ref import InvalidEntry, parse_entry
from fpl_agent.web.provider import EntryNotFound, Gateway, ProviderUnavailable, RateLimited
from fpl_agent.web.service import SAMPLE, NoData, Service

COOKIE = "fplsid"
COOKIE_AGE = 60 * 60 * 24 * 180


def session(request: Request, response: Response) -> dict:
    """The visitor's anonymous session, created (and its cookie set) on first use."""
    svc: Service = request.app.state.service
    sid = request.cookies.get(COOKIE)
    s = svc.store.session(sid)
    if s is None:
        sid = svc.store.new_session()
        s = svc.store.session(sid)
        response.set_cookie(
            COOKIE,
            sid,
            max_age=COOKIE_AGE,
            httponly=True,
            samesite="lax",
            secure=request.url.scheme == "https",
            path="/",
        )
    return s


Sess = Annotated[dict, Depends(session)]


class LookupBody(BaseModel):
    query: str = Field(max_length=200)


class PrefsBody(BaseModel):
    strategy: str | None = None
    target_rank: int | None = None
    timezone: str | None = Field(default=None, max_length=64)


def _err(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def create_app(settings: WebSettings | None = None, gateway: Gateway | None = None) -> FastAPI:
    svc = Service.create(settings, gateway)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield

    app = FastAPI(
        title="FPL AI Agent website API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.state.service = svc

    @app.exception_handler(NoData)
    async def _nodata(_r: Request, exc: NoData):
        return JSONResponse(
            status_code=503, content={"detail": {"code": "no_data", "message": str(exc)}}
        )

    @app.exception_handler(ProviderUnavailable)
    async def _provider(_r: Request, exc: ProviderUnavailable):
        return JSONResponse(
            status_code=503,
            content={"detail": {"code": "provider_unavailable", "message": str(exc)}},
        )

    @app.exception_handler(RateLimited)
    async def _limited(_r: Request, exc: RateLimited):
        return JSONResponse(
            status_code=429, content={"detail": {"code": "rate_limited", "message": str(exc)}}
        )

    # ---- session: see the module-level `session` dependency -----------------------------

    def entry_param(entry: str) -> int | str:
        if entry == SAMPLE:
            return SAMPLE
        try:
            return parse_entry(entry).entry_id
        except InvalidEntry as exc:
            raise _err(400, "invalid_entry", str(exc)) from exc

    def load_state(entry: int | str, force: bool = False) -> planning.PublicState:
        gw = svc.gateway_for(entry)
        try:
            st = planning.load_state(
                gw,
                0 if entry == SAMPLE else int(entry),
                svc.rules,
                svc.settings.season,
                force=force,
            )
        except EntryNotFound as exc:
            raise _err(404, "not_found", "FPL has no team with that ID.") from exc
        except planning.PlanUnavailable as exc:
            raise _err(409, "no_squad", str(exc)) from exc
        return st

    def session_view(s: dict) -> dict:
        return {
            "entry_id": s["entry_id"],
            "prefs": {**planning.prefs_of(s["prefs"]), "timezone": s["prefs"].get("timezone")},
            "team": s["prefs"].get("team"),
        }

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        rev = None
        try:
            r = svc.revision()
            rev = {
                "id": r.id,
                "cutoff": r.meta["cutoff"],
                "fpl_snapshot_at": r.meta.get("fpl_snapshot_at"),
                "snapshot_gws": r.meta.get("snapshot_gws"),
            }
        except NoData:
            pass
        fc = svc.latest_forecast()
        return {
            "ok": True,
            "time": time.time(),
            "revision": rev,
            "forecast": None
            if fc is None
            else {
                "key": fc.key,
                "gws": fc.proj.gws,
                "cutoff": fc.manifest["cutoff"],
                "created_at": fc.manifest["created_at"],
                "build_seconds": fc.manifest.get("build_seconds"),
            },
            "queue": {
                "plan": svc.store.queue_depth("plan"),
                "forecast": svc.store.queue_depth("forecast"),
            },
            "sample_ready": (svc.settings.sample_dir / "entry.json").exists(),
        }

    @app.get("/api/session")
    def get_session(s: Sess) -> dict:
        return session_view(s)

    @app.patch("/api/session")
    def patch_session(body: PrefsBody, s: Sess) -> dict:
        prefs: dict[str, Any] = {}
        if body.strategy is not None:
            if body.strategy not in planning.STRATEGIES:
                raise _err(400, "invalid_strategy", "Choose points, balanced or rank.")
            prefs["strategy"] = body.strategy
        if body.target_rank is not None:
            if body.target_rank not in planning.TARGETS:
                raise _err(400, "invalid_target", "Choose a target of 10k, 100k or 1M.")
            prefs["target_rank"] = body.target_rank
        if body.timezone is not None:
            try:
                ZoneInfo(body.timezone)
            except (ZoneInfoNotFoundError, ValueError) as exc:
                raise _err(400, "invalid_timezone", "Unknown timezone.") from exc
            prefs["timezone"] = body.timezone
        return session_view(svc.store.update_session(s["id"], prefs=prefs))

    @app.delete("/api/session")
    def reset_session(request: Request, response: Response) -> dict:
        sid = request.cookies.get(COOKIE)
        if sid:
            svc.store.delete_session(sid)
        response.delete_cookie(COOKIE, path="/")
        return {"reset": True}

    @app.post("/api/lookup")
    def lookup(body: LookupBody, s: Sess) -> dict:
        if not svc.store.hit(f"lookup:{s['id']}", svc.settings.lookups_per_session_minute, 60):
            raise _err(429, "rate_limited", "Too many lookups in a minute. Wait a moment.")
        try:
            ref = parse_entry(body.query)
        except InvalidEntry as exc:
            raise _err(400, "invalid_entry", str(exc)) from exc
        try:
            e = svc.gateway.entry(ref.entry_id)
        except EntryNotFound as exc:
            raise _err(
                404,
                "not_found",
                f"FPL has no team with ID {ref.entry_id}. Check the "
                "number on your team's Points page.",
            ) from exc
        m = views.manager(e.body)
        svc.store.update_session(
            s["id"],
            entry_id=ref.entry_id,
            prefs={"team": {"entry_id": ref.entry_id, "team_name": m["team_name"]}},
        )
        return {"entry_id": ref.entry_id, "source": ref.source, "manager": m, "fetched": e.meta()}

    # ---- entry screens ---------------------------------------------------------------------
    @app.get("/api/entries/{entry}/dashboard")
    def dashboard(entry: str, s: Sess, refresh: bool = False) -> dict:
        e = entry_param(entry)
        force = False
        if refresh and e != SAMPLE:
            force = svc.gateway.may_refresh(int(e), s["id"])
        st = load_state(e, force=force)
        fx = svc.gateway_for(e).fixtures().body
        try:
            _, shared = svc.forecast_for(st.gw)
        except NoData:
            shared = None
        out = views.dashboard(st, fx, shared or svc.latest_forecast())
        out["sample"] = e == SAMPLE
        out["refresh"] = {"requested": refresh, "applied": force}
        return out

    @app.post("/api/entries/{entry}/plan")
    def start_plan(entry: str, s: Sess) -> dict:
        e = entry_param(entry)
        st = load_state(e)
        if svc.store.queue_depth("plan") >= svc.settings.max_queue:
            raise _err(503, "busy", "The planner is busy. Try again in a few minutes.")
        pre = svc.plan_status_for(e, st, s["prefs"])
        if pre and pre.get("result") and pre.get("stage") == "final":
            return {"status": "ready", **pre}
        if pre and pre.get("job") and pre["job"]["status"] in ("queued", "running"):
            return {"status": pre["job"]["status"], **pre}
        if not svc.store.hit(f"plan:{s['id']}", svc.settings.plans_per_session_hour, 3600):
            raise _err(429, "rate_limited", "You've started many plans this hour. Try later.")
        out = svc.plan_request(e, st, s["prefs"], s["id"])
        return out

    @app.get("/api/entries/{entry}/plan")
    def get_plan(entry: str, s: Sess) -> dict:
        e = entry_param(entry)
        st = load_state(e)
        out = svc.plan_status_for(e, st, s["prefs"])
        return out or {"status": "none", "result": None, "job": None}

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str, s: Sess) -> dict:
        j = svc.store.job(job_id)
        if j is None or not (
            j.session_id == s["id"]
            or (j.entry_id and j.entry_id == s["entry_id"])
            or j.kind == "forecast"
        ):
            raise _err(404, "not_found", "No such job.")
        return j.public()

    # ---- shared screens --------------------------------------------------------------------
    def shared_or_503():
        fc = svc.latest_forecast()
        if fc is None:
            raise _err(503, "no_forecast", "The shared forecast is still being prepared.")
        return fc

    @app.get("/api/fixtures")
    def fixtures(sample: bool = False) -> dict:
        g = svc.gateway_for(SAMPLE if sample else 0)
        return views.fixtures_view(g.bootstrap().body, g.fixtures().body, svc.latest_forecast())

    @app.get("/api/players")
    def players(
        position: str | None = None,
        q: str | None = None,
        team: str | None = None,
        max_price: int | None = None,
        sort: str = "xp",
        limit: int = 60,
        ids: str | None = None,
    ) -> dict:
        if position and position not in ("GKP", "DEF", "MID", "FWD"):
            raise _err(400, "invalid_position", "Position must be GKP, DEF, MID or FWD.")
        id_list = None
        if ids:
            try:
                id_list = [int(x) for x in ids.split(",") if x.strip()][:6]
            except ValueError as exc:
                raise _err(400, "invalid_ids", "Player ids must be numbers.") from exc
        fc = shared_or_503()
        out = views.players_view(
            fc,
            position,
            (q or "")[:40] or None,
            team,
            max_price,
            sort,
            max(1, min(limit, 200)),
            id_list,
        )
        out["forecast"] = {
            "key": fc.key,
            "cutoff": fc.manifest["cutoff"],
            "created_at": fc.manifest["created_at"],
        }
        return out

    @app.get("/api/performance")
    def model_performance() -> dict:
        rev = svc.revision()
        key = f"perf-{rev.id}-v1"
        cached = svc.store.result(key)
        if cached:
            body = cached["body"]
        else:
            db = rev.open()
            try:
                body = performance.summary(db, svc.settings.season)
            finally:
                db.close()
            svc.store.put_result(key, "performance", "final", body)
        return {
            **body,
            "revision": rev.id,
            "cutoff": rev.meta["cutoff"],
            "backtests": performance.backtests(svc.settings.eval_dir),
        }

    return app


__all__ = ["create_app"]

import json
import re
import secrets
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.trustedhost import TrustedHostMiddleware

from crypto_bot.config import Settings
from crypto_bot.domain.clock import SystemClock
from crypto_bot.domain.models import ControlCommand
from crypto_bot.research.reports import HostingCost, build_report, repository_run
from crypto_bot.storage.repository import Repository
from crypto_bot.web.auth import Auth
from crypto_bot.web.views import build_dashboard_view


def create_app(settings: Settings, repository: Repository) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])
    root = Path(__file__).parent
    templates = Jinja2Templates(directory=root / "templates")
    app.mount("/static", StaticFiles(directory=root / "static"), name="static")
    auth = Auth(repository, settings.password_hash.get_secret_value(), SystemClock())

    @app.middleware("http")
    async def private_headers(request: Request, call_next: Any) -> Response:
        response: Response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'"
        )
        response.headers["Referrer-Policy"] = "same-origin"
        return response

    def safe_view() -> dict[str, Any]:
        view = build_dashboard_view(repository, settings)
        from crypto_bot.config import STRATEGY_HASH
        from crypto_bot.research.gates import EvidenceBundle, code_hash, evaluate_gates
        from crypto_bot.storage.repository import plain

        try:
            evidence = (
                EvidenceBundle.read(settings.evidence_path)
                if settings.evidence_path.exists()
                else EvidenceBundle(
                    {
                        "config": settings.config_hash,
                        "strategy": STRATEGY_HASH,
                        "code": code_hash(),
                        "data": "unobserved",
                    },
                    settings.initial_capital_usdt,
                )
            )
            view["gates"] = plain(evaluate_gates(evidence, settings).gates)
        except (ValueError, TypeError, KeyError):
            view["gates"] = [
                {"identity": f"G{i}", "status": "FAIL", "reasons": ["Evidence unreadable"]}
                for i in range(1, 8)
            ]
        worker = getattr(app.state, "worker", None)
        if worker:
            view["skip_reason"] = worker.last_reason
            view["stream_age_seconds"] = (
                (worker.clock.now_ms() - worker.market_heartbeat_ms) / 1000
                if getattr(worker, "market_heartbeat_ms", 0)
                else None
            )
            view["queue_lag_seconds"] = getattr(worker, "loop_lag_seconds", 0)
        serialized = json.dumps(view)
        for secret in (settings.api_key, settings.api_secret, settings.password_hash):
            value = secret.get_secret_value()
            if value:
                serialized = serialized.replace(value, "[REDACTED]")
        return json.loads(serialized)

    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request) -> Response:
        csrf = secrets.token_urlsafe(32)
        response = templates.TemplateResponse(request, "login.html", {"csrf": csrf})
        response.set_cookie(
            "cbot_login",
            csrf,
            httponly=True,
            samesite="strict",
            secure=settings.secure_cookies,
            max_age=900,
        )
        return response

    @app.post("/login")
    async def login(request: Request) -> Response:
        auth.check_origin(request)
        form = await request.form()
        csrf = str(form.get("csrf", ""))
        if not csrf or not secrets.compare_digest(csrf, request.cookies.get("cbot_login", "")):
            raise HTTPException(403, "Invalid login CSRF")
        if not auth.verify_password(
            str(form.get("password", "")), request.client.host if request.client else "unknown"
        ):
            raise HTTPException(401, "Invalid credentials")
        token, csrf = auth.create_session()
        response = RedirectResponse("/", status_code=303)
        for name, value in (("cbot_session", token), ("cbot_csrf", csrf)):
            response.set_cookie(
                name, value, httponly=True, samesite="strict", secure=settings.secure_cookies
            )
        response.delete_cookie("cbot_login")
        return response

    @app.post("/logout")
    async def logout(request: Request) -> Response:
        auth.check_origin(request)
        form = await request.form()
        row = auth.session(request, str(form.get("csrf", "")))
        repository.db.connection.execute(
            "UPDATE web_sessions SET revoked=1 WHERE token_hash=?", (row["token_hash"],)
        )
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie("cbot_session")
        response.delete_cookie("cbot_csrf")
        return response

    async def page(request: Request, template: str) -> Response:
        try:
            auth.session(request)
        except HTTPException:
            return RedirectResponse("/login", status_code=303)
        return templates.TemplateResponse(
            request, template, {"view": safe_view(), "csrf": request.cookies.get("cbot_csrf", "")}
        )

    @app.get("/", response_class=HTMLResponse)
    async def overview(request: Request) -> Response:
        return await page(request, "overview.html")

    @app.get("/trades", response_class=HTMLResponse)
    async def trades(request: Request) -> Response:
        return await page(request, "trades.html")

    @app.get("/diagnostics", response_class=HTMLResponse)
    async def diagnostics(request: Request) -> Response:
        return await page(request, "diagnostics.html")

    @app.get("/api/state")
    async def state(request: Request) -> Response:
        auth.session(request, touch=False)
        return JSONResponse(safe_view())

    @app.get("/health")
    async def health(request: Request) -> Response:
        auth.session(request, touch=False)
        view = safe_view()
        return JSONResponse(
            {
                k: view.get(k)
                for k in (
                    "state",
                    "reason",
                    "reconciled",
                    "heartbeat_age_seconds",
                    "account_age_seconds",
                    "stream_age_seconds",
                    "queue_lag_seconds",
                    "position",
                )
            }
        )

    @app.get("/report")
    async def report(request: Request) -> Response:
        auth.session(request)
        trial = repository.current_trial()
        if trial is None:
            raise HTTPException(404, "No run")
        result = build_report(
            repository_run(repository, trial.run_id, SystemClock().now_ms()),
            HostingCost(settings.hosting_monthly_usd, settings.usdt_per_usd),
        )
        return Response(
            result.json_text,
            media_type="application/json",
            headers={"Content-Disposition": 'attachment; filename="report.json"'},
        )

    @app.post("/commands/{action}")
    async def command(request: Request, action: str) -> Response:
        auth.check_origin(request)
        form = await request.form()
        auth.session(request, str(form.get("csrf", "")))
        if action not in {"pause", "resume", "close-and-pause", "acknowledge-fault"}:
            raise HTTPException(404)
        identity = str(form.get("request_id") or uuid.uuid4().hex)
        if not re.fullmatch(r"[a-f0-9]{32}", identity):
            raise HTTPException(400, "Invalid request identity")
        existing = repository.db.connection.execute(
            "SELECT action FROM control_commands WHERE request_id=?", (identity,)
        ).fetchone()
        if existing and existing[0] != action:
            raise HTTPException(409, "Request identity already used for another action")
        repository.enqueue_command(
            ControlCommand(identity, action, "authenticated-operator", SystemClock().now_ms())
        )
        return JSONResponse(
            {"request_id": identity, "state": "PENDING", "action": action}, status_code=202
        )

    return app

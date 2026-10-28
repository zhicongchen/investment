from __future__ import annotations

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api import router
from app.config import CORS_ORIGINS, DEFAULT_REFRESH_CRON_HOUR_ET, DEFAULT_REFRESH_CRON_MINUTE_ET
from app.db import SessionLocal, init_db
from app.providers.factory import market_data_provider
from app.services.refresh import refresh_all


def create_app() -> FastAPI:
    init_db()
    app = FastAPI(title="Local Stock Tracking Dashboard")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def allow_private_network(request: Request, call_next):
        # Chrome asks before a public page (GitHub Pages) may call a local server.
        response = await call_next(request)
        if request.headers.get("access-control-request-private-network") == "true":
            response.headers["Access-Control-Allow-Private-Network"] = "true"
        return response

    app.include_router(router, prefix="/api")
    return app


app = create_app()
scheduler = BackgroundScheduler(timezone="US/Eastern")


def scheduled_refresh() -> None:
    db = SessionLocal()
    provider = market_data_provider()
    try:
        refresh_all(db, provider)
    finally:
        provider.close()
        db.close()


@app.on_event("startup")
def start_scheduler() -> None:
    if not scheduler.running:
        scheduler.add_job(
            scheduled_refresh,
            "cron",
            hour=DEFAULT_REFRESH_CRON_HOUR_ET,
            minute=DEFAULT_REFRESH_CRON_MINUTE_ET,
            id="daily_market_refresh",
            replace_existing=True,
        )
        scheduler.start()


@app.on_event("shutdown")
def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown()

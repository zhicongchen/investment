from __future__ import annotations

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import router
from app.config import DEFAULT_REFRESH_CRON_HOUR_ET, DEFAULT_REFRESH_CRON_MINUTE_ET
from app.db import SessionLocal, init_db
from app.providers.factory import market_data_provider
from app.services.refresh import refresh_all


def create_app() -> FastAPI:
    init_db()
    app = FastAPI(title="Local Stock Tracking Dashboard")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:5173",
            "http://localhost:5173",
            "http://127.0.0.1:5174",
            "http://localhost:5174",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router, prefix="/api")
    return app


app = create_app()
scheduler = BackgroundScheduler(timezone="US/Eastern")


def scheduled_refresh() -> None:
    db = SessionLocal()
    try:
        refresh_all(db, market_data_provider())
    finally:
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

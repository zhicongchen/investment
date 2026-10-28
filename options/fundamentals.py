"""Fundamental verdicts for put candidates, from the dashboard's financial analysis (backend/app).

    dashboard: analyze what is already stored in data/dashboard.db (no network; refresh the
               dashboard first). Tickers without stored statements get no verdict.
    live:      download prices and statements for the tickers into a temporary database first.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable, Optional

from options.edge import Underlying

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"


def fundamentals_lookup(mode: str) -> Optional[Callable[[list[str]], dict[str, Underlying]]]:
    if mode == "off":
        return None
    if str(BACKEND_DIR) not in sys.path:
        sys.path.append(str(BACKEND_DIR))

    def lookup(tickers: list[str]) -> dict[str, Underlying]:
        # Imported on first use: the backend needs SQLAlchemy, which a Moomoo-only install may lack.
        from app.analyze import open_session, refresh
        from app.models import Company
        from app.services.research import company_financial_analysis, statements_for

        db = open_session(save=mode == "dashboard")
        try:
            if mode == "live":
                failed = refresh(db, tickers)
                tickers = [ticker for ticker in tickers if ticker not in failed]
            views: dict[str, Underlying] = {}
            for ticker in tickers:
                if db.get(Company, ticker) is None or not statements_for(db, ticker):
                    continue
                views[ticker] = to_underlying(company_financial_analysis(db, ticker))
            return views
        finally:
            db.close()

    return lookup


def to_underlying(analysis: dict[str, Any]) -> Underlying:
    recommendation = analysis["recommendation"]
    valuation = analysis["valuation"]
    return Underlying(
        action=recommendation["action"],
        conviction=recommendation["conviction"],
        composite_score=analysis["composite_score"],
        fair_value=valuation["base_value"],
        buy_below=valuation["entry_price"],
    )

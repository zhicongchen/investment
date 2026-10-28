"""Run the financial analysis on live market data from the command line.

    cd backend
    python -m app.analyze AAPL MSFT NVDA                 # temporary in-memory database
    python -m app.analyze AAPL --save                    # also store the data in data/dashboard.db
    python -m app.analyze AAPL MSFT --markdown report.md --json report.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, SessionLocal, init_db
from app.models import Company
from app.providers.factory import market_data_provider
from app.services.analytics import BENCHMARK_TICKER
from app.services.ollama import _jsonable
from app.services.refresh import refresh_ticker
from app.services.research import company_financial_analysis


def _session(save: bool) -> Session:
    if save:
        init_db()
        return SessionLocal()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def refresh(db: Session, tickers: list[str]) -> dict[str, str]:
    """Fetch prices, fundamentals and statements for the tickers plus the SPY benchmark."""
    failed: dict[str, str] = {}
    provider = market_data_provider()
    try:
        for ticker in [*tickers, BENCHMARK_TICKER]:
            try:
                refresh_ticker(db, ticker, provider)
                db.commit()
            except Exception as exc:
                db.rollback()
                failed[ticker] = str(exc)
        if BENCHMARK_TICKER not in tickers:
            benchmark = db.get(Company, BENCHMARK_TICKER)
            if benchmark:
                benchmark.active = False
                db.commit()
    finally:
        provider.close()
    return failed


def _pct(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _money(value: Optional[float]) -> str:
    return "n/a" if value is None else f"${value:,.2f}"


def _num(value: Optional[float], digits: int = 1) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def _metric(analysis: dict[str, Any], key: str) -> Optional[float]:
    for pillar in analysis["pillars"]:
        for metric in pillar["metrics"]:
            if metric["key"] == key:
                return metric["value"]
    return None


def markdown_report(analyses: list[dict[str, Any]], failed: dict[str, str]) -> str:
    lines = [
        "# Financial Analysis",
        "",
        "| Ticker | Verdict | Score | Conviction | Price | Fair value (bear - bull) | Margin of safety | Buy below |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for analysis in analyses:
        recommendation, valuation = analysis["recommendation"], analysis["valuation"]
        lines.append(
            f"| {analysis['ticker']} | **{recommendation['action']}** | {_num(analysis['composite_score'], 0)} | {recommendation['conviction']} "
            f"| {_money(valuation['price'])} | {_money(valuation['base_value'])} ({_money(valuation['bear_value'])} - {_money(valuation['bull_value'])}) "
            f"| {_pct(valuation['margin_of_safety'])} | {_money(valuation['entry_price'])} |"
        )
    for ticker, error in failed.items():
        lines.append(f"| {ticker} | refresh failed | | | | {error[:80]} | | |")

    for analysis in analyses:
        recommendation, valuation = analysis["recommendation"], analysis["valuation"]
        pillars = " · ".join(f"{p['label']} {_num(p['score'], 0)}" for p in analysis["pillars"])
        piotroski = analysis["piotroski"]
        piotroski_text = f"{piotroski['score']} of {piotroski['max']}" if piotroski else "n/a"
        lines += [
            "",
            f"## {analysis['ticker']}: {recommendation['action']} ({recommendation['conviction']} conviction, {_num(analysis['composite_score'], 0)}/100)",
            "",
            recommendation["summary"],
            "",
            *[f"- {item}" for item in recommendation["rationale"]],
            *([f"- Timing: {recommendation['timing_note']}"] if recommendation["timing_note"] else []),
            "",
            f"**Pillars:** {pillars}",
            "",
            f"**Statements:** {analysis['statement_basis'] or 'none'} {analysis['statement_date'] or ''} · data coverage {_pct(analysis['data_coverage'])}",
            "",
            "| Metric | Value |",
            "|---|---|",
            f"| ROIC / WACC | {_pct(_metric(analysis, 'roic'))} / {_pct(valuation['discount_rate'])} |",
            f"| Operating margin / FCF margin | {_pct(_metric(analysis, 'operating_margin'))} / {_pct(_metric(analysis, 'fcf_margin'))} |",
            f"| Revenue CAGR 3y / EPS CAGR 3y | {_pct(_metric(analysis, 'revenue_cagr'))} / {_pct(_metric(analysis, 'eps_cagr'))} |",
            f"| Net debt / EBITDA | {_num(_metric(analysis, 'net_debt_to_ebitda'), 2)} |",
            f"| Altman Z / Piotroski F | {_num(analysis['altman_z'], 2)} / {piotroski_text} |",
            f"| P/E / forward P/E / FCF yield | {_num(_metric(analysis, 'pe'))} / {_num(_metric(analysis, 'forward_pe'))} / {_pct(_metric(analysis, 'fcf_yield'))} |",
            f"| DCF growth yrs 1-5 / growth priced in | {_pct(valuation['stage_one_growth'])} / {_pct(valuation['implied_growth'])} |",
            f"| Base cash flow | {_money(valuation['base_cash_flow'])} ({valuation['cash_flow_basis'] or 'n/a'}) |",
            f"| Rich above | {_money(valuation['trim_price'])} |",
            "",
            "**Strengths**",
            *[f"- {item}" for item in recommendation["strengths"] or ["None stand out"]],
            "",
            "**Concerns**",
            *[f"- {item}" for item in recommendation["concerns"] or ["None"]],
            "",
            "**What would change the view**",
            *[f"- {item}" for item in recommendation["triggers"] or ["n/a"]],
        ]
        if analysis["flags"]:
            lines += ["", "**Red flags**", *[f"- [{flag['severity']}] {flag['message']}" for flag in analysis["flags"]]]
        if analysis["warnings"]:
            lines += ["", *[f"> {warning}" for warning in analysis["warnings"]]]
    lines += ["", "_Educational decision support built from reported numbers only; not financial advice._", ""]
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Financial analysis and buy/hold verdict on live data.")
    parser.add_argument("tickers", nargs="+", help="US tickers, e.g. AAPL MSFT")
    parser.add_argument("--save", action="store_true", help="store fetched data in data/dashboard.db")
    parser.add_argument("--markdown", type=Path, help="write the markdown report to this file")
    parser.add_argument("--json", type=Path, help="write the full analyses as JSON to this file")
    args = parser.parse_args(argv)

    tickers = list(dict.fromkeys(ticker.upper().strip() for ticker in args.tickers))
    db = _session(args.save)
    try:
        failed = refresh(db, tickers)
        analyses = [company_financial_analysis(db, ticker) for ticker in tickers if ticker not in failed]
    finally:
        db.close()

    report = markdown_report(analyses, failed)
    print(report)
    if args.markdown:
        args.markdown.write_text(report, encoding="utf-8")
    if args.json:
        args.json.write_text(json.dumps(_jsonable(analyses), indent=2), encoding="utf-8")
    return 1 if failed and not analyses else 0


if __name__ == "__main__":
    sys.exit(main())

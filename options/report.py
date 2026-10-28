"""Render scan results as JSON (for the dashboard) and Markdown (for reading)."""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

from options.scanner import ScanResult

SCORE_LABELS = {
    "put_score": "Put score",
    "annualized_yield": "Ann. yield",
    "iv_rank": "IV rank",
    "iv_hv_ratio": "IV/HV",
    "blend": "Blend score",
}


def to_dict(result: ScanResult) -> dict[str, Any]:
    return _jsonable(
        {
            "generated_at": result.generated_at.isoformat(),
            "ranking_metric": result.ranking_metric,
            "universe": result.universe,
            "dips": [asdict(item) for item in result.dips],
            "candidates": [asdict(item) for item in result.candidates],
            "vols": {ticker: {**asdict(vol), "iv_hv_ratio": vol.iv_hv_ratio} for ticker, vol in result.vols.items()},
            "positions_status": result.positions_status,
            "positions": [asdict(item) for item in result.positions],
            "exposure": result.exposure,
            "rejections": {ticker: dict(counter) for ticker, counter in result.rejections.items()},
            "errors": result.errors,
            "warnings": result.warnings,
            "underlyings": {ticker: asdict(view) for ticker, view in result.underlyings.items()},
            "earnings": result.earnings,
        }
    )


def write_reports(result: ScanResult, directory: Path) -> dict[str, Path]:
    """Always rewrite latest.*; keep a timestamped copy only when the signals changed."""
    directory.mkdir(parents=True, exist_ok=True)
    data = to_dict(result)
    payload = json.dumps(data, indent=2)
    markdown = to_markdown(result)
    latest_json = directory / "latest.json"
    changed = _signature(data) != _previous_signature(latest_json)

    paths = {"latest_json": latest_json, "latest_markdown": directory / "latest.md"}
    if changed:
        stamp = result.generated_at.strftime("%Y%m%d_%H%M")
        paths["json"] = directory / f"scan_{stamp}.json"
        paths["markdown"] = directory / f"scan_{stamp}.md"
        paths["json"].write_text(payload, encoding="utf-8")
        paths["markdown"].write_text(markdown, encoding="utf-8")
    latest_json.write_text(payload, encoding="utf-8")
    paths["latest_markdown"].write_text(markdown, encoding="utf-8")
    return paths


def _signature(data: dict[str, Any]) -> tuple[Any, ...]:
    """What counts as a new signal: the ranked contracts and the action on each open position."""
    return (
        tuple(item["code"] for item in data.get("candidates", [])),
        tuple(
            (item["position"]["ticker"], item["position"]["expiry"], item["position"]["strike"], item["action"])
            for item in data.get("positions", [])
        ),
    )


def _previous_signature(path: Path) -> Optional[tuple[Any, ...]]:
    try:
        return _signature(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def to_markdown(result: ScanResult) -> str:
    # Yield, IV rank and put score already have their own columns; only add a score column for the other metrics.
    score_label = (
        None if result.ranking_metric in {"annualized_yield", "iv_rank", "put_score"} else SCORE_LABELS.get(result.ranking_metric, "Score")
    )
    graded = any(item.grade for item in result.candidates)
    lines = [
        f"# Put-selling signals — {result.generated_at:%Y-%m-%d %H:%M} ET",
        "",
        f"Scanned **{len(result.universe)}** tickers, **{len(result.dipping)}** going down, "
        f"**{len(result.candidates)}** puts ranked by **{result.ranking_metric}**.",
        "",
        "_Research signals only. Not financial advice. Verify quotes before trading._",
        "",
    ]
    if result.warnings:
        lines += ["## Warnings", ""] + [f"- {item}" for item in result.warnings] + [""]

    lines += ["## Top puts to sell", ""]
    if result.candidates and graded:
        lines += _graded_table(result, score_label)
        lines += ["", "### Why these puts", ""]
        for index, item in enumerate(result.candidates[:8], 1):
            lines.append(f"**{index}. {item.ticker} {item.strike:g}P {item.expiry:%Y-%m-%d}** — grade {item.grade}, score {_num(item.put_score, 0)}")
            lines += [f"- ✔ {text}" for text in item.reasons] + [f"- ⚠ {text}" for text in item.cautions] + [""]
        lines += [
            "_Grade: A ≥ 75, B ≥ 62, C ≥ 50 on premium richness (IV vs realized), probability of profit, distance in "
            "expected moves, quality and price of the underlying, time to expiry (30-45 days best), liquidity; earnings "
            "before expiry or no fundamental check caps the grade at B. PoP is risk-neutral (from IV)._",
        ]
        if any(item.delta_estimated for item in result.candidates):
            lines.append("`*` delta estimated with Black-Scholes (no greeks from the data source).")
    elif result.candidates:
        lines += _table(
            ["#", "Ticker", "Expiry", "DTE", "Strike", "Spot", "OTM", "Bid/Ask", "Premium", "Delta", "IV", "IV rank", "Ann. yield", "Breakeven", "OI"]
            + ([score_label] if score_label else [])
            + ["Held"],
            [
                [
                    str(index),
                    item.ticker,
                    item.expiry.isoformat(),
                    str(item.dte),
                    f"{item.strike:g}",
                    f"{item.spot:.2f}",
                    _pct(item.otm_pct),
                    f"{item.bid:.2f}/{item.ask:.2f}",
                    f"{item.premium:.2f}",
                    (f"{item.delta:.2f}" + ("*" if item.delta_estimated else "")) if item.delta is not None else "-",
                    _pct(item.iv),
                    _num(item.iv_rank, 0),
                    _pct(item.annualized_yield),
                    f"{item.breakeven:.2f}",
                    _num(item.open_interest, 0),
                ]
                + ([_score(item.score, result.ranking_metric)] if score_label else [])
                + [_held(result, item.ticker)]
                for index, item in enumerate(result.candidates, 1)
            ],
        )
        if any(item.delta_estimated for item in result.candidates):
            lines.append("`*` delta estimated with Black-Scholes (no greeks from Moomoo).")
        if any(item.contracts is not None for item in result.candidates):
            lines += ["", "Suggested contracts at your collateral cap: " + ", ".join(
                f"{item.ticker} {item.strike:g}P {item.expiry:%m/%d} → {item.contracts}" for item in result.candidates if item.contracts is not None
            )]
    else:
        lines.append("No puts passed the filters.")
    lines.append("")

    lines += ["## Stocks going down", ""]
    if result.dipping:
        lines += _dip_table(result, result.dipping)
    else:
        lines.append("None of the watchlist met the dip rule.")
    lines.append("")

    lines += ["## Open short puts (IBKR)", "", f"_{result.positions_status}_", ""]
    if result.positions:
        lines += _table(
            ["Action", "Ticker", "Expiry", "DTE", "Strike", "Qty", "Credit", "Now", "Profit", "P&L", "Spot", "Delta", "Why"],
            [
                [
                    f"**{item.action}**",
                    item.position.ticker,
                    item.position.expiry.isoformat(),
                    str(item.dte),
                    f"{item.position.strike:g}",
                    str(-item.position.contracts),
                    f"{item.position.entry_credit:.2f}",
                    _num(item.option_mid),
                    _pct(item.profit_pct),
                    _money(item.unrealized_pnl),
                    _num(item.spot),
                    _num(item.delta),
                    "; ".join(item.reasons),
                ]
                for item in result.positions
            ],
        )
    lines.append("")

    watching = [item for item in result.dips if not item.is_dip]
    if watching:
        lines += ["## Not dipping (for reference)", ""] + _dip_table(result, watching) + [""]

    if result.errors:
        lines += ["## Errors", ""] + [f"- {ticker}: {message}" for ticker, message in sorted(result.errors.items())] + [""]
    no_candidates = {ticker: counter for ticker, counter in result.rejections.items() if not any(c.ticker == ticker for c in result.candidates)}
    if no_candidates:
        lines += ["## Why some dipping stocks have no puts", ""]
        for ticker, counter in sorted(no_candidates.items()):
            detail = ", ".join(f"{reason}: {count}" for reason, count in counter.most_common()) or "no contracts returned"
            lines.append(f"- {ticker}: {detail}")
        lines.append("")
    return "\n".join(lines)


def to_console(result: ScanResult) -> str:
    lines = [
        f"Put-selling scan {result.generated_at:%Y-%m-%d %H:%M} ET | universe {len(result.universe)} | "
        f"dipping {len(result.dipping)} | puts {len(result.candidates)} | rank by {result.ranking_metric}"
    ]
    for warning in result.warnings:
        lines.append(f"  ! {warning}")
    if result.candidates:
        lines.append(
            f"  {'GR':<5} {'TICKER':<6} {'EXPIRY':<10} {'DTE':>3} {'STRIKE':>8} {'SPOT':>8} {'OTM':>6} {'PREM':>6} "
            f"{'DELTA':>6} {'IV':>6} {'IVR':>4} {'YIELD':>7} {'POP':>6}"
        )
        for item in result.candidates:
            lines.append(
                f"  {item.grade or '-':<5} {item.ticker:<6} {item.expiry.isoformat():<10} {item.dte:>3} {item.strike:>8g} {item.spot:>8.2f} "
                f"{_pct(item.otm_pct):>6} {item.premium:>6.2f} {_num(item.delta):>6} {_pct(item.iv):>6} "
                f"{_num(item.iv_rank, 0):>4} {_pct(item.annualized_yield):>7} {_pct(item.prob_profit):>6}"
            )
    for item in result.positions:
        lines.append(
            f"  [{item.action}] {item.position.ticker} {item.position.strike:g}P {item.position.expiry} x{item.position.contracts} "
            f"profit {_pct(item.profit_pct)} — {'; '.join(item.reasons)}"
        )
    if result.errors:
        lines.append(f"  errors: {len(result.errors)} ticker(s), see report")
    return "\n".join(lines)


def _graded_table(result: ScanResult, score_label: Optional[str]) -> list[str]:
    return _table(
        ["#", "Grade", "Score", "Contract", "DTE", "Spot", "OTM", "Premium", "Ann. yield", "Delta", "PoP", "Exp. moves", "IV/RV", "Earnings", "Verdict", "BE vs fair"]
        + ([score_label] if score_label else [])
        + ["Held"],
        [
            [
                str(index),
                f"**{item.grade}**",
                _num(item.put_score, 0),
                f"{item.ticker} {item.strike:g}P {item.expiry:%m/%d}",
                str(item.dte),
                f"{item.spot:.2f}",
                _pct(item.otm_pct),
                f"{item.premium:.2f}",
                _pct(item.annualized_yield),
                (f"{item.delta:.2f}" + ("*" if item.delta_estimated else "")) if item.delta is not None else "-",
                _pct(item.prob_profit),
                _num(item.strike_sd, 1),
                _num(item.contract_iv_hv),
                (f"{item.earnings_date:%m/%d}" + (" ⚠" if item.earnings_in_trade else "")) if item.earnings_date else "-",
                item.verdict or "-",
                _pct(item.breakeven_discount),
            ]
            + ([_score(item.score, result.ranking_metric)] if score_label else [])
            + [_held(result, item.ticker)]
            for index, item in enumerate(result.candidates, 1)
        ],
    )


def _dip_table(result: ScanResult, rows: list[Any]) -> list[str]:
    return _table(
        ["Ticker", "Spot", "Today", "Off high", "N-day", "RSI", "IV", "IV rank", "IV/HV", "Signals"],
        [
            [
                item.ticker,
                f"{item.spot:.2f}",
                _pct(item.day_change_pct),
                _pct(item.off_high_pct),
                _pct(item.n_day_return_pct),
                _num(item.rsi, 0),
                _pct(result.vols[item.ticker].iv) if item.ticker in result.vols else "-",
                _num(result.vols[item.ticker].iv_rank, 0) if item.ticker in result.vols else "-",
                _num(result.vols[item.ticker].iv_hv_ratio) if item.ticker in result.vols else "-",
                "; ".join(item.triggers + item.blocked_by) or "-",
            ]
            for item in rows
        ],
    )


def _table(headers: list[str], rows: list[list[str]]) -> list[str]:
    return ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)] + ["| " + " | ".join(row) + " |" for row in rows]


def _held(result: ScanResult, ticker: str) -> str:
    exposure = result.exposure.get(ticker)
    return f"{int(exposure['contracts'])} ct" if exposure else "-"


def _score(value: float, metric: str) -> str:
    if metric == "annualized_yield":
        return _pct(value)
    return _num(value, 0 if metric in {"iv_rank", "blend", "put_score"} else 2)


def _pct(value: Optional[float]) -> str:
    return f"{value:.1%}" if value is not None else "-"


def _num(value: Optional[float], digits: int = 2) -> str:
    return f"{value:.{digits}f}" if value is not None else "-"


def _money(value: Optional[float]) -> str:
    return f"${value:,.0f}" if value is not None else "-"


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, float) and (value != value or value in {float("inf"), float("-inf")}):
        return None
    return value

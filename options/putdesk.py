"""Turn one CI scan's reports into the Put Desk dashboard's data files.

    python -m options.putdesk --analysis backend/analysis.json --scan reports/options/latest.json --out putdesk --print

Inputs are `python -m app.analyze --json` and the options scan's `latest.json`. Output is five JSON
files of row objects (verdicts, market, puts, filtered, scans). With `--print`, each file is also
written to stdout as one line, `PUTDESK <name> <json>`, so whatever reads the CI log can rebuild the
files exactly. Standard library only, so the CI step needs no dependencies.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
FILES = ("verdicts", "market", "puts", "filtered", "scans")
PILLARS = ("quality", "strength", "growth", "valuation", "momentum")


def build(analyses: list[dict[str, Any]], scan: Optional[dict[str, Any]], now: Optional[datetime] = None) -> dict[str, list[dict[str, Any]]]:
    now = now or datetime.now(timezone.utc)
    scan = scan or {}
    candidates = scan.get("candidates") or []
    return {
        "verdicts": [verdict_row(item) for item in analyses],
        "market": market_rows(scan),
        "puts": [put_row(item) for item in candidates],
        "filtered": filtered_rows(scan),
        "scans": scan_rows(scan, len(candidates), now, bool(analyses)),
    }


def verdict_row(analysis: dict[str, Any]) -> dict[str, Any]:
    recommendation = analysis.get("recommendation") or {}
    valuation = analysis.get("valuation") or {}
    metrics = {metric["key"]: metric.get("value") for pillar in analysis.get("pillars") or [] for metric in pillar.get("metrics") or []}
    scores = {pillar["key"]: _round(pillar.get("score"), 0) for pillar in analysis.get("pillars") or []}
    piotroski = analysis.get("piotroski")
    return {
        "ticker": analysis.get("ticker"),
        "verdict": recommendation.get("action"),
        "score": _round(analysis.get("composite_score"), 0),
        "conviction": recommendation.get("conviction"),
        "price": _round(valuation.get("price"), 2),
        "fair_value": _round(valuation.get("base_value"), 2),
        "bear": _round(valuation.get("bear_value"), 2),
        "bull": _round(valuation.get("bull_value"), 2),
        "margin_of_safety": _round(valuation.get("margin_of_safety"), 4),
        "buy_below": _round(valuation.get("entry_price"), 2),
        "roic": _round(metrics.get("roic"), 4),
        "wacc": _round(valuation.get("discount_rate"), 4),
        "op_margin": _round(metrics.get("operating_margin"), 4),
        "fcf_margin": _round(metrics.get("fcf_margin"), 4),
        "revenue_cagr": _round(metrics.get("revenue_cagr"), 4),
        "eps_cagr": _round(metrics.get("eps_cagr"), 4),
        "net_debt_ebitda": _round(metrics.get("net_debt_to_ebitda"), 2),
        "altman_z": _round(analysis.get("altman_z"), 2),
        "piotroski": f"{piotroski['score']} of {piotroski['max']}" if piotroski else None,
        "pe": _round(metrics.get("pe"), 1),
        "forward_pe": _round(metrics.get("forward_pe"), 1),
        "fcf_yield": _round(metrics.get("fcf_yield"), 4),
        "dcf_growth": _round(valuation.get("stage_one_growth"), 4),
        "growth_priced_in": _round(valuation.get("implied_growth"), 4),
        **{pillar: scores.get(pillar) for pillar in PILLARS},
        "statements_through": analysis.get("statement_date"),
        "fair_value_note": fair_value_note(valuation, metrics),
        "key_risk": key_risk(analysis),
    }


def fair_value_note(valuation: dict[str, Any], metrics: dict[str, Any]) -> str:
    """How far to trust the DCF fair value, from the model's own inputs."""
    if not valuation.get("applicable"):
        return "Not available: " + (valuation.get("note") or "the model could not value this company")
    margin = valuation.get("margin_of_safety")
    if margin is not None and margin < -5:
        return "Not meaningful: price is more than six times the model's value"
    if str(valuation.get("cash_flow_basis") or "").startswith("Normalized"):
        return "Low: cash flow normalized for heavy capital spending"
    fcf, operating = metrics.get("fcf_margin"), metrics.get("operating_margin")
    if fcf is not None and operating and operating > 0 and fcf < 0.4 * operating:
        return "Low: heavy capital spending depresses free cash flow"
    return "Usable"


def key_risk(analysis: dict[str, Any]) -> Optional[str]:
    flags = analysis.get("flags") or []
    for severity in ("high", "medium", "low"):
        for flag in flags:
            if flag.get("severity") == severity:
                return str(flag.get("message", "")).rstrip(".")
    concerns = (analysis.get("recommendation") or {}).get("concerns") or []
    return concerns[0] if concerns else None


def market_rows(scan: dict[str, Any]) -> list[dict[str, Any]]:
    vols = scan.get("vols") or {}
    earnings = scan.get("earnings") or {}
    rows = []
    for dip in scan.get("dips") or []:
        ticker = dip.get("ticker")
        vol = vols.get(ticker) or {}
        rows.append(
            {
                "ticker": ticker,
                "last_close": _round(dip.get("spot"), 2),
                "session_change": _round(dip.get("day_change_pct"), 4),
                "below_20d_high": _round(dip.get("off_high_pct"), 4),
                "change_5d": _round(dip.get("n_day_return_pct"), 4),
                "rsi": _round(dip.get("rsi"), 0),
                "atm_iv": _round(vol.get("iv"), 4),
                "iv_vs_realized": _round(vol.get("iv_hv_ratio"), 2),
                "dipping": "Yes" if dip.get("is_dip") else "No",
                "next_earnings": earnings.get(ticker),
            }
        )
    return rows


def put_row(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "contract": f"{item['ticker']} {float(item['strike']):g}P {item['expiry']}",
        "grade": item.get("grade"),
        "put_score": _round(item.get("put_score"), 1),
        "dte": item.get("dte"),
        "otm_pct": _round(item.get("otm_pct"), 4),
        "premium": _round(item.get("premium"), 2),
        "annualized_yield": _round(item.get("annualized_yield"), 4),
        "prob_profit": _round(item.get("prob_profit"), 4),
        "contract_iv_hv": _round(item.get("contract_iv_hv"), 2),
        "earnings_date": item.get("earnings_date"),
        "verdict": item.get("verdict"),
        "reasons": "\n".join(item.get("reasons") or []),
        "cautions": "\n".join(item.get("cautions") or []),
    }


def filtered_rows(scan: dict[str, Any]) -> list[dict[str, Any]]:
    """Rejection counts for tickers with no passing put, the way the scan report lists them."""
    passed = {item.get("ticker") for item in scan.get("candidates") or []}
    rows = []
    for ticker, reasons in sorted((scan.get("rejections") or {}).items()):
        if ticker in passed:
            continue
        for reason, count in sorted(reasons.items(), key=lambda pair: -pair[1]):
            rows.append({"ticker": ticker, "reason": reason[:1].upper() + reason[1:], "contracts": count})
    return rows


def scan_rows(scan: dict[str, Any], passed: int, now: datetime, analyzed: bool) -> list[dict[str, Any]]:
    rows = []
    generated = scan.get("generated_at")
    if generated:
        stamp = datetime.fromisoformat(generated)
        last_trade = any("last trade" in warning for warning in scan.get("warnings") or [])
        rows.append(
            {
                "scan": "Put scan",
                "ran_at": stamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                "session": session(stamp),
                "quotes": "Yahoo quotes delayed about 15 minutes" + ("; some contracts priced at their last trade" if last_trade else ""),
                "puts_passed": passed,
            }
        )
    if analyzed:
        rows.append(
            {
                "scan": "Financial analysis",
                "ran_at": now.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                "session": session(now),
                "quotes": "Delayed Yahoo price; statements as reported on Yahoo Finance",
                "puts_passed": None,
            }
        )
    return rows


def session(moment: datetime) -> str:
    """US equity session at a moment (exchange holidays are not modeled)."""
    local = moment.astimezone(ET)
    if local.weekday() >= 5:
        return "Market closed"
    minutes = local.hour * 60 + local.minute
    if minutes < 9 * 60 + 30:
        return "Pre-market"
    if minutes < 16 * 60:
        return "Market hours"
    return "After hours"


def _round(value: Any, digits: int) -> Any:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return value
    return round(value) if digits == 0 else round(value, digits)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build Put Desk data files from a scan's reports.")
    parser.add_argument("--analysis", type=Path, help="python -m app.analyze --json output")
    parser.add_argument("--scan", type=Path, help="options scan latest.json")
    parser.add_argument("--out", type=Path, required=True, help="directory for the five JSON files")
    parser.add_argument("--print", action="store_true", help="also print each file as a PUTDESK line")
    args = parser.parse_args(argv)

    analyses = _load(args.analysis) or []
    scan = _load(args.scan)
    if not analyses and not scan:
        print("No analysis or scan report found; nothing to export.", file=sys.stderr)
        return 1
    files = build(analyses, scan)
    args.out.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        (args.out / f"{name}.json").write_text(json.dumps(files[name], indent=1), encoding="utf-8")
        if args.print:
            print(f"PUTDESK {name} {json.dumps(files[name], separators=(',', ':'))}")
    return 0


def _load(path: Optional[Path]) -> Any:
    if path is None or not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import json
from datetime import datetime, timezone

from options.putdesk import build, fair_value_note, main, session

NOW = datetime(2026, 10, 29, 19, 56, tzinfo=timezone.utc)

ANALYSIS = {
    "ticker": "MSFT",
    "composite_score": 66.2,
    "statement_date": "2026-06-30",
    "altman_z": 9.264,
    "piotroski": {"score": 6, "max": 9},
    "recommendation": {"action": "WATCH", "conviction": "medium", "concerns": ["Margin of safety: -43.5%"]},
    "valuation": {
        "applicable": True,
        "price": 529.93,
        "base_value": 369.28,
        "bear_value": 192.76,
        "bull_value": 568.61,
        "margin_of_safety": -0.435,
        "entry_price": 295.43,
        "discount_rate": 0.09,
        "stage_one_growth": 0.185,
        "implied_growth": 0.253,
        "cash_flow_basis": "Average of TTM and 3-year free cash flow to firm",
    },
    "pillars": [
        {"key": "quality", "score": 79.4, "metrics": [{"key": "roic", "value": 0.277}, {"key": "operating_margin", "value": 0.468}, {"key": "fcf_margin", "value": 0.202}]},
        {"key": "strength", "score": 86, "metrics": [{"key": "net_debt_to_ebitda", "value": -0.09}]},
        {"key": "growth", "score": 82, "metrics": [{"key": "revenue_cagr", "value": 0.161}]},
        {"key": "valuation", "score": 31, "metrics": [{"key": "forward_pe", "value": 22.4}]},
        {"key": "momentum", "score": 50, "metrics": []},
    ],
    "flags": [{"severity": "medium", "message": "The price implies 25.3% annual cash-flow growth."}],
}

SCAN = {
    "generated_at": "2026-10-29T15:55:00-04:00",
    "dips": [
        {"ticker": "GOOGL", "spot": 283.83, "day_change_pct": -0.126, "off_high_pct": 0.21, "n_day_return_pct": -0.125, "rsi": 22.4, "is_dip": True},
        {"ticker": "INTC", "spot": 92.09, "day_change_pct": -0.036, "off_high_pct": 0.269, "n_day_return_pct": 0.077, "rsi": 41.0, "is_dip": True},
    ],
    "vols": {"GOOGL": {"iv": 0.307, "iv_hv_ratio": 0.69}},
    "earnings": {"GOOGL": None},
    "candidates": [
        {
            "ticker": "GOOGL", "strike": 265.0, "expiry": "2026-11-27", "grade": "C", "put_score": 54.04, "dte": 29,
            "otm_pct": 0.066, "premium": 2.83, "annualized_yield": 0.134, "prob_profit": 0.814, "contract_iv_hv": 0.70,
            "earnings_date": None, "verdict": "WATCH", "reasons": ["Selling into a pullback"], "cautions": ["IV 31% is below realized volatility 45%"],
        }
    ],
    "rejections": {"GOOGL": {"spread too wide": 3}, "INTC": {"delta out of range": 31, "fundamentals: avoid": 35}},
    "warnings": ["Market closed or quotes missing: some option prices are last trades, not live bid/ask."],
}


def test_build_maps_reports_to_dashboard_rows():
    files = build([ANALYSIS], SCAN, now=NOW)

    verdict = files["verdicts"][0]
    assert verdict["ticker"] == "MSFT" and verdict["verdict"] == "WATCH" and verdict["score"] == 66
    assert verdict["fair_value"] == 369.28 and verdict["buy_below"] == 295.43 and verdict["wacc"] == 0.09
    assert verdict["roic"] == 0.277 and verdict["piotroski"] == "6 of 9" and verdict["quality"] == 79
    assert verdict["fair_value_note"] == "Usable"
    assert verdict["key_risk"] == "The price implies 25.3% annual cash-flow growth"

    assert files["puts"] == [
        {
            "contract": "GOOGL 265P 2026-11-27", "grade": "C", "put_score": 54.0, "dte": 29, "otm_pct": 0.066, "premium": 2.83,
            "annualized_yield": 0.134, "prob_profit": 0.814, "contract_iv_hv": 0.7, "earnings_date": None, "verdict": "WATCH",
            "reasons": "Selling into a pullback", "cautions": "IV 31% is below realized volatility 45%",
        }
    ]
    market = {row["ticker"]: row for row in files["market"]}
    assert market["GOOGL"]["session_change"] == -0.126 and market["GOOGL"]["dipping"] == "Yes" and market["GOOGL"]["rsi"] == 22
    assert market["INTC"]["atm_iv"] is None

    # Tickers with a passing put are left out of the rejection chart, as in the scan report.
    assert files["filtered"] == [
        {"ticker": "INTC", "reason": "Fundamentals: avoid", "contracts": 35},
        {"ticker": "INTC", "reason": "Delta out of range", "contracts": 31},
    ]
    put_scan, analysis_scan = files["scans"]
    assert put_scan == {
        "scan": "Put scan", "ran_at": "2026-10-29T19:55:00Z", "session": "Market hours",
        "quotes": "Yahoo quotes delayed about 15 minutes; some contracts priced at their last trade", "puts_passed": 1,
    }
    assert analysis_scan["scan"] == "Financial analysis" and analysis_scan["ran_at"] == "2026-10-29T19:56:00Z"


def test_fair_value_note_flags_unreliable_models():
    assert fair_value_note({"applicable": True, "margin_of_safety": -90}, {}).startswith("Not meaningful")
    assert fair_value_note({"applicable": True, "margin_of_safety": -1, "cash_flow_basis": "Normalized: ..."}, {}).startswith("Low: cash flow normalized")
    assert fair_value_note({"applicable": True, "margin_of_safety": -1}, {"fcf_margin": 0.119, "operating_margin": 0.331}).startswith("Low: heavy capital")
    assert fair_value_note({"applicable": False, "note": "Free cash flow is negative."}, {}) == "Not available: Free cash flow is negative."


def test_session_labels():
    assert session(datetime(2026, 10, 29, 12, 27, tzinfo=timezone.utc)) == "Pre-market"
    assert session(datetime(2026, 10, 29, 19, 55, tzinfo=timezone.utc)) == "Market hours"
    assert session(datetime(2026, 10, 29, 21, 30, tzinfo=timezone.utc)) == "After hours"
    assert session(datetime(2026, 10, 31, 15, 0, tzinfo=timezone.utc)) == "Market closed"


def test_cli_writes_files_and_prints_one_line_per_file(tmp_path, capsys):
    analysis_path = tmp_path / "analysis.json"
    scan_path = tmp_path / "latest.json"
    analysis_path.write_text(json.dumps([ANALYSIS]))
    scan_path.write_text(json.dumps({**SCAN, "candidates": []}))

    assert main(["--analysis", str(analysis_path), "--scan", str(scan_path), "--out", str(tmp_path / "out"), "--print"]) == 0

    lines = [line for line in capsys.readouterr().out.splitlines() if line.startswith("PUTDESK ")]
    assert [line.split(" ", 2)[1] for line in lines] == ["verdicts", "market", "puts", "filtered", "scans"]
    printed = {line.split(" ", 2)[1]: json.loads(line.split(" ", 2)[2]) for line in lines}
    assert printed["puts"] == [] and printed["scans"][0]["puts_passed"] == 0
    assert json.loads((tmp_path / "out" / "verdicts.json").read_text()) == printed["verdicts"]


def test_cli_fails_without_reports(tmp_path):
    assert main(["--out", str(tmp_path / "out")]) == 1

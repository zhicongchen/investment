# Local Stock Tracking and Signal Dashboard

A local-only decision-support dashboard for long-term US stock investing, plus a separate guarded Python trading bot. The dashboard tracks a watchlist, fetches end-of-day data from Moomoo OpenD (falling back to `yfinance`), stores everything in SQLite, and generates transparent buy/hold/watch/sell-review signals.

Signals are educational decision support, not financial advice. The web dashboard does not connect to a broker and never places trades. Automated IBKR trading, if used, runs only through the standalone Python bot and is disabled by default.

## Put-Selling Signals (primary workflow)

The `options/` package scans your Moomoo watchlist for stocks that are going down, pulls their put chains from Moomoo OpenD, and ranks the richest out-of-the-money puts to sell. It also reads your open short puts from IBKR TWS (read-only) and says whether to take profit, roll or hold. It only produces signals; you place trades yourself.

Pipeline per scan:

1. **Universe**: US stocks/ETFs in your Moomoo watchlist groups (`watchlist.groups`, empty = all groups) plus `extra_tickers`.
2. **Going down**: daily bars + live snapshot → `% below N-day high`, `N-day return`, `RSI`, `today's move`. Pick which checks count in `dip.checks` and combine them with `dip.rule: any|all`. An optional `require_above_ma` filter skips falling knives.
3. **Puts**: OTM puts with 7–45 DTE for dipping stocks only, filtered by delta, OTM %, bid, spread and open interest. If Moomoo returns no greeks, delta/IV are estimated with Black-Scholes and marked `*`.
4. **Ranking** (`ranking.metric`): `annualized_yield` (premium ÷ strike × 365 ÷ DTE), `iv_rank` (from Moomoo's option overview), `iv_hv_ratio` (IV ÷ 30-day HV) or `blend` (weighted percentile of all three). Switch metrics in config to compare them.
5. **Open positions**: short puts from TWS are priced via Moomoo → `TAKE PROFIT` (≥50% credit captured), `ROLL / DEFEND` (spot below strike or delta ≤ −0.50), `CLOSE OR ROLL` (≤7 DTE), else `HOLD`. If TWS is not running the scan still works.

Setup: install and log in to [moomoo OpenD](https://www.moomoo.com/download/OpenAPI) (default `127.0.0.1:11111`), then:

```bash
python -m pip install -r requirements.txt
python -m options.cli check        # OpenD login, kline quota, option quote/greeks permission, TWS
python -m options.cli groups       # your watchlist group names -> options/config.yaml
python -m options.cli scan         # one scan -> reports/options/latest.md + latest.json
python -m options.cli positions    # only the IBKR short-put advice
python -m options.cli loop         # scan every 30 min during US market hours (Ctrl+C to stop)
python -m options.cli history      # recent scans and their top put
```

Options: `--no-positions` skips TWS, `--interval 15` changes the loop period, `--any-time` loops outside market hours, `--refresh-cache` re-downloads daily bars and chain listings, `--config path.yaml` uses another config. All thresholds live in `options/config.yaml`.

Every scan is stored in `data/options.db` (`scans`, `scan_dips`, `scan_candidates`: each ranked put with its yield, IV, IV rank and IV/HV). That history is what you use to compare ranking metrics later, e.g. `SELECT ticker, strike, expiry, annualized_yield, iv_rank FROM scan_candidates WHERE rank <= 5`. The same database holds a per-day cache of daily bars and option-chain listings, so only the first scan of the day pays for those rate-limited requests; later loop iterations only fetch live quotes. `reports/options/latest.*` is rewritten every scan, and a timestamped copy is kept only when the ranked puts or position actions change.

The dashboard's **Put Signals** tab shows `reports/options/latest.json` and reloads every minute while the backend is running.

Notes and limits:

- OpenD rate-limits option chains (~10 requests / 30 s). Chains are fetched only for dipping stocks, each 7–45 DTE window takes two requests, and listings are cached for the day, so only the first scan with a large dip list takes a few minutes.
- Daily history counts against Moomoo's 30-day per-stock kline quota; `check` shows what's left.
- Market hours ignore exchange holidays; on those days the loop just produces unchanged scans.
- Earnings dates are not checked. Look before selling a put that expires after earnings.

Run its tests with `python -m pytest tests/options`.

## Project Layout

- `options/` - Moomoo put-selling signal scanner (dip detection, put ranking, IBKR short-put advice, reports, scan history).
- `shared/` - Code used by several packages: the Moomoo OpenD client, IBKR connection helpers, `.env` loading.
- `backend/` - FastAPI app, SQLite models, market data provider, refresh jobs, signal rules, tests.
- `frontend/` - React + Vite dashboard.
- `bot/` - Standalone guarded IBKR trading bot with strategy hooks, risk checks, and SQLite audit storage.
- `data/` - Local SQLite database location and imported demo CSV cache.
- `reports/` - Ignored local/generated reports and archived demo analysis outputs.

## Local Databases

- `data/dashboard.db` - Web dashboard research data: watchlist, prices, fundamentals, reported financial statements, manual holdings, signals, and local AI analyses.
- `data/options.db` - Put-selling scan history plus the scanner's per-day market data cache.
- `data/ibkr_bot_audit.db` - Standalone IBKR bot audit data: runs, decisions, risk checks, synced positions, submitted orders, and fills.

## Backend Setup

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r ../requirements.txt
uvicorn app.main:app --reload --port 8000
```

The API will be available at `http://127.0.0.1:8000`.

Run the backend test suite with:

```bash
cd backend
python3 -m pytest
```

### Market Data Provider

Dashboard prices come from Moomoo OpenD (`MARKET_DATA_PROVIDER=moomoo`, the default), the same source as the put scanner: raw OHLCV plus forward-adjusted closes, company name/exchange, trailing PE and dividend yield. Moomoo has no sector, forward PE, growth, leverage or free-cash-flow data, so those still come from `yfinance` (or FMP, below). If OpenD is not running when a refresh starts, that refresh uses `yfinance` for everything. Set `MARKET_DATA_PROVIDER=yfinance` in `backend/.env` to skip Moomoo, and `MOOMOO_OPEND_HOST` / `MOOMOO_OPEND_PORT` if OpenD is not on `127.0.0.1:11111`.

### Optional Fundamentals Provider

Without Moomoo, price history and company profiles use `yfinance`. For more reliable fundamentals, set a Financial Modeling Prep API key before starting the backend:

```bash
cd backend
cp .env.example .env
# edit .env and set FMP_API_KEY=your_key_here
uvicorn app.main:app --reload --port 8000
```

When `FMP_API_KEY` is present, refreshes keep the price provider above and use Financial Modeling Prep for PE, forward PE, growth, debt-to-equity, free cash flow, and dividend yield. Without a key, the app keeps the existing `yfinance` fundamentals fallback. You can also edit fundamentals manually from the selected company panel.

### Financial Analysis and Buy/Hold Verdict

Each refresh also downloads up to five years of annual and eight quarters of reported financial statements (income statement, balance sheet, cash flow) from yfinance, or from FMP when `FMP_API_KEY` is set (FMP quarterly statements need a paid plan; annual still works). They are stored in the `financial_statements` table and analyzed on demand by `GET /api/companies/{ticker}/financial-analysis`, shown in the selected-company panel and as the **Verdict** column of the watchlist.

What the analysis computes (`backend/app/services/financials.py`):

- **Ratios on trailing twelve months** (sum of the last four quarters; latest fiscal year if quarters are missing): gross/operating/net/FCF margins, ROE, ROIC on average invested capital, FCF conversion, Sloan accruals ratio, stock-comp intensity, current/quick ratio, debt/equity, net debt/EBITDA, interest coverage, payout ratio, buyback and dividend yield.
- **Growth**: 3-year revenue, diluted EPS and FCF CAGR, operating-margin trend, share-count change, and the forward EPS growth implied by trailing vs forward P/E.
- **Accounting scores**: Piotroski F-score (9 tests, with the pass/fail list) and Altman Z-score (distress < 1.81, safe > 2.99).
- **Intrinsic value**: two-stage DCF on free cash flow to the firm (5 years at a growth rate blended from history and forward estimates, capped at 20%, fading to terminal growth over years 6-10), discounted at WACC with a CAPM cost of equity and a 2-year regression beta vs SPY. Bear/base/bull scenarios give a fair-value range, a **buy-below price** (base value less the required margin of safety) and a **rich-above price**. A reverse DCF solves for the growth rate the current price implies. Banks and insurers use a justified price-to-book model (ROE vs cost of equity) instead.
- **Five pillars scored 0-100**: profitability & quality (25%), financial strength (20%), growth (20%), valuation (25%), price momentum (10%), plus red flags such as negative operating cash flow, distress-zone Z-score, thin interest coverage, heavy leverage, dilution, low-quality earnings and "priced for perfection".
- **Verdict**: not owned → `BUY`, `ACCUMULATE` (good business at a fair price, build gradually), `WATCH` (wait for the buy-below price) or `AVOID`; owned → `ADD`, `HOLD`, `TRIM` (far above fair value or overweight vs target) or `SELL_REVIEW` (red flags with a weak score). Each verdict comes with conviction, a written summary, strengths, concerns, the price levels and events that would change it, and a timing note when price momentum disagrees with fundamentals.

Valuation inputs are set in `backend/.env`: `RISK_FREE_RATE` (keep it near the current 10-year Treasury yield), `EQUITY_RISK_PREMIUM`, `TERMINAL_GROWTH_RATE` and `REQUIRED_MARGIN_OF_SAFETY`. The Ollama ticker memo receives the full analysis and is asked to check the verdict against the numbers. The older 7-field signal is kept as the "quick screen". The verdict is a model of reported numbers: it knows nothing about news, guidance, competitive changes or one-off items, so treat it as a starting point for your own research.

## Frontend Setup

```bash
cd frontend
npm install
npm run dev
```

The dashboard will be available at `http://127.0.0.1:5173`.

To point the dashboard at a different API URL, copy `frontend/.env.example` to `frontend/.env` and set `VITE_API_BASE`.

Run the frontend production build check with:

```bash
cd frontend
npm run build
```

## Standalone IBKR Trading Bot

The bot is separate from the web app. It connects to a locally running Interactive Brokers Trader Workstation, runs strategy hooks, checks every proposed order against guardrails, and records decisions, risk checks, submissions, fills, positions, and run heartbeats in its own SQLite database.

### Safety Defaults

Live trading is disabled unless you opt in with bot-specific environment variables:

```bash
cp bot/.env.example bot/.env
# edit .env
BOT_LIVE_TRADING_ENABLED=true
```

Default bot settings:

```env
BOT_IBKR_HOST=127.0.0.1
BOT_IBKR_PORT=7496
BOT_IBKR_CLIENT_ID=20
BOT_LIVE_TRADING_ENABLED=false
BOT_MAX_ORDER_NOTIONAL_USD=500
BOT_MAX_DAILY_NOTIONAL_USD=2000
BOT_ALLOW_AFTER_HOURS=false
BOT_LOOP_INTERVAL_SECONDS=300
BOT_DB_PATH=data/ibkr_bot_audit.db
BOT_TICKERS=AAPL,MSFT,SPY
BOT_STRATEGY=noop
```

V1 bot guardrails:

- US stocks/ETFs only, modeled as `STK`, `SMART`, `USD`.
- Limit orders only.
- Strategies return proposed order intents; only the risk layer can approve submission.
- Blocks short sells by default using synced IBKR positions.
- Blocks orders outside regular US market hours unless explicitly configured otherwise.
- Blocks orders above per-order and daily notional limits.
- Blocks duplicate same-symbol/same-side orders within the configured cooldown window.

Run commands from the repository root:

```bash
python -m bot.runner status
python -m bot.runner once
python -m bot.runner loop
```

The default strategy is `noop`. To enable the conservative dip-buy strategy, set `BOT_STRATEGY=conservative_dip`. It uses yfinance daily history for trend/pullback checks and IBKR only for account, position, contract, and order operations.

## Minute-Level Research Quant System

The `quant/` package is a separate Python research system for one-minute US equity analysis. It reads its watchlist and parameters from `quant/config.yaml`, downloads Yahoo Finance 1m bars, builds same-day historical-only features, trains pooled regression and three-class classification models for the next 60 minutes, generates research-only live prediction reports, and runs an event-driven backtest. It does not connect to a broker and does not place orders.

Important assumptions and risks:

- Signals are generated after the `t` minute close and execute at the `t+1` minute open in backtests.
- Yahoo Finance 1m data has limited history and can return fewer rows than requested. The default `30d` period may need adjustment if Yahoo rejects the range.
- Yahoo Finance may delay, revise, or omit minute bars and is not suitable for serious live execution.
- SPCX may have a very short trading history. Short histories and newly listed instruments can cause severe distribution drift.
- Seven trading days, or any similarly short minute-data window, does not represent many independent samples because adjacent 60-minute labels overlap heavily.
- Regression predictions require out-of-sample calibration before they can be interpreted as achievable returns.
- Classification probabilities are model scores, not true calibrated probabilities unless a separate calibration check supports that interpretation.
- The watchlist is user-selected in `quant/config.yaml`, so universe-selection and survivorship bias still exist.
- Transaction cost and slippage are modeled, but minute-level fills can differ materially from the backtest assumption.
- The first version only executes LONG/NO_TRADE signals. Short labels are used for classification but not traded.

Install Python dependencies from the repository root:

```bash
python -m pip install -r requirements.txt
```

Edit the minute-level watchlist and parameters:

```bash
notepad quant/config.yaml
```

Download 1m data:

```bash
python -m quant.cli download-data
```

Build features, 60-minute labels, and the training dataset:

```bash
python -m quant.cli build-dataset
```

Train the walk-forward regression and classification models and save out-of-sample predictions:

```bash
python -m quant.cli train-model
```

Evaluate saved out-of-sample predictions by ticker, day, intraday hour, and score buckets:

```bash
python -m quant.cli evaluate
```

Generate latest predictions with the saved model:

```bash
python -m quant.cli predict
```

Generate a research-only live prediction report for the latest completed SPCX minute bar:

```bash
python -m quant.cli predict-live --ticker SPCX
```

Generate a research-only live prediction report for a specific New York time:

```bash
python -m quant.cli predict-live --ticker SPCX --time "10:30"
```

Run download, dataset build, walk-forward training, final model training, and evaluation:

```bash
python -m quant.cli full-train
```

Generate LONG/NO_TRADE signals:

```bash
python -m quant.cli generate-signals
```

Run the event-driven minute-level backtest:

```bash
python -m quant.cli backtest
```

Run only the quant test suite:

```bash
python -m pytest tests/quant
```

Generated quant data is written under `data/quant/`; generated reports are written under `reports/quant/`. Both are ignored by git. Key outputs include `data/quant/processed/dataset.parquet`, `data/quant/models/regression_model.pkl`, `data/quant/models/classification_model.pkl`, `data/quant/models/preprocessor.pkl`, `data/quant/models/ticker_encoder.pkl`, `data/quant/models/feature_columns.json`, `data/quant/models/model_metadata.json`, `data/quant/predictions/predictions.csv`, `data/quant/predictions/signals.csv`, `reports/quant/live_prediction_*.md`, `reports/quant/live_prediction_*.json`, `reports/quant/trades.csv`, `reports/quant/equity_curve.csv`, `reports/quant/risk_status.csv`, and `reports/quant/metrics.csv`.

### TWS Setup

In Trader Workstation:

1. Open `File` -> `Global Configuration` -> `API` -> `Settings`.
2. Check `Enable ActiveX and Socket Clients`.
3. Uncheck `Read-Only API` if you want to submit orders.
4. Use socket port `7496` for live TWS. Paper TWS commonly uses `7497`.
5. Allow localhost in Trusted IPs. If needed, add both `127.0.0.1` and `::1`, or temporarily clear Trusted IP restrictions while testing locally.
6. Apply settings and fully restart TWS if the API handshake times out.

If `127.0.0.1:7496` is open but the connection still times out, TWS is accepting the socket but not completing the IB API handshake. Check for TWS popups, locked/login state, Trusted IPs, SSL/API settings, and restart TWS.

## Common Flow

1. Open the dashboard.
2. Add US tickers to the watchlist.
3. Click `Refresh Now` to fetch latest data.
4. Add manual holdings and target allocations.
5. Review signal explanations before making your own decision.

For the standalone IBKR bot:

1. Start and log in to TWS.
2. Configure `bot/.env`.
3. Run `python -m bot.runner status` to check connectivity and audit DB state.
4. Run `python -m bot.runner once` for a single evaluation.
5. Run `python -m bot.runner loop` only when the strategy hook and guardrails are ready.

## Demo Assets

The previous `stock_analysis` folder has been absorbed into the current project:

- Demo CSV price cache: `data/demo_csv/`
- Sample generated reports: `reports/demo/`
- Old Python report workflow reference: `reports/demo-stock-analysis/source/`
  
Use the dashboard Settings action `Import Demo CSV Cache` to load `data/demo_csv/*.csv` into the local SQLite database.

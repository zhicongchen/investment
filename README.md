# Local Stock Tracking and Signal Dashboard

A local-only decision-support dashboard for long-term US stock investing, plus a separate guarded Python trading bot. The dashboard tracks a watchlist, fetches free end-of-day data with `yfinance`, stores everything in SQLite, and generates transparent buy/hold/watch/sell-review signals.

Signals are educational decision support, not financial advice. The web dashboard does not connect to a broker and never places trades. Automated IBKR trading, if used, runs only through the standalone Python bot and is disabled by default.

## Project Layout

- `backend/` - FastAPI app, SQLite models, market data provider, refresh jobs, signal rules, tests.
- `frontend/` - React + Vite dashboard.
- `bot/` - Standalone guarded IBKR trading bot with strategy hooks, risk checks, and SQLite audit storage.
- `data/` - Local SQLite database location and imported demo CSV cache.
- `reports/` - Ignored local/generated reports and archived demo analysis outputs.

## Local Databases

- `data/dashboard.db` - Web dashboard research data: watchlist, prices, fundamentals, manual holdings, signals, and local AI analyses.
- `data/ibkr_bot_audit.db` - Standalone IBKR bot audit data: runs, decisions, risk checks, synced positions, submitted orders, and fills.

## Backend Setup

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

The API will be available at `http://127.0.0.1:8000`.

Run the backend test suite with:

```bash
cd backend
python3 -m pytest
```

### Optional Fundamentals Provider

Price history and company profiles use `yfinance` by default. For more reliable fundamentals, set a Financial Modeling Prep API key before starting the backend:

```bash
cd backend
cp .env.example .env
# edit .env and set FMP_API_KEY=your_key_here
uvicorn app.main:app --reload --port 8000
```

When `FMP_API_KEY` is present, refreshes continue to use `yfinance` for prices and use Financial Modeling Prep for PE, forward PE, growth, debt-to-equity, free cash flow, and dividend yield. Without a key, the app keeps the existing `yfinance` fundamentals fallback. You can also edit fundamentals manually from the selected company panel.

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

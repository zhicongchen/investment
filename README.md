# Local Stock Tracking and Signal Dashboard

A local-only decision-support dashboard for long-term US stock investing. It tracks a watchlist, fetches free end-of-day data with `yfinance`, stores everything in SQLite, and generates transparent buy/hold/watch/sell-review signals.

This tool does not connect to a broker and never places trades. Signals are educational decision support, not financial advice.

## Project Layout

- `backend/` - FastAPI app, SQLite models, market data provider, refresh jobs, signal rules, tests.
- `frontend/` - React + Vite dashboard.
- `data/` - Local SQLite database location and imported demo CSV cache.
- `reports/` - Ignored local/generated reports and archived demo analysis outputs.

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

## Common Flow

1. Open the dashboard.
2. Add US tickers to the watchlist.
3. Click `Refresh Now` to fetch latest data.
4. Add manual holdings and target allocations.
5. Review signal explanations before making your own decision.

## Demo Assets

The previous `stock_analysis` folder has been absorbed into the current project:

- Demo CSV price cache: `data/demo_csv/`
- Sample generated reports: `reports/demo/`
- Old Python report workflow reference: `reports/demo-stock-analysis/source/`
  
Use the dashboard Settings action `Import Demo CSV Cache` to load `data/demo_csv/*.csv` into the local SQLite database.

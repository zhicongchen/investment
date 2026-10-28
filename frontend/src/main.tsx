import React, { useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Activity, BarChart3, BrainCircuit, BriefcaseBusiness, Cpu, GitCompare, Plus, RefreshCw, Save, Settings, Trash2, TrendingDown } from 'lucide-react';
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import './styles.css';

const DEFAULT_API_BASE = import.meta.env.VITE_API_BASE ?? 'http://127.0.0.1:8000/api';
const API_BASE_STORAGE_KEY = 'apiBase';

function storedApiBase(): string {
  try {
    return localStorage.getItem(API_BASE_STORAGE_KEY) || '';
  } catch {
    return '';
  }
}

const API_BASE = (storedApiBase() || DEFAULT_API_BASE).replace(/\/+$/, '');

type Signal = {
  date: string;
  action: string;
  score: number;
  confidence: string;
  reasons: string[];
  risk_flags: string[];
};

type WatchlistRow = {
  ticker: string;
  name: string;
  sector: string;
  exchange: string;
  latest_price: number | null;
  latest_price_date: string | null;
  signal: Signal | null;
  stale: boolean;
  metrics: PriceMetrics | null;
};

type PriceMetrics = {
  latest_price: number | null;
  latest_price_date: string | null;
  ytd_return: number | null;
  high_52w: number | null;
  low_52w: number | null;
  annualized_volatility: number | null;
  max_drawdown: number | null;
  total_return: number | null;
  benchmark_return: number | null;
  relative_return: number | null;
};

type Holding = {
  ticker: string;
  shares: number;
  average_cost: number;
  target_allocation: number;
  notes: string;
  market_value: number;
  unrealized_gain_loss: number;
  current_allocation: number;
};

type PricePoint = {
  date: string;
  close: number | null;
  adjusted_close: number | null;
  volume: number | null;
  ma20: number | null;
  ma50: number | null;
  ma200: number | null;
  rsi: number | null;
  macd: number | null;
  macd_signal: number | null;
  macd_histogram: number | null;
  bb_mid: number | null;
  bb_upper: number | null;
  bb_lower: number | null;
  drawdown: number | null;
};

type Fundamentals = {
  date: string;
  pe: number | null;
  forward_pe: number | null;
  eps_growth: number | null;
  revenue_growth: number | null;
  debt_to_equity: number | null;
  free_cash_flow: number | null;
  dividend_yield: number | null;
};

type CompanyDetail = {
  company: {
    ticker: string;
    name: string;
    sector: string;
    exchange: string;
    active: boolean;
  };
  prices: PricePoint[];
  metrics: PriceMetrics | null;
  fundamentals: Fundamentals | null;
  signals: Signal[];
  holding: Holding | null;
};

type MarketComparison = {
  benchmark: string;
  tickers: string[];
  cumulative_returns: Record<string, number | string | null>[];
  correlation: Record<string, number | string | null>[];
};

type PortfolioAnalytics = {
  benchmark: string;
  total_value: number;
  total_return: number | null;
  annualized_volatility: number | null;
  sharpe_ratio: number | null;
  series: Record<string, number | string | null>[];
  allocation: {
    ticker: string;
    current_allocation: number;
    target_allocation: number;
    drift: number;
  }[];
};

type OllamaStatus = {
  base_url: string;
  model: string;
  reachable: boolean;
  error: string | null;
};

type OllamaAnalysis = {
  id: number;
  subject_type: string;
  ticker: string | null;
  model: string;
  prompt_version: string;
  analysis_type: string;
  data_snapshot_date: string | null;
  content: string;
  created_at: string;
};

type Tab = 'dashboard' | 'puts' | 'holdings' | 'compare' | 'settings';

type PutCandidate = {
  ticker: string;
  code: string;
  expiry: string;
  dte: number;
  strike: number;
  spot: number;
  bid: number;
  ask: number;
  premium: number;
  delta: number | null;
  delta_estimated: boolean;
  iv: number | null;
  iv_rank: number | null;
  open_interest: number | null;
  otm_pct: number;
  annualized_yield: number;
  breakeven: number;
  cushion_pct: number;
  collateral: number;
  contracts: number | null;
  score: number;
};

type DipRow = {
  ticker: string;
  spot: number;
  day_change_pct: number | null;
  off_high_pct: number | null;
  n_day_return_pct: number | null;
  rsi: number | null;
  triggers: string[];
  blocked_by: string[];
  is_dip: boolean;
};

type PositionAdviceRow = {
  position: { ticker: string; expiry: string; strike: number; contracts: number; entry_credit: number };
  dte: number;
  spot: number | null;
  option_mid: number | null;
  delta: number | null;
  profit_pct: number | null;
  unrealized_pnl: number | null;
  action: string;
  reasons: string[];
};

type OptionsScan = {
  generated_at: string;
  ranking_metric: string;
  universe: string[];
  dips: DipRow[];
  candidates: PutCandidate[];
  vols: Record<string, { iv: number | null; iv_rank: number | null; iv_hv_ratio: number | null }>;
  positions_status: string;
  positions: PositionAdviceRow[];
  exposure: Record<string, { contracts: number; collateral: number }>;
  errors: Record<string, string>;
  warnings: string[];
};

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      headers: { 'Content-Type': 'application/json' },
      ...options,
    });
  } catch {
    throw new Error(`Cannot reach the backend at ${API_BASE}. Start it on this computer (uvicorn app.main:app --port 8000) or change the backend URL in Settings.`);
  }
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

function money(value: number | null | undefined): string {
  if (value === null || value === undefined) return 'n/a';
  return value.toLocaleString(undefined, { style: 'currency', currency: 'USD' });
}

function pct(value: number | null | undefined): string {
  if (value === null || value === undefined) return 'n/a';
  return `${(value * 100).toFixed(1)}%`;
}

function num(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined) return 'n/a';
  return value.toFixed(digits);
}

function signalClass(action?: string): string {
  return `signal ${action ? action.toLowerCase().replace('_', '-') : 'none'}`;
}

function App() {
  const [tab, setTab] = useState<Tab>('dashboard');
  const [watchlist, setWatchlist] = useState<WatchlistRow[]>([]);
  const [holdings, setHoldings] = useState<Holding[]>([]);
  const [selectedTicker, setSelectedTicker] = useState<string>('');
  const [detail, setDetail] = useState<CompanyDetail | null>(null);
  const [comparison, setComparison] = useState<MarketComparison | null>(null);
  const [portfolioAnalytics, setPortfolioAnalytics] = useState<PortfolioAnalytics | null>(null);
  const [ollamaStatus, setOllamaStatus] = useState<OllamaStatus | null>(null);
  const [tickerAnalyses, setTickerAnalyses] = useState<OllamaAnalysis[]>([]);
  const [portfolioAnalyses, setPortfolioAnalyses] = useState<OllamaAnalysis[]>([]);
  const [tickerInput, setTickerInput] = useState('');
  const [holdingForm, setHoldingForm] = useState({ ticker: '', shares: '', average_cost: '', target_allocation: '', notes: '' });
  const [busy, setBusy] = useState(false);
  const [aiBusy, setAiBusy] = useState(false);
  const [error, setError] = useState('');
  const [apiBaseInput, setApiBaseInput] = useState(API_BASE);

  function saveApiBase(event: React.FormEvent) {
    event.preventDefault();
    const value = apiBaseInput.trim().replace(/\/+$/, '');
    try {
      if (!value || value === DEFAULT_API_BASE) localStorage.removeItem(API_BASE_STORAGE_KEY);
      else localStorage.setItem(API_BASE_STORAGE_KEY, value);
    } catch {
      setError('Could not save the backend URL: browser storage is unavailable.');
      return;
    }
    window.location.reload();
  }

  async function load() {
    setError('');
    const [watchRows, holdingRows] = await Promise.all([
      request<WatchlistRow[]>('/watchlist'),
      request<Holding[]>('/holdings'),
    ]);
    setWatchlist(watchRows);
    setHoldings(holdingRows);
    if (!selectedTicker && watchRows.length > 0) {
      setSelectedTicker(watchRows[0].ticker);
    }
  }

  async function loadAnalytics() {
    const [marketData, portfolioData] = await Promise.all([
      request<MarketComparison>('/analytics/market'),
      request<PortfolioAnalytics>('/analytics/portfolio'),
    ]);
    setComparison(marketData);
    setPortfolioAnalytics(portfolioData);
  }

  async function loadOllamaStatus() {
    setOllamaStatus(await request<OllamaStatus>('/ollama/status'));
  }

  async function loadTickerAnalyses(ticker: string) {
    setTickerAnalyses(await request<OllamaAnalysis[]>(`/ollama/analyze/ticker/${ticker}`));
  }

  async function loadPortfolioAnalyses() {
    setPortfolioAnalyses(await request<OllamaAnalysis[]>('/ollama/analyze/portfolio'));
  }

  useEffect(() => {
    load().catch((err) => setError(err.message));
  }, []);

  useEffect(() => {
    if (!selectedTicker) {
      setDetail(null);
      return;
    }
    request<CompanyDetail>(`/companies/${selectedTicker}`).then(setDetail).catch((err) => setError(err.message));
    loadTickerAnalyses(selectedTicker).catch(() => setTickerAnalyses([]));
  }, [selectedTicker]);

  useEffect(() => {
    if (tab === 'compare' || tab === 'holdings') {
      loadAnalytics().catch((err) => setError(err.message));
    }
    if (tab === 'holdings') {
      loadPortfolioAnalyses().catch(() => setPortfolioAnalyses([]));
    }
    if (tab === 'settings') {
      loadOllamaStatus().catch((err) => setError(err.message));
    }
  }, [tab, holdings.length, watchlist.length]);

  const portfolioValue = useMemo(() => holdings.reduce((sum, holding) => sum + holding.market_value, 0), [holdings]);
  const staleCount = watchlist.filter((row) => row.stale).length;

  async function addTicker(event: React.FormEvent) {
    event.preventDefault();
    if (!tickerInput.trim()) return;
    setBusy(true);
    try {
      const ticker = tickerInput.trim().toUpperCase();
      await request('/watchlist', { method: 'POST', body: JSON.stringify({ ticker }) });
      setTickerInput('');
      setSelectedTicker(ticker);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to add ticker');
    } finally {
      setBusy(false);
    }
  }

  async function refresh(ticker?: string) {
    setBusy(true);
    setError('');
    try {
      const result = await request<{ refreshed: string[]; failed: Record<string, string> }>(ticker ? `/refresh/${ticker}` : '/refresh', { method: 'POST' });
      const failures = Object.entries(result.failed);
      if (failures.length > 0) {
        const preview = failures.slice(0, 3).map(([symbol, message]) => `${symbol}: ${message}`).join('; ');
        setError(`Refresh completed with ${failures.length} failure(s). ${preview}`);
      }
      await load();
      if (tab === 'compare' || tab === 'holdings') await loadAnalytics();
      if (ticker) {
        setDetail(await request<CompanyDetail>(`/companies/${ticker}`));
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Refresh failed');
    } finally {
      setBusy(false);
    }
  }

  async function removeTicker(ticker: string) {
    setBusy(true);
    try {
      await request(`/watchlist/${ticker}`, { method: 'DELETE' });
      if (selectedTicker === ticker) setSelectedTicker('');
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Remove failed');
    } finally {
      setBusy(false);
    }
  }

  async function saveHolding(event: React.FormEvent) {
    event.preventDefault();
    const ticker = holdingForm.ticker.trim().toUpperCase();
    if (!ticker) return;
    setBusy(true);
    try {
      await request(`/holdings/${ticker}`, {
        method: 'PUT',
        body: JSON.stringify({
          ticker,
          shares: Number(holdingForm.shares || 0),
          average_cost: Number(holdingForm.average_cost || 0),
          target_allocation: Number(holdingForm.target_allocation || 0) / 100,
          notes: holdingForm.notes,
        }),
      });
      setHoldingForm({ ticker: '', shares: '', average_cost: '', target_allocation: '', notes: '' });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save holding');
    } finally {
      setBusy(false);
    }
  }

  async function importDemoData() {
    setBusy(true);
    setError('');
    try {
      await request('/demo/import', { method: 'POST' });
      await load();
      await loadAnalytics();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Demo data import failed');
    } finally {
      setBusy(false);
    }
  }

  async function saveFundamentals(ticker: string, payload: Omit<Fundamentals, 'date'>) {
    setBusy(true);
    setError('');
    try {
      await request(`/companies/${ticker}/fundamentals`, {
        method: 'PUT',
        body: JSON.stringify(payload),
      });
      await load();
      setDetail(await request<CompanyDetail>(`/companies/${ticker}`));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save fundamentals');
    } finally {
      setBusy(false);
    }
  }

  async function generateTickerAnalysis(ticker: string) {
    setAiBusy(true);
    setError('');
    try {
      const analysis = await request<OllamaAnalysis>(`/ollama/analyze/ticker/${ticker}`, { method: 'POST' });
      setTickerAnalyses((current) => [analysis, ...current]);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Ollama analysis failed');
    } finally {
      setAiBusy(false);
    }
  }

  async function generatePortfolioAnalysis() {
    setAiBusy(true);
    setError('');
    try {
      const analysis = await request<OllamaAnalysis>('/ollama/analyze/portfolio', { method: 'POST' });
      setPortfolioAnalyses((current) => [analysis, ...current]);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Ollama portfolio analysis failed');
    } finally {
      setAiBusy(false);
    }
  }

  return (
    <main className="shell">
      <aside className="sidebar">
        <div className="brand">
          <BarChart3 size={26} />
          <div>
            <h1>Stock Signals</h1>
            <p>Local decision support</p>
          </div>
        </div>
        <button className={tab === 'dashboard' ? 'nav active' : 'nav'} onClick={() => setTab('dashboard')}>
          <Activity size={18} /> Dashboard
        </button>
        <button className={tab === 'puts' ? 'nav active' : 'nav'} onClick={() => setTab('puts')}>
          <TrendingDown size={18} /> Put Signals
        </button>
        <button className={tab === 'holdings' ? 'nav active' : 'nav'} onClick={() => setTab('holdings')}>
          <BriefcaseBusiness size={18} /> Holdings
        </button>
        <button className={tab === 'compare' ? 'nav active' : 'nav'} onClick={() => setTab('compare')}>
          <GitCompare size={18} /> Compare
        </button>
        <button className={tab === 'settings' ? 'nav active' : 'nav'} onClick={() => setTab('settings')}>
          <Settings size={18} /> Settings
        </button>
      </aside>

      <section className="content">
        <header className="topbar">
          <div>
            <h2>{tab === 'dashboard' ? 'Watchlist Overview' : tab === 'puts' ? 'Put-Selling Signals' : tab === 'holdings' ? 'Manual Holdings' : tab === 'compare' ? 'Market Comparison' : 'Settings'}</h2>
            <p>No broker connection. No trade execution. Review each signal before acting.</p>
          </div>
          {tab !== 'puts' && (
            <button className="primary" onClick={() => refresh()} disabled={busy || watchlist.length === 0}>
              <RefreshCw size={17} /> Refresh Now
            </button>
          )}
        </header>

        {tab === 'puts' && <PutSignalsPanel />}

        {error && <div className="alert">{error}</div>}
        {staleCount > 0 && <div className="notice">{staleCount} ticker(s) have stale or missing data.</div>}

        {tab === 'dashboard' && (
          <div className="grid">
            <section className="panel wide">
              <form className="inline-form" onSubmit={addTicker}>
                <input value={tickerInput} onChange={(event) => setTickerInput(event.target.value)} placeholder="Add ticker, e.g. MSFT" />
                <button className="icon-button" disabled={busy} title="Add ticker">
                  <Plus size={18} />
                </button>
              </form>
              <table>
                <thead>
                  <tr>
                    <th>Ticker</th>
                    <th>Company</th>
                    <th>Price</th>
                    <th>Signal</th>
                    <th>Score</th>
                    <th>YTD</th>
                    <th>Drawdown</th>
                    <th>Date</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {watchlist.map((row) => (
                    <tr key={row.ticker} className={selectedTicker === row.ticker ? 'selected' : ''} onClick={() => setSelectedTicker(row.ticker)}>
                      <td className="ticker">{row.ticker}</td>
                      <td>
                        <strong>{row.name || row.ticker}</strong>
                        <span>{row.sector || row.exchange || 'US stock'}</span>
                      </td>
                      <td>{money(row.latest_price)}</td>
                      <td><span className={signalClass(row.signal?.action)}>{row.signal?.action || 'PENDING'}</span></td>
                      <td>{row.signal ? row.signal.score.toFixed(1) : 'n/a'}</td>
                      <td className={row.metrics?.ytd_return && row.metrics.ytd_return >= 0 ? 'positive' : 'negative'}>{pct(row.metrics?.ytd_return)}</td>
                      <td>{pct(row.metrics?.max_drawdown)}</td>
                      <td>{row.latest_price_date || 'n/a'}</td>
                      <td>
                        <button className="ghost" title="Remove ticker" onClick={(event) => { event.stopPropagation(); removeTicker(row.ticker); }}>
                          <Trash2 size={16} />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>

            <CompanyPanel
              detail={detail}
              refresh={refresh}
              saveFundamentals={saveFundamentals}
              busy={busy}
              aiBusy={aiBusy}
              analyses={tickerAnalyses}
              generateAnalysis={generateTickerAnalysis}
            />
          </div>
        )}

        {tab === 'holdings' && (
          <div className="grid">
            <section className="panel">
              <form className="stack-form" onSubmit={saveHolding}>
                <input placeholder="Ticker" value={holdingForm.ticker} onChange={(e) => setHoldingForm({ ...holdingForm, ticker: e.target.value })} />
                <input placeholder="Shares" type="number" step="0.0001" value={holdingForm.shares} onChange={(e) => setHoldingForm({ ...holdingForm, shares: e.target.value })} />
                <input placeholder="Average cost" type="number" step="0.01" value={holdingForm.average_cost} onChange={(e) => setHoldingForm({ ...holdingForm, average_cost: e.target.value })} />
                <input placeholder="Target allocation %" type="number" step="0.1" value={holdingForm.target_allocation} onChange={(e) => setHoldingForm({ ...holdingForm, target_allocation: e.target.value })} />
                <textarea placeholder="Notes" value={holdingForm.notes} onChange={(e) => setHoldingForm({ ...holdingForm, notes: e.target.value })} />
                <button className="primary" disabled={busy}>Save Holding</button>
              </form>
            </section>
            <section className="panel wide">
              <div className="metric-row">
                <div><span>Portfolio Value</span><strong>{money(portfolioValue)}</strong></div>
                <div><span>Positions</span><strong>{holdings.length}</strong></div>
                <div><span>Total Return</span><strong>{pct(portfolioAnalytics?.total_return)}</strong></div>
                <div><span>Sharpe</span><strong>{num(portfolioAnalytics?.sharpe_ratio)}</strong></div>
              </div>
              <table>
                <thead>
                  <tr>
                    <th>Ticker</th>
                    <th>Shares</th>
                    <th>Market Value</th>
                    <th>Gain/Loss</th>
                    <th>Current</th>
                    <th>Target</th>
                  </tr>
                </thead>
                <tbody>
                  {holdings.map((holding) => (
                    <tr key={holding.ticker}>
                      <td className="ticker">{holding.ticker}</td>
                      <td>{holding.shares}</td>
                      <td>{money(holding.market_value)}</td>
                      <td className={holding.unrealized_gain_loss >= 0 ? 'positive' : 'negative'}>{money(holding.unrealized_gain_loss)}</td>
                      <td>{pct(holding.current_allocation)}</td>
                      <td>{pct(holding.target_allocation)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <PortfolioPanel analytics={portfolioAnalytics} />
              <AnalysisPanel
                title="Portfolio AI Analysis"
                analyses={portfolioAnalyses}
                busy={aiBusy}
                onGenerate={generatePortfolioAnalysis}
                empty="Generate a local Ollama memo from holdings, allocation drift, watchlist signals, and SPY context."
              />
            </section>
          </div>
        )}

        {tab === 'compare' && (
          <ComparePanel comparison={comparison} />
        )}

        {tab === 'settings' && (
          <section className="panel settings-panel">
            <h3>Local Configuration</h3>
            <form className="stack-form api-base-form" onSubmit={saveApiBase}>
              <label htmlFor="api-base">Backend URL</label>
              <div className="inline-form">
                <input id="api-base" value={apiBaseInput} onChange={(event) => setApiBaseInput(event.target.value)} placeholder={DEFAULT_API_BASE} />
                <button className="icon-button" title="Save backend URL">
                  <Save size={18} />
                </button>
              </div>
            </form>
            <dl>
              <dt>Market</dt><dd>US-listed stocks</dd>
              <dt>Data provider</dt><dd>Moomoo OpenD prices (yfinance when OpenD is offline); yfinance or FMP for sector and extra fundamentals</dd>
              <dt>Refresh schedule</dt><dd>Daily at 5:10 PM US/Eastern while the backend is running</dd>
              <dt>Signal posture</dt><dd>Balanced long-term scoring using valuation, quality, growth, trend, and portfolio fit</dd>
              <dt>Execution</dt><dd>Signals only. This app never places trades.</dd>
              <dt>Ollama</dt><dd>{ollamaStatus ? `${ollamaStatus.reachable ? 'Reachable' : 'Unavailable'} · ${ollamaStatus.model} · ${ollamaStatus.base_url}` : 'Status not checked yet'}</dd>
            </dl>
            {ollamaStatus?.error && <div className="alert compact-alert">{ollamaStatus.error}</div>}
            <button className="secondary" onClick={loadOllamaStatus} disabled={busy}>
              <Cpu size={16} /> Check Ollama
            </button>
            <button className="secondary" onClick={importDemoData} disabled={busy}>
              <RefreshCw size={16} /> Import Demo CSV Cache
            </button>
          </section>
        )}
      </section>
    </main>
  );
}

type FundamentalsForm = Record<keyof Omit<Fundamentals, 'date'>, string>;

function CompanyPanel({
  detail,
  refresh,
  saveFundamentals,
  busy,
  aiBusy,
  analyses,
  generateAnalysis,
}: {
  detail: CompanyDetail | null;
  refresh: (ticker?: string) => Promise<void>;
  saveFundamentals: (ticker: string, payload: Omit<Fundamentals, 'date'>) => Promise<void>;
  busy: boolean;
  aiBusy: boolean;
  analyses: OllamaAnalysis[];
  generateAnalysis: (ticker: string) => Promise<void>;
}) {
  if (!detail) {
    return <section className="panel detail"><p className="empty">Add or select a ticker to inspect the signal.</p></section>;
  }

  const latestSignal = detail.signals[0];
  const chartData = detail.prices.slice(-260).map((point) => ({
    date: point.date,
    close: point.adjusted_close ?? point.close,
    ma20: point.ma20,
    ma50: point.ma50,
    ma200: point.ma200,
    bbUpper: point.bb_upper,
    bbLower: point.bb_lower,
    rsi: point.rsi,
    macd: point.macd,
    macdSignal: point.macd_signal,
    drawdown: point.drawdown,
  }));
  const latestPoint = detail.prices.length ? detail.prices[detail.prices.length - 1] : null;

  return (
    <section className="panel detail">
      <div className="detail-head">
        <div>
          <h3>{detail.company.ticker} <span>{detail.company.name}</span></h3>
          <p>{detail.company.sector || detail.company.exchange || 'US stock'}</p>
        </div>
        <div className="button-row">
          <button className="secondary" onClick={() => generateAnalysis(detail.company.ticker)} disabled={aiBusy}>
            <BrainCircuit size={16} /> AI Analysis
          </button>
          <button className="secondary" onClick={() => refresh(detail.company.ticker)}>
            <RefreshCw size={16} /> Refresh
          </button>
        </div>
      </div>

      {latestSignal && (
        <div className="signal-box">
          <span className={signalClass(latestSignal.action)}>{latestSignal.action}</span>
          <strong>{latestSignal.score.toFixed(1)}</strong>
          <small>{latestSignal.confidence} confidence · {latestSignal.date}</small>
        </div>
      )}

      <div className="metric-row compact">
        <div><span>YTD</span><strong>{pct(detail.metrics?.ytd_return)}</strong></div>
        <div><span>52W Range</span><strong>{money(detail.metrics?.low_52w)} - {money(detail.metrics?.high_52w)}</strong></div>
        <div><span>Volatility</span><strong>{pct(detail.metrics?.annualized_volatility)}</strong></div>
        <div><span>Vs SPY</span><strong>{pct(detail.metrics?.relative_return)}</strong></div>
      </div>

      <div className="chart">
        <ResponsiveContainer width="100%" height={260}>
          <LineChart data={chartData}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="date" minTickGap={32} />
            <YAxis domain={['auto', 'auto']} />
            <Tooltip />
            <Line type="monotone" dataKey="close" stroke="#2563eb" strokeWidth={2} dot={false} />
            <Line type="monotone" dataKey="ma20" stroke="#16a34a" strokeWidth={1.2} dot={false} />
            <Line type="monotone" dataKey="ma50" stroke="#0891b2" strokeWidth={1.5} dot={false} />
            <Line type="monotone" dataKey="ma200" stroke="#b45309" strokeWidth={1.5} dot={false} />
            <Line type="monotone" dataKey="bbUpper" stroke="#94a3b8" strokeWidth={1} dot={false} />
            <Line type="monotone" dataKey="bbLower" stroke="#94a3b8" strokeWidth={1} dot={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>

      <div className="technical-grid">
        <div>
          <h4>Technical</h4>
          <table className="mini-table">
            <tbody>
              <tr><td>RSI</td><td>{num(latestPoint?.rsi, 1)}</td></tr>
              <tr><td>MACD</td><td>{num(latestPoint?.macd)}</td></tr>
              <tr><td>MACD Signal</td><td>{num(latestPoint?.macd_signal)}</td></tr>
              <tr><td>Drawdown</td><td>{pct(latestPoint?.drawdown)}</td></tr>
            </tbody>
          </table>
        </div>
        <div className="small-chart">
          <ResponsiveContainer width="100%" height={170}>
            <LineChart data={chartData}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="date" minTickGap={36} />
              <YAxis domain={[0, 100]} />
              <Tooltip />
              <Line type="monotone" dataKey="rsi" stroke="#7c3aed" strokeWidth={1.5} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      <FundamentalsEditor detail={detail} saveFundamentals={saveFundamentals} busy={busy} />

      <div className="explain">
        <div>
          <h4>Reasons</h4>
          <ul>{(latestSignal?.reasons.length ? latestSignal.reasons : ['Signal pending data refresh']).map((item) => <li key={item}>{item}</li>)}</ul>
        </div>
        <div>
          <h4>Risk Flags</h4>
          <ul>{(latestSignal?.risk_flags.length ? latestSignal.risk_flags : ['No risk flags recorded']).map((item) => <li key={item}>{item}</li>)}</ul>
        </div>
      </div>

      <AnalysisPanel
        title="Ticker AI Analysis"
        analyses={analyses}
        busy={aiBusy}
        onGenerate={() => generateAnalysis(detail.company.ticker)}
        empty="Generate a local Ollama deep dive from this ticker's collected data."
      />
    </section>
  );
}

function fieldValue(value: number | null | undefined, percent = false): string {
  if (value === null || value === undefined) return '';
  return String(percent ? Number((value * 100).toFixed(4)) : value);
}

function parseOptionalNumber(value: string, percent = false): number | null {
  if (!value.trim()) return null;
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return null;
  return percent ? parsed / 100 : parsed;
}

function FundamentalsEditor({
  detail,
  saveFundamentals,
  busy,
}: {
  detail: CompanyDetail;
  saveFundamentals: (ticker: string, payload: Omit<Fundamentals, 'date'>) => Promise<void>;
  busy: boolean;
}) {
  const [form, setForm] = useState<FundamentalsForm>({
    pe: '',
    forward_pe: '',
    eps_growth: '',
    revenue_growth: '',
    debt_to_equity: '',
    free_cash_flow: '',
    dividend_yield: '',
  });

  useEffect(() => {
    setForm({
      pe: fieldValue(detail.fundamentals?.pe),
      forward_pe: fieldValue(detail.fundamentals?.forward_pe),
      eps_growth: fieldValue(detail.fundamentals?.eps_growth, true),
      revenue_growth: fieldValue(detail.fundamentals?.revenue_growth, true),
      debt_to_equity: fieldValue(detail.fundamentals?.debt_to_equity),
      free_cash_flow: fieldValue(detail.fundamentals?.free_cash_flow),
      dividend_yield: fieldValue(detail.fundamentals?.dividend_yield, true),
    });
  }, [detail.company.ticker, detail.fundamentals]);

  function update(field: keyof FundamentalsForm, value: string) {
    setForm((current) => ({ ...current, [field]: value }));
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    await saveFundamentals(detail.company.ticker, {
      pe: parseOptionalNumber(form.pe),
      forward_pe: parseOptionalNumber(form.forward_pe),
      eps_growth: parseOptionalNumber(form.eps_growth, true),
      revenue_growth: parseOptionalNumber(form.revenue_growth, true),
      debt_to_equity: parseOptionalNumber(form.debt_to_equity),
      free_cash_flow: parseOptionalNumber(form.free_cash_flow),
      dividend_yield: parseOptionalNumber(form.dividend_yield, true),
    });
  }

  return (
    <form className="fundamentals-editor" onSubmit={submit}>
      <div className="section-head">
        <div>
          <h4>Fundamentals</h4>
          <span>{detail.fundamentals?.date || 'Manual values'}</span>
        </div>
        <button className="secondary" disabled={busy} title="Save fundamentals">
          <Save size={16} /> Save
        </button>
      </div>
      <div className="fundamentals-grid">
        <label>PE<input type="number" step="0.01" value={form.pe} onChange={(e) => update('pe', e.target.value)} /></label>
        <label>Forward PE<input type="number" step="0.01" value={form.forward_pe} onChange={(e) => update('forward_pe', e.target.value)} /></label>
        <label>EPS Growth %<input type="number" step="0.01" value={form.eps_growth} onChange={(e) => update('eps_growth', e.target.value)} /></label>
        <label>Revenue Growth %<input type="number" step="0.01" value={form.revenue_growth} onChange={(e) => update('revenue_growth', e.target.value)} /></label>
        <label>Debt / Equity<input type="number" step="0.01" value={form.debt_to_equity} onChange={(e) => update('debt_to_equity', e.target.value)} /></label>
        <label>Free Cash Flow<input type="number" step="1" value={form.free_cash_flow} onChange={(e) => update('free_cash_flow', e.target.value)} /></label>
        <label>Dividend Yield %<input type="number" step="0.01" value={form.dividend_yield} onChange={(e) => update('dividend_yield', e.target.value)} /></label>
      </div>
    </form>
  );
}

function ComparePanel({ comparison }: { comparison: MarketComparison | null }) {
  if (!comparison) {
    return <section className="panel"><p className="empty">Refresh analytics to compare watchlist returns against SPY.</p></section>;
  }
  const chartData = comparison.cumulative_returns.slice(-520);
  const colors = ['#2563eb', '#059669', '#b45309', '#7c3aed', '#dc2626', '#0891b2', '#4f46e5', '#475569'];
  return (
    <div className="grid compare-grid">
      <section className="panel wide">
        <h3>Cumulative Returns</h3>
        <div className="chart tall">
          <ResponsiveContainer width="100%" height={360}>
            <LineChart data={chartData}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="date" minTickGap={38} />
              <YAxis tickFormatter={(value) => `${(Number(value) * 100).toFixed(0)}%`} />
              <Tooltip formatter={(value) => pct(Number(value))} />
              {comparison.tickers.map((ticker, index) => (
                <Line key={ticker} type="monotone" dataKey={ticker} stroke={colors[index % colors.length]} strokeWidth={1.8} dot={false} />
              ))}
              <Line type="monotone" dataKey={comparison.benchmark} stroke="#111827" strokeWidth={2.2} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </section>
      <section className="panel">
        <h3>Correlation</h3>
        <table className="correlation-table">
          <thead>
            <tr>
              <th></th>
              {comparison.tickers.map((ticker) => <th key={ticker}>{ticker}</th>)}
            </tr>
          </thead>
          <tbody>
            {comparison.correlation.map((row) => (
              <tr key={String(row.ticker)}>
                <td className="ticker">{row.ticker}</td>
                {comparison.tickers.map((ticker) => (
                  <td key={ticker}>{typeof row[ticker] === 'number' ? Number(row[ticker]).toFixed(2) : 'n/a'}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}

function PortfolioPanel({ analytics }: { analytics: PortfolioAnalytics | null }) {
  if (!analytics || analytics.series.length === 0) {
    return <p className="empty">Add holdings and refresh prices to see portfolio analytics.</p>;
  }
  return (
    <div className="portfolio-analytics">
      <h3>Portfolio Analytics</h3>
      <div className="chart">
        <ResponsiveContainer width="100%" height={250}>
          <LineChart data={analytics.series.slice(-520)}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="date" minTickGap={36} />
            <YAxis tickFormatter={(value) => `${(Number(value) * 100).toFixed(0)}%`} />
            <Tooltip formatter={(value) => pct(Number(value))} />
            <Line type="monotone" dataKey="portfolio" stroke="#2563eb" strokeWidth={2} dot={false} />
            <Line type="monotone" dataKey={analytics.benchmark} stroke="#111827" strokeWidth={1.8} dot={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <table className="mini-table">
        <thead>
          <tr>
            <th>Ticker</th>
            <th>Current</th>
            <th>Target</th>
            <th>Drift</th>
          </tr>
        </thead>
        <tbody>
          {analytics.allocation.map((item) => (
            <tr key={item.ticker}>
              <td className="ticker">{item.ticker}</td>
              <td>{pct(item.current_allocation)}</td>
              <td>{pct(item.target_allocation)}</td>
              <td className={Math.abs(item.drift) > 0.03 ? 'negative' : 'positive'}>{pct(item.drift)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function AnalysisPanel({
  title,
  analyses,
  busy,
  onGenerate,
  empty,
}: {
  title: string;
  analyses: OllamaAnalysis[];
  busy: boolean;
  onGenerate: () => Promise<void>;
  empty: string;
}) {
  const latest = analyses[0];
  return (
    <div className="ai-analysis">
      <div className="section-head">
        <div>
          <h4>{title}</h4>
          <span>{latest ? `${latest.model} · ${new Date(latest.created_at).toLocaleString()}` : 'No saved analysis yet'}</span>
        </div>
        <button className="secondary" onClick={onGenerate} disabled={busy} title="Generate local Ollama analysis">
          <BrainCircuit size={16} /> {busy ? 'Analyzing' : 'Generate'}
        </button>
      </div>
      {latest ? (
        <article className="analysis-content">{latest.content}</article>
      ) : (
        <p className="empty">{empty}</p>
      )}
      {analyses.length > 1 && <p className="history-note">{analyses.length - 1} earlier analysis item(s) saved.</p>}
    </div>
  );
}

const ADVICE_CLASS: Record<string, string> = {
  'TAKE PROFIT': 'signal buy',
  HOLD: 'signal hold',
  'CLOSE OR ROLL': 'signal watch',
  'ROLL / DEFEND': 'signal sell-review',
};

function PutSignalsPanel() {
  const [scan, setScan] = useState<OptionsScan | null>(null);
  const [error, setError] = useState('');

  async function load() {
    try {
      setScan(await request<OptionsScan>('/options/latest'));
      setError('');
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  useEffect(() => {
    load();
    const timer = window.setInterval(load, 60_000);
    return () => window.clearInterval(timer);
  }, []);

  if (!scan) {
    return <section className="panel">{error ? <div className="alert">{error}</div> : <p className="empty">Loading latest scan…</p>}</section>;
  }

  const dipping = scan.dips.filter((row) => row.is_dip);
  const errors = Object.entries(scan.errors);
  return (
    <div className="grid">
      {error && <div className="alert">{error}</div>}
      {scan.warnings.map((warning) => <div className="notice" key={warning}>{warning}</div>)}
      <section className="panel wide">
        <div className="section-head">
          <h4>Top puts to sell</h4>
          <span>
            {new Date(scan.generated_at).toLocaleString()} · {scan.universe.length} scanned · {dipping.length} going down · ranked by {scan.ranking_metric}
            {' '}
            <button className="ghost" onClick={load} title="Reload latest scan"><RefreshCw size={14} /></button>
          </span>
        </div>
        {scan.candidates.length === 0 ? (
          <p className="empty">No puts passed the filters in the latest scan.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Contract</th><th>DTE</th><th>Spot</th><th>OTM</th><th>Bid / Ask</th><th>Premium</th>
                <th>Delta</th><th>IV</th><th>IV rank</th><th>Ann. yield</th><th>Breakeven</th><th>OI</th><th>Held</th>
              </tr>
            </thead>
            <tbody>
              {scan.candidates.map((put) => (
                <tr key={put.code}>
                  <td><span className="ticker">{put.ticker} {put.strike}P</span><span>{put.expiry}</span></td>
                  <td>{put.dte}</td>
                  <td>{num(put.spot)}</td>
                  <td>{pct(put.otm_pct)}</td>
                  <td>{num(put.bid)} / {num(put.ask)}</td>
                  <td>{num(put.premium)}<span>{money(put.premium * 100)} / ct</span></td>
                  <td>{num(put.delta)}{put.delta_estimated ? '*' : ''}</td>
                  <td>{pct(put.iv)}</td>
                  <td>{num(put.iv_rank, 0)}</td>
                  <td><strong>{pct(put.annualized_yield)}</strong></td>
                  <td>{num(put.breakeven)}<span>{pct(put.cushion_pct)} cushion</span></td>
                  <td>{put.open_interest ?? 'n/a'}</td>
                  <td>{scan.exposure[put.ticker] ? `${scan.exposure[put.ticker].contracts} ct` : '-'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="panel">
        <div className="section-head"><h4>Open short puts (IBKR)</h4><span>{scan.positions_status}</span></div>
        {scan.positions.length === 0 ? (
          <p className="empty">No open short puts reported.</p>
        ) : (
          <table className="mini-table">
            <thead><tr><th>Action</th><th>Contract</th><th>DTE</th><th>Credit → Now</th><th>Profit</th><th>Spot</th></tr></thead>
            <tbody>
              {scan.positions.map((row) => (
                <tr key={`${row.position.ticker}-${row.position.expiry}-${row.position.strike}`}>
                  <td><span className={ADVICE_CLASS[row.action] ?? 'signal'}>{row.action}</span><span>{row.reasons.join('; ')}</span></td>
                  <td><span className="ticker">{row.position.ticker} {row.position.strike}P ×{row.position.contracts}</span><span>{row.position.expiry}</span></td>
                  <td>{row.dte}</td>
                  <td>{num(row.position.entry_credit)} → {num(row.option_mid)}</td>
                  <td>{pct(row.profit_pct)}<span>{money(row.unrealized_pnl)}</span></td>
                  <td>{num(row.spot)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="panel">
        <div className="section-head"><h4>Stocks going down</h4><span>{dipping.length} of {scan.dips.length}</span></div>
        {dipping.length === 0 ? (
          <p className="empty">None of the watchlist met the dip rule.</p>
        ) : (
          <table className="mini-table">
            <thead><tr><th>Ticker</th><th>Spot</th><th>Today</th><th>Off high</th><th>N-day</th><th>IV rank</th></tr></thead>
            <tbody>
              {dipping.map((row) => (
                <tr key={row.ticker}>
                  <td><span className="ticker">{row.ticker}</span><span>{row.triggers.join('; ')}</span></td>
                  <td>{num(row.spot)}</td>
                  <td className={(row.day_change_pct ?? 0) < 0 ? 'negative' : 'positive'}>{pct(row.day_change_pct)}</td>
                  <td>{pct(row.off_high_pct)}</td>
                  <td>{pct(row.n_day_return_pct)}</td>
                  <td>{num(scan.vols[row.ticker]?.iv_rank, 0)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {errors.length > 0 && <p className="history-note">{errors.length} ticker(s) had data errors: {errors.map(([ticker]) => ticker).join(', ')}</p>}
      </section>
    </div>
  );
}

createRoot(document.getElementById('root')!).render(<App />);

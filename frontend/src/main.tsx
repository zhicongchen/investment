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
import { browserRequest, dataStatus } from './browser/api';
import { clearAll } from './browser/storage';
import './styles.css';

const API_BASE = import.meta.env.VITE_API_BASE ?? 'http://127.0.0.1:8000/api';
// The GitHub Pages build reads the data pages/build.py publishes daily instead of calling the local backend.
const ONLINE = import.meta.env.VITE_DATA_MODE === 'browser';

type Signal = {
  date: string;
  action: string;
  score: number;
  confidence: string;
  reasons: string[];
  risk_flags: string[];
};

type VerdictSummary = {
  action: string;
  conviction: string;
  composite_score: number | null;
  margin_of_safety: number | null;
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
  verdict: VerdictSummary | null;
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

type AnalysisMetric = {
  key: string;
  label: string;
  value: number | null;
  format: 'pct' | 'pp' | 'x' | 'score';
  score: number | null;
  weight: number;
  note: string;
};

type AnalysisPillar = {
  key: string;
  label: string;
  weight: number;
  score: number | null;
  metrics: AnalysisMetric[];
};

type AnnualFinancials = {
  fiscal_date: string;
  revenue: number | null;
  revenue_growth: number | null;
  gross_margin: number | null;
  operating_margin: number | null;
  net_margin: number | null;
  free_cash_flow: number | null;
  fcf_margin: number | null;
  roe: number | null;
  roic: number | null;
  debt_to_equity: number | null;
  current_ratio: number | null;
  diluted_eps: number | null;
  diluted_shares: number | null;
};

type ValuationSummary = {
  method: string;
  applicable: boolean;
  note: string;
  price: number | null;
  bear_value: number | null;
  base_value: number | null;
  bull_value: number | null;
  margin_of_safety: number | null;
  upside: number | null;
  entry_price: number | null;
  trim_price: number | null;
  discount_rate: number | null;
  cost_of_equity: number | null;
  beta: number | null;
  stage_one_growth: number | null;
  growth_source: string;
  terminal_growth: number | null;
  terminal_value_share: number | null;
  implied_growth: number | null;
  base_cash_flow: number | null;
  cash_flow_basis: string;
  required_margin_of_safety: number;
};

type FinancialAnalysis = {
  ticker: string;
  sector: string;
  as_of: string | null;
  statement_date: string | null;
  statement_basis: string;
  is_financial_sector: boolean;
  data_coverage: number;
  price: number | null;
  market_cap: number | null;
  enterprise_value: number | null;
  pillars: AnalysisPillar[];
  composite_score: number | null;
  annual: AnnualFinancials[];
  piotroski: { score: number; max: number; normalized: number; fiscal_date: string; checks: { name: string; passed: boolean }[] } | null;
  altman_z: number | null;
  economic_spread: number | null;
  valuation: ValuationSummary;
  flags: { severity: 'high' | 'medium' | 'low'; message: string }[];
  position: {
    shares: number;
    average_cost: number;
    market_value: number | null;
    unrealized_return: number | null;
    allocation: number | null;
    target_allocation: number | null;
    overweight: boolean;
    underweight: boolean;
  } | null;
  recommendation: {
    action: string;
    conviction: string;
    composite_score: number | null;
    summary: string;
    rationale: string[];
    strengths: string[];
    concerns: string[];
    triggers: string[];
    timing_note: string | null;
  };
  warnings: string[];
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
  prob_profit: number | null;
  strike_sd: number | null;
  contract_iv_hv: number | null;
  edge_pct: number | null;
  earnings_date: string | null;
  earnings_in_trade: boolean;
  verdict: string | null;
  fair_value: number | null;
  breakeven_discount: number | null;
  put_score: number | null;
  grade: string | null;
  reasons: string[];
  cautions: string[];
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
  rejections?: Record<string, Record<string, number>>;
  underlyings?: Record<string, { action: string; conviction: string; composite_score: number | null; fair_value: number | null; buy_below: number | null }>;
  earnings?: Record<string, string | null>;
  source?: string;
};

type OptionsDefaults = {
  tickers: string[];
  ranking_metric: string;
  min_dte: number;
  max_dte: number;
  delta_min: number;
  delta_max: number;
  earnings: string;
  positions_enabled: boolean;
  moomoo_available: boolean;
};

type ScanForm = {
  tickers: string;
  source: 'auto' | 'moomoo' | 'yahoo';
  onlyDipping: boolean;
  includePositions: boolean;
  useFundamentals: boolean;
};

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  if (ONLINE) return browserRequest<T>(path, options);
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
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

function actionLabel(action: string): string {
  return action.replace('_', ' ');
}

function compactMoney(value: number | null | undefined): string {
  if (value === null || value === undefined) return 'n/a';
  const abs = Math.abs(value);
  const sign = value < 0 ? '-' : '';
  if (abs >= 1e12) return `${sign}$${(abs / 1e12).toFixed(2)}T`;
  if (abs >= 1e9) return `${sign}$${(abs / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `${sign}$${(abs / 1e6).toFixed(1)}M`;
  return money(value);
}

function metricText(metric: AnalysisMetric): string {
  const value = metric.value;
  if (value === null) return 'n/a';
  if (metric.format === 'pct') return pct(value);
  if (metric.format === 'pp') return `${value >= 0 ? '+' : ''}${(value * 100).toFixed(1)} pp`;
  if (metric.format === 'x') return `${value.toFixed(1)}x`;
  return value.toFixed(2);
}

function scoreClass(score: number | null | undefined): string {
  if (score === null || score === undefined) return 'score-na';
  if (score >= 70) return 'score-good';
  if (score >= 45) return 'score-mid';
  return 'score-weak';
}

function App() {
  const [tab, setTab] = useState<Tab>('dashboard');
  const [watchlist, setWatchlist] = useState<WatchlistRow[]>([]);
  const [holdings, setHoldings] = useState<Holding[]>([]);
  const [selectedTicker, setSelectedTicker] = useState<string>('');
  const [detail, setDetail] = useState<CompanyDetail | null>(null);
  const [analysis, setAnalysis] = useState<FinancialAnalysis | null>(null);
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

  async function loadFinancialAnalysis(ticker: string) {
    try {
      setAnalysis(await request<FinancialAnalysis>(`/companies/${ticker}/financial-analysis`));
    } catch {
      setAnalysis(null);
    }
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
      setAnalysis(null);
      return;
    }
    setAnalysis(null);
    request<CompanyDetail>(`/companies/${selectedTicker}`).then(setDetail).catch((err) => setError(err.message));
    loadFinancialAnalysis(selectedTicker);
    loadTickerAnalyses(selectedTicker).catch(() => setTickerAnalyses([]));
  }, [selectedTicker]);

  useEffect(() => {
    if (tab === 'compare' || tab === 'holdings') {
      loadAnalytics().catch((err) => setError(err.message));
    }
    if (tab === 'holdings' && !ONLINE) {
      loadPortfolioAnalyses().catch(() => setPortfolioAnalyses([]));
    }
    if (tab === 'settings' && !ONLINE) {
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
      const analyzed = ticker ?? selectedTicker;
      if (analyzed) await loadFinancialAnalysis(analyzed);
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
      if (selectedTicker) await loadFinancialAnalysis(selectedTicker);
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
      await loadFinancialAnalysis(ticker);
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
            <p>{ONLINE ? 'Runs in your browser' : 'Local decision support'}</p>
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
              <RefreshCw size={17} /> {ONLINE ? 'Reload Data' : 'Refresh Now'}
            </button>
          )}
        </header>

        {tab === 'puts' && <PutSignalsPanel />}

        {error && <div className="alert">{error}</div>}
        {staleCount > 0 && <div className="notice">{staleCount} ticker(s) have stale or missing data.</div>}

        {tab === 'dashboard' && (
          <div className="grid">
            <section className="panel wide">
              {ONLINE ? (
                <p className="settings-note">Watchlist from <code>pages/watchlist.yaml</code>. Prices are daily closes, updated after each US close.</p>
              ) : (
                <form className="inline-form" onSubmit={addTicker}>
                  <input value={tickerInput} onChange={(event) => setTickerInput(event.target.value)} placeholder="Add ticker, e.g. MSFT" />
                  <button className="icon-button" disabled={busy} title="Add ticker">
                    <Plus size={18} />
                  </button>
                </form>
              )}
              <table>
                <thead>
                  <tr>
                    <th>Ticker</th>
                    <th>Company</th>
                    <th>Price</th>
                    <th>Verdict</th>
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
                      <td>
                        <span className={signalClass(row.verdict?.action)}>{row.verdict ? actionLabel(row.verdict.action) : 'PENDING'}</span>
                        {row.verdict?.margin_of_safety !== null && row.verdict?.margin_of_safety !== undefined && (
                          <span>{row.verdict.margin_of_safety >= 0 ? `${pct(row.verdict.margin_of_safety)} below fair value` : `${pct(-row.verdict.margin_of_safety)} above fair value`}</span>
                        )}
                      </td>
                      <td><span className={signalClass(row.signal?.action)}>{row.signal?.action || 'PENDING'}</span></td>
                      <td>{row.signal ? row.signal.score.toFixed(1) : 'n/a'}</td>
                      <td className={row.metrics?.ytd_return && row.metrics.ytd_return >= 0 ? 'positive' : 'negative'}>{pct(row.metrics?.ytd_return)}</td>
                      <td>{pct(row.metrics?.max_drawdown)}</td>
                      <td>{row.latest_price_date || 'n/a'}</td>
                      <td>
                        {!ONLINE && (
                          <button className="ghost" title="Remove ticker" onClick={(event) => { event.stopPropagation(); removeTicker(row.ticker); }}>
                            <Trash2 size={16} />
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>

            <CompanyPanel
              detail={detail}
              analysis={analysis}
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
              {!ONLINE && (
                <AnalysisPanel
                  title="Portfolio AI Analysis"
                  analyses={portfolioAnalyses}
                  busy={aiBusy}
                  onGenerate={generatePortfolioAnalysis}
                  empty="Generate a local Ollama memo from holdings, allocation drift, watchlist signals, and SPY context."
                />
              )}
            </section>
          </div>
        )}

        {tab === 'compare' && (
          <ComparePanel comparison={comparison} />
        )}

        {tab === 'settings' && ONLINE && <OnlineSettings />}

        {tab === 'settings' && !ONLINE && (
          <section className="panel settings-panel">
            <h3>Local Configuration</h3>
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
  analysis,
  refresh,
  saveFundamentals,
  busy,
  aiBusy,
  analyses,
  generateAnalysis,
}: {
  detail: CompanyDetail | null;
  analysis: FinancialAnalysis | null;
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
          {!ONLINE && (
            <button className="secondary" onClick={() => generateAnalysis(detail.company.ticker)} disabled={aiBusy}>
              <BrainCircuit size={16} /> AI Analysis
            </button>
          )}
          <button className="secondary" onClick={() => refresh(detail.company.ticker)}>
            <RefreshCw size={16} /> Refresh
          </button>
        </div>
      </div>

      {analysis && analysis.ticker === detail.company.ticker && <VerdictPanel analysis={analysis} />}

      {latestSignal && (
        <div className="signal-box">
          <span className={signalClass(latestSignal.action)}>{latestSignal.action}</span>
          <strong>{latestSignal.score.toFixed(1)}</strong>
          <small>Quick screen · {latestSignal.confidence} confidence · {latestSignal.date}</small>
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

      {analysis && analysis.ticker === detail.company.ticker && <FinancialDetails analysis={analysis} />}

      <FundamentalsEditor detail={detail} saveFundamentals={saveFundamentals} busy={busy} />

      <div className="explain">
        <div>
          <h4>Quick Screen Reasons</h4>
          <ul>{(latestSignal?.reasons.length ? latestSignal.reasons : ['Signal pending data refresh']).map((item) => <li key={item}>{item}</li>)}</ul>
        </div>
        <div>
          <h4>Quick Screen Risks</h4>
          <ul>{(latestSignal?.risk_flags.length ? latestSignal.risk_flags : ['No risk flags recorded']).map((item) => <li key={item}>{item}</li>)}</ul>
        </div>
      </div>

      {!ONLINE && (
        <AnalysisPanel
          title="Ticker AI Analysis"
          analyses={analyses}
          busy={aiBusy}
          onGenerate={() => generateAnalysis(detail.company.ticker)}
          empty="Generate a local Ollama deep dive from this ticker's collected data."
        />
      )}
    </section>
  );
}

function VerdictPanel({ analysis }: { analysis: FinancialAnalysis }) {
  const recommendation = analysis.recommendation;
  return (
    <div className="verdict-panel">
      <div className="verdict-head">
        <span className={signalClass(recommendation.action)}>{actionLabel(recommendation.action)}</span>
        <strong>{num(recommendation.composite_score, 0)}<small>/100</small></strong>
        <small>
          {recommendation.conviction} conviction · {analysis.statement_date ? `${analysis.statement_basis} ${analysis.statement_date}` : 'no statements'}
        </small>
      </div>
      <p className="verdict-summary">{recommendation.summary}</p>
      {recommendation.rationale.length > 0 && (
        <ul className="verdict-rationale">{recommendation.rationale.map((item) => <li key={item}>{item}</li>)}</ul>
      )}
      {recommendation.timing_note && <p className="timing-note">{recommendation.timing_note}</p>}
      {analysis.warnings.map((warning) => <div className="notice compact-notice" key={warning}>{warning}</div>)}
      <ValueRange valuation={analysis.valuation} />
      <div className="pillar-list">
        {analysis.pillars.map((pillar) => (
          <div className="pillar" key={pillar.key}>
            <span>{pillar.label}</span>
            <div className="pillar-track">
              <div className={`pillar-fill ${scoreClass(pillar.score)}`} style={{ width: `${pillar.score ?? 0}%` }} />
            </div>
            <strong>{pillar.score === null ? 'n/a' : pillar.score.toFixed(0)}</strong>
          </div>
        ))}
      </div>
    </div>
  );
}

function ValueRange({ valuation }: { valuation: ValuationSummary }) {
  const { price, bear_value: bear, base_value: base, bull_value: bull, entry_price: entry, trim_price: trim } = valuation;
  if (!valuation.applicable || price === null || bear === null || base === null || bull === null) {
    return <p className="empty">{valuation.note || 'Fair value estimate unavailable.'}</p>;
  }
  const points = [price, bear, bull, entry ?? base, trim ?? bull];
  const low = Math.min(...points) * 0.9;
  const high = Math.max(...points) * 1.08;
  const position = (value: number) => `${((value - low) / (high - low)) * 100}%`;
  const upside = valuation.upside;
  return (
    <div className="value-range">
      <div className="value-range-head">
        <span>Fair value <strong>{money(base)}</strong></span>
        {upside !== null && (
          <strong className={upside >= 0 ? 'positive' : 'negative'}>{upside >= 0 ? '+' : ''}{pct(upside)} to base case</strong>
        )}
      </div>
      <div className="range-track">
        {entry !== null && <div className="zone buy-zone" style={{ left: 0, width: position(entry) }} />}
        {trim !== null && <div className="zone rich-zone" style={{ left: position(trim), right: 0 }} />}
        <div className="fair-band" style={{ left: position(bear), width: `calc(${position(bull)} - ${position(bear)})` }} />
        <div className="base-tick" style={{ left: position(base) }} />
        <div className="price-marker" style={{ left: position(price) }}><span>{money(price)}</span></div>
      </div>
      <div className="range-legend">
        <span>Bear {money(bear)}</span>
        <span>Base {money(base)}</span>
        <span>Bull {money(bull)}</span>
        {entry !== null && <span className="positive">Buy below {money(entry)}</span>}
        {trim !== null && <span className="negative">Rich above {money(trim)}</span>}
      </div>
    </div>
  );
}

type AnnualLine = { key: Exclude<keyof AnnualFinancials, 'fiscal_date'>; label: string; format: 'money' | 'pct' | 'x' | 'eps' | 'shares' };

const ANNUAL_LINES: AnnualLine[] = [
  { key: 'revenue', label: 'Revenue', format: 'money' },
  { key: 'revenue_growth', label: 'Revenue growth', format: 'pct' },
  { key: 'gross_margin', label: 'Gross margin', format: 'pct' },
  { key: 'operating_margin', label: 'Operating margin', format: 'pct' },
  { key: 'net_margin', label: 'Net margin', format: 'pct' },
  { key: 'free_cash_flow', label: 'Free cash flow', format: 'money' },
  { key: 'fcf_margin', label: 'FCF margin', format: 'pct' },
  { key: 'roic', label: 'ROIC', format: 'pct' },
  { key: 'roe', label: 'ROE', format: 'pct' },
  { key: 'debt_to_equity', label: 'Debt / equity', format: 'x' },
  { key: 'current_ratio', label: 'Current ratio', format: 'x' },
  { key: 'diluted_eps', label: 'Diluted EPS', format: 'eps' },
  { key: 'diluted_shares', label: 'Diluted shares', format: 'shares' },
];

function annualCell(value: number | null, format: AnnualLine['format']): string {
  if (value === null) return 'n/a';
  if (format === 'money') return compactMoney(value);
  if (format === 'pct') return pct(value);
  if (format === 'x') return `${value.toFixed(2)}x`;
  if (format === 'eps') return money(value);
  return `${(value / 1e6).toFixed(0)}M`;
}

function FinancialDetails({ analysis }: { analysis: FinancialAnalysis }) {
  const recommendation = analysis.recommendation;
  const valuation = analysis.valuation;
  const position = analysis.position;
  return (
    <div className="financial-details">
      <div className="explain triple">
        <div>
          <h4>Strengths</h4>
          <ul>{(recommendation.strengths.length ? recommendation.strengths : ['Nothing stands out yet']).map((item) => <li key={item}>{item}</li>)}</ul>
        </div>
        <div>
          <h4>Concerns</h4>
          <ul>{(recommendation.concerns.length ? recommendation.concerns : ['No major concerns']).map((item) => <li key={item}>{item}</li>)}</ul>
        </div>
        <div>
          <h4>What Would Change the View</h4>
          <ul>{(recommendation.triggers.length ? recommendation.triggers : ['Refresh after the next earnings report']).map((item) => <li key={item}>{item}</li>)}</ul>
        </div>
      </div>

      {analysis.flags.length > 0 && (
        <div className="flag-list">
          <h4>Red Flags</h4>
          {analysis.flags.map((flag) => (
            <div key={flag.message} className={`flag ${flag.severity}`}><span>{flag.severity}</span>{flag.message}</div>
          ))}
        </div>
      )}

      {position && (
        <div className="metric-row compact">
          <div><span>Unrealized</span><strong className={(position.unrealized_return ?? 0) >= 0 ? 'positive' : 'negative'}>{pct(position.unrealized_return)}</strong></div>
          <div><span>Market Value</span><strong>{money(position.market_value)}</strong></div>
          <div><span>Allocation</span><strong>{pct(position.allocation)}</strong></div>
          <div><span>Target</span><strong>{pct(position.target_allocation)}</strong></div>
        </div>
      )}

      <details className="analysis-section" open>
        <summary>Valuation model</summary>
        <table className="mini-table">
          <tbody>
            <tr><td>Method</td><td>{valuation.method || 'n/a'}{valuation.note && <span>{valuation.note}</span>}</td></tr>
            <tr><td>Market cap / EV</td><td>{compactMoney(analysis.market_cap)} / {compactMoney(analysis.enterprise_value)}</td></tr>
            <tr><td>Discount rate (WACC)</td><td>{pct(valuation.discount_rate)}<span>cost of equity {pct(valuation.cost_of_equity)} · beta {num(valuation.beta)}</span></td></tr>
            <tr><td>Base free cash flow</td><td>{compactMoney(valuation.base_cash_flow)}<span>{valuation.cash_flow_basis}</span></td></tr>
            <tr><td>Growth, years 1-5</td><td>{pct(valuation.stage_one_growth)}<span>{valuation.growth_source}</span></td></tr>
            <tr><td>Terminal growth</td><td>{pct(valuation.terminal_growth)}<span>{pct(valuation.terminal_value_share)} of value from terminal year</span></td></tr>
            <tr><td>Growth priced in</td><td>{pct(valuation.implied_growth)}<span>reverse DCF at today's price</span></td></tr>
            <tr><td>Margin of safety</td><td>{pct(valuation.margin_of_safety)}<span>required {pct(valuation.required_margin_of_safety)}</span></td></tr>
            <tr><td>ROIC minus WACC</td><td className={(analysis.economic_spread ?? 0) >= 0 ? 'positive' : 'negative'}>{pct(analysis.economic_spread)}</td></tr>
            <tr><td>Altman Z / Piotroski F</td><td>{num(analysis.altman_z)} / {analysis.piotroski ? `${analysis.piotroski.score} of ${analysis.piotroski.max}` : 'n/a'}</td></tr>
          </tbody>
        </table>
      </details>

      <details className="analysis-section">
        <summary>Scorecard detail</summary>
        {analysis.pillars.map((pillar) => (
          <div key={pillar.key} className="scorecard">
            <h5>{pillar.label} <span className={`score-chip ${scoreClass(pillar.score)}`}>{pillar.score === null ? 'n/a' : pillar.score.toFixed(0)}</span></h5>
            <table className="mini-table">
              <tbody>
                {pillar.metrics.filter((metric) => metric.value !== null || metric.score !== null).map((metric) => (
                  <tr key={metric.key}>
                    <td>{metric.label}{metric.note && <span>{metric.note}</span>}</td>
                    <td>{metricText(metric)}</td>
                    <td><span className={`score-chip ${scoreClass(metric.score)}`}>{metric.score === null ? 'info' : metric.score.toFixed(0)}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
      </details>

      {analysis.annual.length > 0 && (
        <details className="analysis-section">
          <summary>Annual trend</summary>
          <div className="table-scroll">
            <table className="mini-table trend-table">
              <thead>
                <tr><th></th>{analysis.annual.map((row) => <th key={row.fiscal_date}>FY{row.fiscal_date.slice(0, 4)}</th>)}</tr>
              </thead>
              <tbody>
                {ANNUAL_LINES.map((line) => (
                  <tr key={line.key}>
                    <td>{line.label}</td>
                    {analysis.annual.map((row) => <td key={row.fiscal_date}>{annualCell(row[line.key], line.format)}</td>)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}

      {analysis.piotroski && (
        <details className="analysis-section">
          <summary>Piotroski F-score {analysis.piotroski.score} of {analysis.piotroski.max} (FY{analysis.piotroski.fiscal_date.slice(0, 4)})</summary>
          <ul className="checklist">
            {analysis.piotroski.checks.map((check) => (
              <li key={check.name} className={check.passed ? 'positive' : 'negative'}>{check.passed ? '✓' : '✗'} {check.name}</li>
            ))}
          </ul>
        </details>
      )}
    </div>
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

const GRADE_CLASS: Record<string, string> = { A: 'buy', B: 'hold', C: 'watch', PASS: 'none', AVOID: 'avoid' };

const ADVICE_CLASS: Record<string, string> = {
  'TAKE PROFIT': 'signal buy',
  HOLD: 'signal hold',
  'CLOSE OR ROLL': 'signal watch',
  'ROLL / DEFEND': 'signal sell-review',
};

function PutSignalsPanel() {
  const [scan, setScan] = useState<OptionsScan | null>(null);
  const [defaults, setDefaults] = useState<OptionsDefaults | null>(null);
  const [form, setForm] = useState<ScanForm>({ tickers: '', source: 'auto', onlyDipping: false, includePositions: false, useFundamentals: true });
  const [scanning, setScanning] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  async function load() {
    try {
      setScan(await request<OptionsScan>('/options/latest'));
      setNotice('');
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      if (ONLINE) setNotice(message);
      else setNotice(message.includes('No put-selling scan') ? 'No scan yet: choose tickers and press Run scan.' : '');
    }
  }

  useEffect(() => {
    load();
    // Online, the scan is published once a day when the site is rebuilt; there is no backend to run one.
    if (ONLINE) return undefined;
    request<OptionsDefaults>('/options/defaults')
      .then((value) => {
        setDefaults(value);
        setForm((current) => ({ ...current, tickers: current.tickers || value.tickers.join(' '), includePositions: value.positions_enabled && value.moomoo_available }));
      })
      .catch(() => setDefaults(null));
    const timer = window.setInterval(load, 60_000);
    return () => window.clearInterval(timer);
  }, []);

  async function runScan(event: React.FormEvent) {
    event.preventDefault();
    const tickers = form.tickers.split(/[\s,]+/).map((ticker) => ticker.trim().toUpperCase()).filter(Boolean);
    if (tickers.length === 0) {
      setError('Enter at least one ticker.');
      return;
    }
    setScanning(true);
    setError('');
    try {
      setScan(
        await request<OptionsScan>('/options/scan', {
          method: 'POST',
          body: JSON.stringify({
            tickers,
            source: form.source,
            only_dipping: form.onlyDipping,
            include_positions: form.includePositions,
            fundamentals: form.useFundamentals ? 'dashboard' : 'off',
          }),
        }),
      );
      setNotice('');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Scan failed');
    } finally {
      setScanning(false);
    }
  }

  const scanForm = ONLINE ? (
    <section className="panel wide-row">
      <p className="history-note">
        Scanned after each US close on delayed Yahoo data for the tickers in <code>pages/watchlist.yaml</code>. Thresholds live in options/config.yaml.
      </p>
    </section>
  ) : (
    <section className="panel wide-row">
      <form className="scan-form" onSubmit={runScan}>
        <label className="scan-tickers">
          Tickers
          <input value={form.tickers} onChange={(e) => setForm({ ...form, tickers: e.target.value })} placeholder="GOOGL MSFT AMZN INTC" />
        </label>
        <label>
          Data
          <select value={form.source} onChange={(e) => setForm({ ...form, source: e.target.value as ScanForm['source'] })}>
            <option value="auto">Auto {defaults ? (defaults.moomoo_available ? '(Moomoo)' : '(Yahoo)') : ''}</option>
            <option value="moomoo">Moomoo OpenD</option>
            <option value="yahoo">Yahoo (delayed)</option>
          </select>
        </label>
        <label className="check"><input type="checkbox" checked={form.onlyDipping} onChange={(e) => setForm({ ...form, onlyDipping: e.target.checked })} /> Only stocks going down</label>
        <label className="check"><input type="checkbox" checked={form.useFundamentals} onChange={(e) => setForm({ ...form, useFundamentals: e.target.checked })} /> Check fundamentals</label>
        <label className="check"><input type="checkbox" checked={form.includePositions} onChange={(e) => setForm({ ...form, includePositions: e.target.checked })} /> My IBKR short puts</label>
        <button className="primary" disabled={scanning}>
          <RefreshCw size={16} className={scanning ? 'spin' : ''} /> {scanning ? 'Scanning…' : 'Run scan'}
        </button>
      </form>
      <p className="history-note">
        {scanning
          ? 'Fetching prices, option chains, earnings dates and fundamentals. Yahoo takes seconds per ticker; Moomoo is rate-limited and can take a few minutes.'
          : defaults
            ? `Puts ${defaults.min_dte}-${defaults.max_dte} days out, delta ${defaults.delta_min} to ${defaults.delta_max}, earnings before expiry: ${defaults.earnings}. Thresholds live in options/config.yaml.`
            : 'Graded on premium richness, odds, earnings risk, the quality and price of the stock, time to expiry and liquidity.'}
      </p>
      {error && <div className="alert compact-alert">{error}</div>}
    </section>
  );

  if (!scan) {
    return (
      <div className="grid">
        {scanForm}
        <section className="panel wide-row">{notice ? <p className="empty">{notice}</p> : <p className="empty">Loading latest scan…</p>}</section>
      </div>
    );
  }

  const dipping = scan.dips.filter((row) => row.is_dip);
  const errors = Object.entries(scan.errors);
  const rejected = Object.entries(scan.rejections ?? {}).filter(([ticker]) => !scan.candidates.some((put) => put.ticker === ticker));
  return (
    <div className="grid">
      {scanForm}
      {scan.warnings.map((warning) => <div className="notice wide-row" key={warning}>{warning}</div>)}
      <section className="panel wide-row">
        <div className="section-head">
          <h4>Top puts to sell</h4>
          <span>
            {new Date(scan.generated_at).toLocaleString()} · {scan.source ? `${scan.source} · ` : ''}{scan.universe.length} scanned · {dipping.length} going down · ranked by {scan.ranking_metric}
            {' '}
            <button className="ghost" onClick={load} title="Reload latest scan"><RefreshCw size={14} /></button>
          </span>
        </div>
        {scan.candidates.length === 0 ? (
          <p className="empty">No puts passed the filters in this scan. See why below.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Grade</th><th>Contract</th><th>DTE</th><th>Spot</th><th>OTM</th><th>Bid / Ask</th><th>Premium</th>
                <th>Delta</th><th>PoP</th><th>IV / RV</th><th>IV rank</th><th>Ann. yield</th><th>Breakeven</th><th>Earnings</th>
                <th>Verdict</th><th>OI</th><th>Held</th>
              </tr>
            </thead>
            <tbody>
              {scan.candidates.map((put) => (
                <tr key={put.code} title={[...put.reasons.map((text) => `+ ${text}`), ...put.cautions.map((text) => `! ${text}`)].join('\n')}>
                  <td>
                    <span className={`signal ${GRADE_CLASS[put.grade ?? ''] ?? 'none'}`}>{put.grade ?? '-'}</span>
                    <span>{num(put.put_score, 0)}</span>
                  </td>
                  <td><span className="ticker">{put.ticker} {put.strike}P</span><span>{put.expiry}</span></td>
                  <td>{put.dte}</td>
                  <td>{num(put.spot)}</td>
                  <td>{pct(put.otm_pct)}</td>
                  <td>{num(put.bid)} / {num(put.ask)}</td>
                  <td>{num(put.premium)}<span>{money(put.premium * 100)} / ct</span></td>
                  <td>{num(put.delta)}{put.delta_estimated ? '*' : ''}</td>
                  <td>{pct(put.prob_profit)}<span>{num(put.strike_sd, 1)} exp. moves</span></td>
                  <td>{pct(put.iv)}<span>{put.contract_iv_hv !== null ? `${num(put.contract_iv_hv)}x realized` : ''}</span></td>
                  <td>{num(put.iv_rank, 0)}</td>
                  <td><strong>{pct(put.annualized_yield)}</strong></td>
                  <td>{num(put.breakeven)}<span>{pct(put.cushion_pct)} cushion</span></td>
                  <td className={put.earnings_in_trade ? 'negative' : ''}>{put.earnings_date ?? '-'}<span>{put.earnings_in_trade ? 'before expiry' : ''}</span></td>
                  <td>{put.verdict ? <span className={signalClass(put.verdict)}>{actionLabel(put.verdict)}</span> : '-'}<span>{put.breakeven_discount !== null ? `BE ${pct(Math.abs(put.breakeven_discount))} ${put.breakeven_discount >= 0 ? 'below' : 'above'} fair value` : ''}</span></td>
                  <td>{put.open_interest ?? 'n/a'}</td>
                  <td>{scan.exposure[put.ticker] ? `${scan.exposure[put.ticker].contracts} ct` : '-'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {scan.candidates.some((put) => put.grade) && (
          <div className="put-reasons">
            {scan.candidates.slice(0, 12).map((put, index) => (
              <div className="put-reason" key={put.code}>
                <div>
                  <span className={`signal ${GRADE_CLASS[put.grade ?? ''] ?? 'none'}`}>{put.grade ?? '-'}</span>
                  <strong>{index + 1}. {put.ticker} {put.strike}P {put.expiry}</strong>
                  <small>score {num(put.put_score, 0)} · {pct(put.prob_profit)} PoP · {pct(put.annualized_yield)} ann.</small>
                </div>
                <ul>
                  {put.reasons.map((text) => <li key={text} className="positive">✔ {text}</li>)}
                  {put.cautions.map((text) => <li key={text} className="negative">⚠ {text}</li>)}
                </ul>
              </div>
            ))}
          </div>
        )}
        {rejected.length > 0 && (
          <div className="rejections">
            <h4>Why some tickers have no puts</h4>
            <ul>
              {rejected.map(([ticker, reasons]) => (
                <li key={ticker}>
                  <strong>{ticker}</strong>: {Object.entries(reasons).sort((a, b) => b[1] - a[1]).map(([reason, count]) => `${reason} (${count})`).join(', ') || 'no contracts returned'}
                </li>
              ))}
            </ul>
          </div>
        )}
      </section>

      {!ONLINE && <section className="panel">
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
      </section>}

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

function OnlineSettings() {
  const [status, setStatus] = useState<{ generated_at: string; tickers: string[]; errors: Record<string, string> } | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    dataStatus().then(setStatus).catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, []);

  function clearData() {
    if (!window.confirm('Delete the holdings and manually entered fundamentals stored in this browser?')) return;
    clearAll();
    window.location.reload();
  }

  const errors = Object.entries(status?.errors ?? {});
  return (
    <section className="panel settings-panel">
      <h3>Online Configuration</h3>
      {error && <div className="alert">{error}</div>}
      <dl>
        <dt>Market data</dt><dd>Yahoo Finance daily data, fetched by a GitHub Actions job after each US close</dd>
        <dt>Last update</dt><dd>{status ? new Date(status.generated_at).toLocaleString() : 'Loading…'}</dd>
        <dt>Watchlist</dt><dd>{status ? status.tickers.join(', ') || 'Empty' : 'Loading…'} · edit <code>pages/watchlist.yaml</code> on GitHub</dd>
        <dt>Put scanner</dt><dd>Thresholds from <code>options/config.yaml</code>; delta estimated with Black-Scholes (Yahoo has no greeks or IV rank)</dd>
        <dt>Your data</dt><dd>Holdings and manually entered fundamentals are stored only in this browser</dd>
        <dt>Execution</dt><dd>Signals only. This app never places trades.</dd>
      </dl>
      {errors.length > 0 && (
        <div className="notice">Last update could not fetch: {errors.map(([ticker, message]) => `${ticker} (${message})`).join('; ')}</div>
      )}
      <button className="secondary" onClick={clearData}>
        <Trash2 size={16} /> Clear Data in This Browser
      </button>
    </section>
  );
}

createRoot(document.getElementById('root')!).render(<App />);

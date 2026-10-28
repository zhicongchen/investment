// Answers the dashboard's backend API calls inside the browser for the GitHub Pages
// build: market data from Tradier (and FMP when a key is set), state in localStorage.
import { BENCHMARK_TICKER, enrichPricePoints, latestClose, marketComparison, portfolioAnalytics, summaryMetrics } from './analytics';
import { FmpClient, type FundamentalsData } from './fmp';
import { runScan, nyDate, shiftDays, type ScanResult } from './scanner';
import { computeSignal, type HoldingRecord, type SignalDecision } from './scoring';
import { loadScanConfig, loadSettings } from './settings';
import { readJson, removeKey, writeJson } from './storage';
import { TradierClient, type Bar } from './tradier';

type Company = { ticker: string; name: string; sector: string; exchange: string };
// [date, open, high, low, close, volume] keeps five years of bars per ticker small.
type CompactBar = [string, number | null, number | null, number | null, number | null, number | null];

const HISTORY_YEARS = 5;
const SIGNAL_HISTORY = 30;
const STALE_DAYS = 5;

class HttpError extends Error {}

export async function browserRequest<T>(path: string, options?: RequestInit): Promise<T> {
  const method = (options?.method ?? 'GET').toUpperCase();
  const body = options?.body ? JSON.parse(String(options.body)) : {};
  const parts = path.split('?')[0].split('/').filter(Boolean).map(decodeURIComponent);
  return (await route(method, parts, body)) as T;
}

async function route(method: string, parts: string[], body: Record<string, unknown>): Promise<unknown> {
  const [head, second, third] = parts;
  const key = `${method} /${head ?? ''}`;
  switch (key) {
    case 'GET /health':
      return { status: 'ok' };
    case 'GET /watchlist':
      return listWatchlist();
    case 'POST /watchlist':
      return addCompany(String(body.ticker ?? ''));
    case 'DELETE /watchlist':
      return removeCompany(second);
    case 'POST /refresh':
      return second ? refreshSome([symbol(second)], false) : refreshSome(watchlist().map((item) => item.ticker), true);
    case 'GET /companies':
      return companyDetail(second);
    case 'PUT /companies':
      if (third === 'fundamentals') return saveFundamentals(second, body);
      break;
    case 'GET /holdings':
      return listHoldings();
    case 'PUT /holdings':
      return upsertHolding(second, body);
    case 'DELETE /holdings':
      return deleteHolding(second);
    case 'GET /analytics':
      if (second === 'market') return marketComparison(priceMap(watchlist().map((item) => item.ticker)), bars(BENCHMARK_TICKER));
      if (second === 'portfolio') return portfolioAnalytics(holdingInputs(), priceMap(Object.keys(holdings())), bars(BENCHMARK_TICKER));
      break;
    case 'GET /options':
      if (second === 'latest') return latestScan();
      break;
    case 'POST /options':
      if (second === 'scan') return scanPuts();
      break;
    case 'GET /ollama':
      if (second === 'status') return { base_url: 'n/a', model: 'n/a', reachable: false, error: 'Ollama runs only in the local version.' };
      return [];
  }
  throw new HttpError(`${method} /${parts.join('/')} is not available in the online version.`);
}

// ---- state ----

function symbol(value: string | undefined): string {
  const ticker = (value ?? '').trim().toUpperCase();
  if (!/^[A-Z][A-Z0-9.\-]{0,9}$/.test(ticker)) throw new HttpError(`"${value ?? ''}" is not a valid US ticker.`);
  return ticker;
}

function watchlist(): Company[] {
  return readJson<Company[]>('watchlist', []);
}

function companies(): Record<string, Company> {
  return readJson<Record<string, Company>>('companies', {});
}

function bars(ticker: string): Bar[] {
  return readJson<CompactBar[]>(`prices:${ticker}`, []).map(([date, open, high, low, close, volume]) => ({ date, open, high, low, close, volume }));
}

function saveBars(ticker: string, rows: Bar[]): void {
  writeJson(
    `prices:${ticker}`,
    rows.map((bar): CompactBar => [bar.date, bar.open, bar.high, bar.low, bar.close, bar.volume]),
  );
}

function priceMap(tickers: string[]): Record<string, Bar[]> {
  return Object.fromEntries(tickers.map((ticker) => [ticker, bars(ticker)]));
}

function fundamentals(): Record<string, FundamentalsData> {
  return readJson<Record<string, FundamentalsData>>('fundamentals', {});
}

function holdings(): Record<string, HoldingRecord> {
  return readJson<Record<string, HoldingRecord>>('holdings', {});
}

function holdingInputs() {
  return Object.entries(holdings())
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([ticker, holding]) => ({ ticker, shares: holding.shares, target_allocation: holding.target_allocation }));
}

function signals(): Record<string, SignalDecision[]> {
  return readJson<Record<string, SignalDecision[]>>('signals', {});
}

function portfolioValue(): number {
  return Object.entries(holdings()).reduce((total, [ticker, holding]) => {
    const close = latestClose(bars(ticker));
    return close === null ? total : total + holding.shares * close;
  }, 0);
}

function recomputeSignal(ticker: string): void {
  const decision = computeSignal(bars(ticker), fundamentals()[ticker] ?? null, holdings()[ticker] ?? null, portfolioValue(), nyDate(new Date()));
  const all = signals();
  all[ticker] = [decision, ...(all[ticker] ?? []).filter((item) => item.date !== decision.date)].slice(0, SIGNAL_HISTORY);
  writeJson('signals', all);
}

function today(): string {
  return nyDate(new Date());
}

// ---- watchlist and companies ----

function listWatchlist() {
  const benchmark = bars(BENCHMARK_TICKER);
  const staleBefore = shiftDays(today(), -STALE_DAYS);
  return watchlist().map((company) => {
    const rows = bars(company.ticker);
    const latest = rows.length ? rows[rows.length - 1] : null;
    return {
      ...company,
      latest_price: latest ? latest.close : null,
      latest_price_date: latest ? latest.date : null,
      signal: signals()[company.ticker]?.[0] ?? null,
      stale: latest === null || latest.date < staleBefore,
      metrics: summaryMetrics(rows, benchmark),
    };
  });
}

function ensureCompany(ticker: string): Company {
  const known = companies();
  if (!known[ticker]) {
    known[ticker] = { ticker, name: ticker, sector: '', exchange: '' };
    writeJson('companies', known);
  }
  return known[ticker];
}

async function addCompany(raw: string) {
  const ticker = symbol(raw);
  const company = ensureCompany(ticker);
  const list = watchlist();
  if (!list.some((item) => item.ticker === ticker)) {
    writeJson('watchlist', [...list, company].sort((a, b) => a.ticker.localeCompare(b.ticker)));
  }
  // Fetch data right away so a new row is not empty; failures surface on the next refresh.
  await refreshSome([ticker], false).catch(() => undefined);
  return { ...company, active: true };
}

function removeCompany(raw: string | undefined) {
  const ticker = symbol(raw);
  const list = watchlist();
  if (!list.some((item) => item.ticker === ticker)) throw new HttpError('Ticker not found');
  writeJson('watchlist', list.filter((item) => item.ticker !== ticker));
  if (!holdings()[ticker] && ticker !== BENCHMARK_TICKER) removeKey(`prices:${ticker}`);
  return { status: 'removed' };
}

function companyDetail(raw: string | undefined) {
  const ticker = symbol(raw);
  const company = companies()[ticker];
  if (!company) throw new HttpError('Ticker not found');
  const rows = bars(ticker);
  const holding = holdings()[ticker];
  return {
    company: { ...company, active: watchlist().some((item) => item.ticker === ticker) },
    prices: enrichPricePoints(rows),
    metrics: summaryMetrics(rows, bars(BENCHMARK_TICKER)),
    fundamentals: fundamentals()[ticker] ?? null,
    signals: signals()[ticker] ?? [],
    holding: holding ? holdingRead(ticker, holding) : null,
  };
}

function saveFundamentals(raw: string | undefined, body: Record<string, unknown>) {
  const ticker = symbol(raw);
  ensureCompany(ticker);
  const field = (name: string) => (typeof body[name] === 'number' && Number.isFinite(body[name]) ? (body[name] as number) : null);
  const snapshot: FundamentalsData = {
    date: today(),
    pe: field('pe'),
    forward_pe: field('forward_pe'),
    eps_growth: field('eps_growth'),
    revenue_growth: field('revenue_growth'),
    debt_to_equity: field('debt_to_equity'),
    free_cash_flow: field('free_cash_flow'),
    dividend_yield: field('dividend_yield'),
  };
  writeJson('fundamentals', { ...fundamentals(), [ticker]: snapshot });
  recomputeSignal(ticker);
  return snapshot;
}

// ---- refresh ----

async function refreshSome(tickers: string[], includeBenchmark: boolean) {
  const settings = loadSettings();
  const tradier = new TradierClient(settings.tradierToken, settings.tradierSandbox);
  const fmp = settings.fmpKey ? new FmpClient(settings.fmpKey) : null;
  const wanted = [...new Set([...tickers, ...(includeBenchmark || !bars(BENCHMARK_TICKER).length ? [BENCHMARK_TICKER] : [])])];
  const refreshed: string[] = [];
  const failed: Record<string, string> = {};
  const start = shiftDays(today(), -365 * HISTORY_YEARS);

  const quotes = await tradier.quotes(wanted).catch(() => ({}) as Awaited<ReturnType<TradierClient['quotes']>>);
  for (const ticker of wanted) {
    try {
      const history = await tradier.dailyBars(ticker, start);
      if (!history.length) throw new Error('Tradier returned no price history');
      saveBars(ticker, history);

      const known = companies();
      const company = known[ticker] ?? { ticker, name: ticker, sector: '', exchange: '' };
      const quote = quotes[ticker];
      if (quote) {
        company.name = quote.name || company.name;
        company.exchange = quote.exchange || company.exchange;
      }
      if (fmp) {
        try {
          const profile = await fmp.profile(ticker);
          company.name = profile.name || company.name;
          company.sector = profile.sector || company.sector;
          company.exchange = profile.exchange || company.exchange;
          writeJson('fundamentals', { ...fundamentals(), [ticker]: await fmp.fundamentals(ticker, today()) });
        } catch (err) {
          failed[ticker] = `prices updated; fundamentals failed: ${err instanceof Error ? err.message : String(err)}`;
        }
      }
      writeJson('companies', { ...known, [ticker]: company });
      if (watchlist().some((item) => item.ticker === ticker)) {
        writeJson('watchlist', watchlist().map((item) => (item.ticker === ticker ? company : item)));
      }
      if (ticker !== BENCHMARK_TICKER || tickers.includes(ticker)) {
        recomputeSignal(ticker);
        if (!failed[ticker]) refreshed.push(ticker);
      }
    } catch (err) {
      failed[ticker] = err instanceof Error ? err.message : String(err);
    }
  }
  return { refreshed, failed };
}

// ---- holdings ----

function holdingRead(ticker: string, holding: HoldingRecord) {
  const close = latestClose(bars(ticker));
  const marketValue = close !== null ? holding.shares * close : 0;
  const total = portfolioValue();
  return {
    ticker,
    ...holding,
    market_value: Math.round(marketValue * 100) / 100,
    unrealized_gain_loss: Math.round((marketValue - holding.shares * holding.average_cost) * 100) / 100,
    current_allocation: total ? Math.round((marketValue / total) * 10_000) / 10_000 : 0,
  };
}

function listHoldings() {
  return Object.entries(holdings())
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([ticker, holding]) => holdingRead(ticker, holding));
}

async function upsertHolding(raw: string | undefined, body: Record<string, unknown>) {
  const ticker = symbol(raw);
  ensureCompany(ticker);
  const holding: HoldingRecord = {
    shares: Number(body.shares ?? 0),
    average_cost: Number(body.average_cost ?? 0),
    target_allocation: Number(body.target_allocation ?? 0),
    notes: String(body.notes ?? ''),
  };
  writeJson('holdings', { ...holdings(), [ticker]: holding });
  if (!bars(ticker).length) await refreshSome([ticker], false).catch(() => undefined);
  recomputeSignal(ticker);
  return holdingRead(ticker, holding);
}

function deleteHolding(raw: string | undefined) {
  const ticker = symbol(raw);
  const all = holdings();
  if (!all[ticker]) throw new HttpError('Holding not found');
  delete all[ticker];
  writeJson('holdings', all);
  recomputeSignal(ticker);
  return { status: 'deleted' };
}

// ---- put scanner ----

function latestScan(): ScanResult {
  const scan = readJson<ScanResult | null>('options-scan', null);
  if (!scan) throw new HttpError('No put-selling scan yet. Click "Scan now".');
  return scan;
}

async function scanPuts(): Promise<ScanResult> {
  const settings = loadSettings();
  const config = loadScanConfig();
  const client = new TradierClient(settings.tradierToken, settings.tradierSandbox);
  const exclude = new Set(config.exclude_tickers.map((ticker) => ticker.toUpperCase()));
  const universe = [...new Set([...watchlist().map((item) => item.ticker), ...config.extra_tickers.map((ticker) => ticker.toUpperCase())])]
    .filter((ticker) => !exclude.has(ticker))
    .sort();
  const result = await runScan(client, universe, config, new Date());
  if (settings.tradierSandbox) result.warnings.push('Tradier sandbox data is delayed 15 minutes.');
  result.warnings.push('IV rank is not available from Tradier, so it shows n/a.');
  writeJson('options-scan', result);
  return result;
}

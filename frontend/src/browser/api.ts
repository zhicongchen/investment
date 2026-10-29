// Answers the dashboard's backend API calls inside the browser for the GitHub Pages build.
// Market data is the static JSON that pages/build.py publishes after each US close; holdings
// and manual fundamentals stay in this browser's localStorage.
import { BENCHMARK_TICKER, enrichPricePoints, latestClose, marketComparison, portfolioAnalytics, summaryMetrics, type Bar } from './analytics';
import { computeSignal, type FundamentalsData, type HoldingRecord } from './scoring';
import { readJson, writeJson } from './storage';

type Company = { ticker: string; name: string; sector: string; exchange: string };
type Market = {
  generated_at: string;
  benchmark: string;
  tickers: Company[];
  fundamentals: Record<string, FundamentalsData>;
  errors: Record<string, string>;
};
// [date, open, high, low, close, volume, adjusted close]
type PriceRow = [string, number | null, number | null, number | null, number | null, number | null, number | null];

const STALE_DAYS = 5;
const DATA_BASE = `${import.meta.env?.BASE_URL ?? '/'}data/`;
const WATCHLIST_NOTE = 'The online watchlist is the list in pages/watchlist.yaml. Edit it on GitHub and the site rebuilds.';

let marketCache: Promise<Market> | null = null;
const priceCache = new Map<string, Promise<Bar[]>>();

class HttpError extends Error {}

export async function browserRequest<T>(path: string, options?: RequestInit): Promise<T> {
  const method = (options?.method ?? 'GET').toUpperCase();
  const body = options?.body ? JSON.parse(String(options.body)) : {};
  const parts = path.split('?')[0].split('/').filter(Boolean).map(decodeURIComponent);
  return (await route(method, parts, body)) as T;
}

/** When the published data was generated, for the Settings panel. */
export async function dataStatus(): Promise<{ generated_at: string; tickers: string[]; errors: Record<string, string> }> {
  const data = await market();
  return { generated_at: data.generated_at, tickers: data.tickers.map((item) => item.ticker), errors: data.errors };
}

async function route(method: string, parts: string[], body: Record<string, unknown>): Promise<unknown> {
  const [head, second, third] = parts;
  switch (`${method} /${head ?? ''}`) {
    case 'GET /health':
      return { status: 'ok' };
    case 'GET /watchlist':
      return listWatchlist();
    case 'POST /watchlist':
    case 'DELETE /watchlist':
      throw new HttpError(WATCHLIST_NOTE);
    case 'POST /refresh':
      return reload();
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
      if (second === 'market') {
        const tickers = (await market()).tickers.map((item) => item.ticker);
        return marketComparison(await priceMap(tickers), await bars(BENCHMARK_TICKER));
      }
      if (second === 'portfolio') {
        return portfolioAnalytics(holdingInputs(), await priceMap(Object.keys(holdings())), await bars(BENCHMARK_TICKER));
      }
      break;
    case 'GET /options':
      if (second === 'latest') return fetchJson('options-scan.json');
      break;
    case 'GET /ollama':
      if (second === 'status') return { base_url: 'n/a', model: 'n/a', reachable: false, error: 'Ollama runs only in the local version.' };
      return [];
  }
  throw new HttpError(`${method} /${parts.join('/')} is not available in the online version.`);
}

// ---- published data ----

async function fetchJson<T>(file: string, fresh = false): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${DATA_BASE}${file}`, { cache: fresh ? 'no-cache' : 'default' });
  } catch {
    throw new HttpError('Could not load the published market data. Check your connection.');
  }
  if (response.status === 404) throw new HttpError(`${file} has not been published yet. Run the "Deploy dashboard to GitHub Pages" workflow.`);
  if (!response.ok) throw new HttpError(`Loading ${file} failed: ${response.status}`);
  return (await response.json()) as T;
}

function market(fresh = false): Promise<Market> {
  if (!marketCache || fresh) {
    marketCache = fetchJson<Market>('market.json', fresh);
    marketCache.catch(() => {
      marketCache = null;
    });
  }
  return marketCache;
}

function bars(ticker: string): Promise<Bar[]> {
  if (!priceCache.has(ticker)) {
    const request = fetchJson<PriceRow[]>(`prices/${encodeURIComponent(ticker)}.json`)
      .then((rows) =>
        rows.map(([date, open, high, low, close, volume, adjusted_close]) => ({ date, open, high, low, close, volume, adjusted_close })),
      )
      // Tickers that are only holdings have no published prices.
      .catch(() => [] as Bar[]);
    priceCache.set(ticker, request);
  }
  return priceCache.get(ticker) as Promise<Bar[]>;
}

async function priceMap(tickers: string[]): Promise<Record<string, Bar[]>> {
  return Object.fromEntries(await Promise.all(tickers.map(async (ticker) => [ticker, await bars(ticker)] as const)));
}

async function reload() {
  priceCache.clear();
  const data = await market(true);
  return { refreshed: data.tickers.map((item) => item.ticker), failed: data.errors };
}

// ---- browser-only state ----

function symbol(value: string | undefined): string {
  const ticker = (value ?? '').trim().toUpperCase();
  if (!/^[A-Z][A-Z0-9.\-]{0,9}$/.test(ticker)) throw new HttpError(`"${value ?? ''}" is not a valid US ticker.`);
  return ticker;
}

function manualFundamentals(): Record<string, FundamentalsData> {
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

async function portfolioValue(): Promise<number> {
  let total = 0;
  for (const [ticker, holding] of Object.entries(holdings())) {
    const close = latestClose(await bars(ticker));
    if (close !== null) total += holding.shares * close;
  }
  return total;
}

/** Manual entries made in this browser win over the published Yahoo fundamentals. */
async function fundamentalsFor(ticker: string): Promise<FundamentalsData | null> {
  return manualFundamentals()[ticker] ?? (await market()).fundamentals[ticker] ?? null;
}

async function signalFor(ticker: string, rows: Bar[], total: number) {
  return computeSignal(rows, await fundamentalsFor(ticker), holdings()[ticker] ?? null, total, rows.length ? rows[rows.length - 1].date : '');
}

function today(): string {
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York' }).format(new Date());
}

// ---- endpoints ----

async function listWatchlist() {
  const data = await market();
  const [benchmark, total] = await Promise.all([bars(BENCHMARK_TICKER), portfolioValue()]);
  const staleBefore = new Date(Date.parse(`${today()}T00:00:00Z`) - STALE_DAYS * 86_400_000).toISOString().slice(0, 10);
  return Promise.all(
    data.tickers.map(async (company) => {
      const rows = await bars(company.ticker);
      const latest = rows.length ? rows[rows.length - 1] : null;
      return {
        ...company,
        latest_price: latest ? latest.adjusted_close ?? latest.close : null,
        latest_price_date: latest ? latest.date : null,
        signal: rows.length ? await signalFor(company.ticker, rows, total) : null,
        stale: latest === null || latest.date < staleBefore,
        metrics: summaryMetrics(rows, benchmark),
      };
    }),
  );
}

async function companyDetail(raw: string | undefined) {
  const ticker = symbol(raw);
  const data = await market();
  const company = data.tickers.find((item) => item.ticker === ticker);
  if (!company && !holdings()[ticker]) throw new HttpError('Ticker not found');
  const rows = await bars(ticker);
  const holding = holdings()[ticker];
  const total = await portfolioValue();
  return {
    company: { ...(company ?? { ticker, name: ticker, sector: '', exchange: '' }), active: Boolean(company) },
    prices: enrichPricePoints(rows),
    metrics: summaryMetrics(rows, await bars(BENCHMARK_TICKER)),
    fundamentals: await fundamentalsFor(ticker),
    signals: rows.length ? [await signalFor(ticker, rows, total)] : [],
    holding: holding ? await holdingRead(ticker, holding, total) : null,
  };
}

function saveFundamentals(raw: string | undefined, body: Record<string, unknown>) {
  const ticker = symbol(raw);
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
  writeJson('fundamentals', { ...manualFundamentals(), [ticker]: snapshot });
  return snapshot;
}

async function holdingRead(ticker: string, holding: HoldingRecord, total: number) {
  const close = latestClose(await bars(ticker));
  const marketValue = close !== null ? holding.shares * close : 0;
  return {
    ticker,
    ...holding,
    market_value: Math.round(marketValue * 100) / 100,
    unrealized_gain_loss: Math.round((marketValue - holding.shares * holding.average_cost) * 100) / 100,
    current_allocation: total ? Math.round((marketValue / total) * 10_000) / 10_000 : 0,
  };
}

async function listHoldings() {
  const total = await portfolioValue();
  return Promise.all(
    Object.entries(holdings())
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([ticker, holding]) => holdingRead(ticker, holding, total)),
  );
}

async function upsertHolding(raw: string | undefined, body: Record<string, unknown>) {
  const ticker = symbol(raw);
  const holding: HoldingRecord = {
    shares: Number(body.shares ?? 0),
    average_cost: Number(body.average_cost ?? 0),
    target_allocation: Number(body.target_allocation ?? 0),
    notes: String(body.notes ?? ''),
  };
  writeJson('holdings', { ...holdings(), [ticker]: holding });
  return holdingRead(ticker, holding, await portfolioValue());
}

function deleteHolding(raw: string | undefined) {
  const ticker = symbol(raw);
  const all = holdings();
  if (!all[ticker]) throw new HttpError('Holding not found');
  delete all[ticker];
  writeJson('holdings', all);
  return { status: 'deleted' };
}

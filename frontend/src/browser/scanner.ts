// Port of options/dips.py, options/pricing.py, options/puts.py and options/scanner.py
// running on Tradier data. IV rank and IBKR positions are not available here.
import { sum } from './analytics';
import type { ScanConfig } from './settings';
import type { Bar, OptionQuote, StockQuote, TradierClient } from './tradier';

const CONTRACT_MULTIPLIER = 100;
const DAY_MS = 86_400_000;

type DailyBar = { date: string; high: number; close: number };

export type DipMetrics = {
  ticker: string;
  spot: number;
  day_change_pct: number | null;
  off_high_pct: number | null;
  n_day_return_pct: number | null;
  rsi: number | null;
  moving_average: number | null;
  triggers: string[];
  blocked_by: string[];
  is_dip: boolean;
};

export type UnderlyingVol = { iv: number | null; iv_rank: number | null; hv_30d: number | null; iv_hv_ratio: number | null };

export type PutCandidate = {
  ticker: string;
  code: string;
  expiry: string;
  dte: number;
  strike: number;
  spot: number;
  bid: number;
  ask: number;
  premium: number;
  spread_pct: number;
  delta: number | null;
  delta_estimated: boolean;
  iv: number | null;
  open_interest: number | null;
  volume: number | null;
  otm_pct: number;
  annualized_yield: number;
  breakeven: number;
  cushion_pct: number;
  collateral: number;
  iv_rank: number | null;
  iv_hv_ratio: number | null;
  contracts: number | null;
  score: number;
};

export type ScanResult = {
  generated_at: string;
  ranking_metric: string;
  universe: string[];
  dips: DipMetrics[];
  candidates: PutCandidate[];
  vols: Record<string, UnderlyingVol>;
  positions_status: string;
  positions: never[];
  exposure: Record<string, { contracts: number; collateral: number }>;
  rejections: Record<string, Record<string, number>>;
  errors: Record<string, string>;
  warnings: string[];
};

// ---- dips.py ----

export function evaluateDip(ticker: string, bars: DailyBar[], config: ScanConfig['dip'], prevClose: number | null): DipMetrics {
  if (!bars.length) {
    return blankDip(ticker, ['no price history']);
  }
  const closes = bars.map((bar) => bar.close);
  const spot = closes[closes.length - 1];
  const checks = config.checks;
  const referenceClose = prevClose || (closes.length >= 2 ? closes[closes.length - 2] : null);
  const maDays = config.require_above_ma.days;

  const metrics: DipMetrics = {
    ...blankDip(ticker, []),
    spot,
    day_change_pct: pctChange(referenceClose, spot),
    off_high_pct: offHigh(bars, checks.off_high.lookback_days),
    n_day_return_pct: closes.length > checks.drop_n_days.days ? pctChange(closes[closes.length - 1 - checks.drop_n_days.days], spot) : null,
    rsi: rsi(closes, checks.rsi.period),
    moving_average: closes.length >= maDays ? mean(closes.slice(-maDays)) : null,
  };

  const results: boolean[] = [];
  if (checks.off_high.enabled) {
    const hit = metrics.off_high_pct !== null && metrics.off_high_pct >= checks.off_high.min_pct;
    results.push(hit);
    if (hit) metrics.triggers.push(`${fmtPct(metrics.off_high_pct)} below ${checks.off_high.lookback_days}d high`);
  }
  if (checks.drop_n_days.enabled) {
    const hit = metrics.n_day_return_pct !== null && metrics.n_day_return_pct <= -checks.drop_n_days.min_pct;
    results.push(hit);
    if (hit) metrics.triggers.push(`${fmtPct(metrics.n_day_return_pct, true)} over ${checks.drop_n_days.days}d`);
  }
  if (checks.rsi.enabled) {
    const hit = metrics.rsi !== null && metrics.rsi <= checks.rsi.max;
    results.push(hit);
    if (hit) metrics.triggers.push(`RSI(${checks.rsi.period}) ${(metrics.rsi as number).toFixed(0)}`);
  }
  if (checks.day_drop.enabled) {
    const hit = metrics.day_change_pct !== null && metrics.day_change_pct <= -checks.day_drop.min_pct;
    results.push(hit);
    if (hit) metrics.triggers.push(`${fmtPct(metrics.day_change_pct, true)} today`);
  }

  let passed: boolean;
  if (!results.length) {
    passed = false;
    metrics.blocked_by.push('no dip checks enabled');
  } else {
    passed = config.rule === 'all' ? results.every(Boolean) : results.some(Boolean);
  }

  if (passed && config.require_above_ma.enabled) {
    if (metrics.moving_average === null) {
      passed = false;
      metrics.blocked_by.push(`not enough history for ${maDays}d MA`);
    } else if (spot < metrics.moving_average) {
      passed = false;
      metrics.blocked_by.push(`below ${maDays}d MA ${metrics.moving_average.toFixed(2)}`);
    }
  }
  metrics.is_dip = passed;
  return metrics;
}

function blankDip(ticker: string, blockedBy: string[]): DipMetrics {
  return {
    ticker,
    spot: 0,
    day_change_pct: null,
    off_high_pct: null,
    n_day_return_pct: null,
    rsi: null,
    moving_average: null,
    triggers: [],
    blocked_by: blockedBy,
    is_dip: false,
  };
}

/** Wilder's RSI of the last close. */
export function rsi(closes: number[], period = 14): number | null {
  if (period <= 0 || closes.length <= period) return null;
  const deltas = closes.slice(1).map((close, index) => close - closes[index]);
  const gains = deltas.map((delta) => Math.max(delta, 0));
  const losses = deltas.map((delta) => Math.max(-delta, 0));
  let avgGain = sum(gains.slice(0, period)) / period;
  let avgLoss = sum(losses.slice(0, period)) / period;
  for (let index = period; index < deltas.length; index += 1) {
    avgGain = (avgGain * (period - 1) + gains[index]) / period;
    avgLoss = (avgLoss * (period - 1) + losses[index]) / period;
  }
  if (avgLoss === 0) return avgGain === 0 ? 50 : 100;
  return 100 - 100 / (1 + avgGain / avgLoss);
}

function offHigh(bars: DailyBar[], lookbackDays: number): number | null {
  if (lookbackDays <= 0 || !bars.length) return null;
  const high = Math.max(...bars.slice(-lookbackDays).map((bar) => Math.max(bar.high, bar.close)));
  if (high <= 0) return null;
  return (high - bars[bars.length - 1].close) / high;
}

function pctChange(start: number | null, end: number): number | null {
  return start ? end / start - 1 : null;
}

function mean(values: number[]): number | null {
  return values.length ? sum(values) / values.length : null;
}

function fmtPct(value: number | null, signed = false): string {
  const text = `${((value ?? 0) * 100).toFixed(1)}%`;
  return signed && (value ?? 0) >= 0 ? `+${text}` : text;
}

// ---- pricing.py ----

function normCdf(value: number): number {
  // Abramowitz-Stegun 7.1.26 erf approximation (max error 1.5e-7).
  const x = Math.abs(value) / Math.SQRT2;
  const t = 1 / (1 + 0.3275911 * x);
  const erf = 1 - ((((1.061405429 * t - 1.453152027) * t + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * Math.exp(-x * x);
  return 0.5 * (1 + (value >= 0 ? erf : -erf));
}

function d1d2(spot: number, strike: number, years: number, rate: number, vol: number): [number, number] {
  const d1 = (Math.log(spot / strike) + (rate + 0.5 * vol * vol) * years) / (vol * Math.sqrt(years));
  return [d1, d1 - vol * Math.sqrt(years)];
}

function putPrice(spot: number, strike: number, years: number, rate: number, vol: number): number {
  if (years <= 0 || vol <= 0) return Math.max(strike - spot, 0);
  const [d1, d2] = d1d2(spot, strike, years, rate, vol);
  return strike * Math.exp(-rate * years) * normCdf(-d2) - spot * normCdf(-d1);
}

export function putDelta(spot: number, strike: number, years: number, rate: number, vol: number): number | null {
  if (spot <= 0 || strike <= 0 || years <= 0 || vol <= 0) return null;
  return normCdf(d1d2(spot, strike, years, rate, vol)[0]) - 1;
}

export function impliedVol(price: number, spot: number, strike: number, years: number, rate: number): number | null {
  if (price <= 0 || spot <= 0 || strike <= 0 || years <= 0) return null;
  const intrinsic = Math.max(strike * Math.exp(-rate * years) - spot, 0);
  if (price <= intrinsic || price >= strike) return null;
  let low = 1e-4;
  let high = 5;
  for (let step = 0; step < 100 && high - low >= 1e-6; step += 1) {
    const mid = (low + high) / 2;
    if (putPrice(spot, strike, years, rate, mid) > price) high = mid;
    else low = mid;
  }
  return (low + high) / 2;
}

// ---- puts.py ----

export function selectPuts(
  spot: number,
  quotes: OptionQuote[],
  config: ScanConfig['puts'],
  today: string,
  vol: UnderlyingVol | undefined,
  sizing: ScanConfig['sizing'],
): { candidates: PutCandidate[]; rejected: Record<string, number> } {
  const candidates: PutCandidate[] = [];
  const rejected: Record<string, number> = {};
  const reject = (reason: string) => {
    rejected[reason] = (rejected[reason] ?? 0) + 1;
  };

  for (const quote of quotes) {
    const dte = daysBetween(today, quote.expiry);
    if (dte < config.min_dte || dte > config.max_dte) {
      reject('dte out of range');
      continue;
    }
    if (spot <= 0 || quote.strike >= spot) {
      reject('not out of the money');
      continue;
    }
    const otmPct = 1 - quote.strike / spot;
    if (otmPct < config.min_otm_pct || otmPct > config.max_otm_pct) {
      reject('otm % out of range');
      continue;
    }
    if (quote.bid === null || quote.ask === null || quote.bid < config.min_bid || quote.ask < quote.bid) {
      reject('bid too low / no market');
      continue;
    }
    const mid = (quote.bid + quote.ask) / 2;
    const spreadPct = mid > 0 ? (quote.ask - quote.bid) / mid : Infinity;
    if (spreadPct > config.max_spread_pct) {
      reject('spread too wide');
      continue;
    }
    if (quote.openInterest !== null && quote.openInterest < config.min_open_interest) {
      reject('open interest too low');
      continue;
    }

    const years = Math.max(dte, 1) / 365;
    const iv = quote.iv || impliedVol(mid, spot, quote.strike, years, config.risk_free_rate);
    let delta = quote.delta;
    let deltaEstimated = false;
    if (delta === null && iv) {
      delta = putDelta(spot, quote.strike, years, config.risk_free_rate, iv);
      deltaEstimated = delta !== null;
    }
    if (delta === null) {
      reject('no delta');
      continue;
    }
    if (delta < config.delta_min || delta > config.delta_max) {
      reject('delta out of range');
      continue;
    }

    const premium = config.price_basis === 'mid' ? mid : quote.bid;
    const collateral = quote.strike * CONTRACT_MULTIPLIER;
    const breakeven = quote.strike - premium;
    candidates.push({
      ticker: quote.ticker,
      code: quote.code,
      expiry: quote.expiry,
      dte,
      strike: quote.strike,
      spot,
      bid: quote.bid,
      ask: quote.ask,
      premium: Math.round(premium * 10_000) / 10_000,
      spread_pct: spreadPct,
      delta,
      delta_estimated: deltaEstimated,
      iv,
      open_interest: quote.openInterest,
      volume: quote.volume,
      otm_pct: otmPct,
      annualized_yield: ((premium / quote.strike) * 365) / Math.max(dte, 1),
      breakeven,
      cushion_pct: (spot - breakeven) / spot,
      collateral,
      iv_rank: vol?.iv_rank ?? null,
      iv_hv_ratio: vol?.iv_hv_ratio ?? null,
      contracts: sizing.max_collateral_per_trade ? Math.floor(sizing.max_collateral_per_trade / collateral) : null,
      score: 0,
    });
  }
  return { candidates, rejected };
}

export function rankCandidates(candidates: PutCandidate[], ranking: ScanConfig['ranking'], topPerTicker: number): PutCandidate[] {
  if (!candidates.length) return [];
  if (ranking.metric === 'blend') {
    blendScores(candidates, ranking.blend_weights);
  } else {
    for (const item of candidates) {
      const value = item[ranking.metric];
      item.score = value ?? -Infinity;
    }
  }
  const ranked = [...candidates].sort((a, b) => b.score - a.score || b.annualized_yield - a.annualized_yield);
  const perTicker: Record<string, number> = {};
  const kept = ranked.filter((item) => {
    if ((perTicker[item.ticker] ?? 0) >= topPerTicker) return false;
    perTicker[item.ticker] = (perTicker[item.ticker] ?? 0) + 1;
    return true;
  });
  return ranking.top_n ? kept.slice(0, ranking.top_n) : kept;
}

function blendScores(candidates: PutCandidate[], weights: Record<string, number>): void {
  const percentiles: Record<string, Map<number, number>> = {};
  for (const metric of Object.keys(weights)) {
    const present = candidates
      .map((item, index) => [index, (item as unknown as Record<string, unknown>)[metric]] as const)
      .filter((entry): entry is readonly [number, number] => typeof entry[1] === 'number');
    if (!present.length) continue;
    const ordered = present.map(([, value]) => value).sort((a, b) => a - b);
    percentiles[metric] = new Map(present.map(([index, value]) => [index, 100 * percentileRank(ordered, value)]));
  }
  candidates.forEach((item, index) => {
    let total = 0;
    let weightSum = 0;
    for (const [metric, weight] of Object.entries(weights)) {
      const value = percentiles[metric]?.get(index);
      if (value === undefined || weight <= 0) continue;
      total += weight * value;
      weightSum += weight;
    }
    item.score = weightSum ? total / weightSum : 0;
  });
}

function percentileRank(ordered: number[], value: number): number {
  if (ordered.length === 1) return 1;
  const below = ordered.filter((item) => item < value).length;
  const equal = ordered.filter((item) => item === value).length;
  return (below + (equal - 1) / 2) / (ordered.length - 1);
}

// ---- scanner.py ----

export async function runScan(client: TradierClient, universe: string[], config: ScanConfig, now: Date): Promise<ScanResult> {
  const today = nyDate(now);
  const result: ScanResult = {
    generated_at: now.toISOString(),
    ranking_metric: config.ranking.metric,
    universe,
    dips: [],
    candidates: [],
    vols: {},
    positions_status: 'IBKR positions are not available in the online version.',
    positions: [],
    exposure: {},
    rejections: {},
    errors: {},
    warnings: [],
  };
  if (!universe.length) {
    result.warnings.push('Universe is empty: add tickers to the watchlist or to extra_tickers in the scanner settings.');
    return result;
  }

  let quotes: Record<string, StockQuote> = {};
  try {
    quotes = await client.quotes(universe);
  } catch (err) {
    result.warnings.push(`stock quotes failed: ${message(err)}`);
  }

  const historyStart = shiftDays(today, -historyCalendarDays(config));
  const closesByTicker: Record<string, number[]> = {};
  await forEachLimited(universe, 4, async (ticker) => {
    try {
      const quote = quotes[ticker];
      const bars = mergeLivePrice(toDailyBars(await client.dailyBars(ticker, historyStart)), quote?.last ?? null, quote?.tradeDate ?? null, today);
      closesByTicker[ticker] = bars.map((bar) => bar.close);
      result.dips.push(evaluateDip(ticker, bars, config.dip, quote?.prevclose ?? null));
    } catch (err) {
      result.errors[ticker] = `history: ${message(err)}`;
    }
  });
  result.dips.sort((a, b) => Number(!a.is_dip) - Number(!b.is_dip) || (b.off_high_pct ?? 0) - (a.off_high_pct ?? 0));

  const allCandidates: PutCandidate[] = [];
  let quotesSeen = false;
  let greeksSeen = false;
  await forEachLimited(result.dips.filter((item) => item.is_dip), 2, async (metrics) => {
    const ticker = metrics.ticker;
    try {
      const expirations = (await client.expirations(ticker)).filter((expiry) => {
        const dte = daysBetween(today, expiry);
        return dte >= config.puts.min_dte && dte <= config.puts.max_dte;
      });
      const floor = metrics.spot * (1 - config.puts.max_otm_pct);
      const chain: OptionQuote[] = [];
      for (const expiry of expirations) {
        chain.push(...(await client.putChain(ticker, expiry)));
      }
      const vol = underlyingVol(chain, metrics.spot, closesByTicker[ticker] ?? []);
      result.vols[ticker] = vol;
      const inRange = chain.filter((quote) => quote.strike >= floor && quote.strike < metrics.spot);
      quotesSeen = quotesSeen || inRange.length > 0;
      greeksSeen = greeksSeen || inRange.some((quote) => quote.delta !== null);
      const { candidates, rejected } = selectPuts(metrics.spot, inRange, config.puts, today, vol, config.sizing);
      result.rejections[ticker] = rejected;
      allCandidates.push(...candidates);
    } catch (err) {
      result.errors[ticker] = `options: ${message(err)}`;
    }
  });
  if (quotesSeen && !greeksSeen) {
    result.warnings.push('Tradier returned option quotes without greeks; delta/IV were estimated with Black-Scholes (marked *).');
  }
  result.candidates = rankCandidates(allCandidates, config.ranking, config.puts.top_per_ticker);
  return result;
}

/** IV of the put nearest the money in the nearest expiry, against 30-day historical volatility. */
function underlyingVol(chain: OptionQuote[], spot: number, closes: number[]): UnderlyingVol {
  const withIv = chain.filter((quote) => quote.iv);
  const nearestExpiry = withIv.map((quote) => quote.expiry).sort()[0];
  const atm = withIv
    .filter((quote) => quote.expiry === nearestExpiry)
    .sort((a, b) => Math.abs(a.strike - spot) - Math.abs(b.strike - spot))[0];
  const iv = atm?.iv ?? null;
  const hv = historicalVol(closes, 30);
  return { iv, iv_rank: null, hv_30d: hv, iv_hv_ratio: iv && hv ? iv / hv : null };
}

function historicalVol(closes: number[], window: number): number | null {
  const recent = closes.slice(-(window + 1));
  if (recent.length < window + 1) return null;
  const returns = recent.slice(1).map((close, index) => Math.log(close / recent[index]));
  const avg = sum(returns) / returns.length;
  const variance = sum(returns.map((value) => (value - avg) ** 2)) / (returns.length - 1);
  return Math.sqrt(variance) * Math.sqrt(252);
}

function toDailyBars(bars: Bar[]): DailyBar[] {
  return bars
    .filter((bar) => bar.close !== null)
    .map((bar) => ({ date: bar.date, high: bar.high ?? (bar.close as number), close: bar.close as number }));
}

/** Make the last bar reflect the live quote (replace today's bar or append a new one). */
export function mergeLivePrice(bars: DailyBar[], lastPrice: number | null, tradeDate: string | null, today: string): DailyBar[] {
  if (!lastPrice) return bars;
  const last = bars[bars.length - 1];
  if (last && (tradeDate === null || tradeDate === last.date)) {
    return [...bars.slice(0, -1), { date: last.date, high: Math.max(last.high, lastPrice), close: lastPrice }];
  }
  if (last && tradeDate !== null && tradeDate < last.date) return bars;
  return [...bars, { date: tradeDate ?? today, high: lastPrice, close: lastPrice }];
}

function historyCalendarDays(config: ScanConfig): number {
  const checks = config.dip.checks;
  const needed = [checks.off_high.lookback_days, checks.drop_n_days.days + 1, checks.rsi.period * 6, 31];
  if (config.dip.require_above_ma.enabled) needed.push(config.dip.require_above_ma.days);
  return Math.floor((Math.max(...needed) + 5) * 1.5) + 10;
}

async function forEachLimited<T>(items: T[], limit: number, worker: (item: T) => Promise<void>): Promise<void> {
  let next = 0;
  const run = async () => {
    while (next < items.length) {
      const item = items[next];
      next += 1;
      await worker(item);
    }
  };
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, run));
}

/** Calendar date in New York, where US options expire. */
export function nyDate(now: Date): string {
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit' }).format(now);
}

export function shiftDays(day: string, days: number): string {
  return new Date(Date.parse(`${day}T00:00:00Z`) + days * DAY_MS).toISOString().slice(0, 10);
}

function daysBetween(start: string, end: string): number {
  return Math.round((Date.parse(`${end}T00:00:00Z`) - Date.parse(`${start}T00:00:00Z`)) / DAY_MS);
}

function message(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

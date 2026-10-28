// Port of backend/app/services/analytics.py over bars kept in the browser.
import type { Bar } from './tradier';

export const BENCHMARK_TICKER = 'SPY';
const TRADING_DAYS = 252;

export type HoldingInput = { ticker: string; shares: number; target_allocation: number };

export function closeValue(bar: Bar): number | null {
  return bar.close;
}

function simpleMovingAverage(values: (number | null)[], window: number): (number | null)[] {
  const clean: number[] = [];
  return values.map((value) => {
    if (value !== null) clean.push(value);
    return clean.length >= window ? sum(clean.slice(-window)) / window : null;
  });
}

function exponentialMovingAverage(values: (number | null)[], span: number): (number | null)[] {
  const multiplier = 2 / (span + 1);
  let previous: number | null = null;
  return values.map((value) => {
    if (value === null) return previous;
    previous = previous === null ? value : (value - previous) * multiplier + previous;
    return previous;
  });
}

function rsiSeries(values: (number | null)[], period = 14): (number | null)[] {
  const result: (number | null)[] = values.map(() => null);
  const gains: number[] = [];
  const losses: number[] = [];
  for (let index = 1; index < values.length; index += 1) {
    const current = values[index];
    const previous = values[index - 1];
    if (current === null || previous === null) {
      gains.push(0);
      losses.push(0);
      continue;
    }
    gains.push(Math.max(current - previous, 0));
    losses.push(Math.max(previous - current, 0));
  }
  if (gains.length < period) return result;

  let avgGain = sum(gains.slice(0, period)) / period;
  let avgLoss = sum(losses.slice(0, period)) / period;
  result[period] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);
  for (let index = period + 1; index < values.length; index += 1) {
    avgGain = (avgGain * (period - 1) + gains[index - 1]) / period;
    avgLoss = (avgLoss * (period - 1) + losses[index - 1]) / period;
    result[index] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);
  }
  return result;
}

function bollinger(values: (number | null)[], window = 20, stdCount = 2) {
  const clean: number[] = [];
  const mids: (number | null)[] = [];
  const uppers: (number | null)[] = [];
  const lowers: (number | null)[] = [];
  for (const value of values) {
    if (value !== null) clean.push(value);
    if (clean.length >= window) {
      const sample = clean.slice(-window);
      const mid = sum(sample) / window;
      const std = Math.sqrt(sum(sample.map((point) => (point - mid) ** 2)) / window);
      mids.push(mid);
      uppers.push(mid + stdCount * std);
      lowers.push(mid - stdCount * std);
    } else {
      mids.push(null);
      uppers.push(null);
      lowers.push(null);
    }
  }
  return { mids, uppers, lowers };
}

export function enrichPricePoints(bars: Bar[]) {
  const closes = bars.map(closeValue);
  const ma20 = simpleMovingAverage(closes, 20);
  const ma50 = simpleMovingAverage(closes, 50);
  const ma200 = simpleMovingAverage(closes, 200);
  const rsi = rsiSeries(closes);
  const ema12 = exponentialMovingAverage(closes, 12);
  const ema26 = exponentialMovingAverage(closes, 26);
  const macd = ema12.map((fast, index) => (fast !== null && ema26[index] !== null ? fast - (ema26[index] as number) : null));
  const macdSignal = exponentialMovingAverage(macd, 9);
  const band = bollinger(closes);

  let runningHigh: number | null = null;
  return bars.map((bar, index) => {
    const close = closes[index];
    if (close !== null) runningHigh = runningHigh === null ? close : Math.max(runningHigh, close);
    const signal = macdSignal[index];
    return {
      date: bar.date,
      close: bar.close,
      adjusted_close: null,
      volume: bar.volume,
      ma20: ma20[index],
      ma50: ma50[index],
      ma200: ma200[index],
      rsi: rsi[index],
      macd: macd[index],
      macd_signal: signal,
      macd_histogram: macd[index] !== null && signal !== null ? (macd[index] as number) - signal : null,
      bb_mid: band.mids[index],
      bb_upper: band.uppers[index],
      bb_lower: band.lowers[index],
      drawdown: close !== null && runningHigh ? (close - runningHigh) / runningHigh : null,
    };
  });
}

function dailyReturns(bars: Bar[]): Map<string, number> {
  const returns = new Map<string, number>();
  let previous: number | null = null;
  for (const bar of bars) {
    const close = closeValue(bar);
    if (close !== null && previous) returns.set(bar.date, close / previous - 1);
    if (close !== null) previous = close;
  }
  return returns;
}

function cumulativeSeries(bars: Bar[]): Map<string, number> {
  const series = new Map<string, number>();
  let start: number | null = null;
  for (const bar of bars) {
    const close = closeValue(bar);
    if (close === null) continue;
    if (start === null) start = close;
    series.set(bar.date, close / start - 1);
  }
  return series;
}

function stdev(values: number[]): number {
  const mean = sum(values) / values.length;
  return Math.sqrt(sum(values.map((value) => (value - mean) ** 2)) / (values.length - 1));
}

function annualizedVolatility(bars: Bar[]): number | null {
  const returns = [...dailyReturns(bars).values()];
  return returns.length < 2 ? null : stdev(returns) * Math.sqrt(TRADING_DAYS);
}

function maxDrawdown(bars: Bar[]): number | null {
  let peak: number | null = null;
  let worst = 0;
  let found = false;
  for (const bar of bars) {
    const close = closeValue(bar);
    if (close === null) continue;
    found = true;
    peak = peak === null ? close : Math.max(peak, close);
    if (peak) worst = Math.min(worst, close / peak - 1);
  }
  return found ? worst : null;
}

function validCloses(bars: Bar[]): [string, number][] {
  return bars.filter((bar) => closeValue(bar) !== null).map((bar) => [bar.date, closeValue(bar) as number]);
}

function ytdReturn(bars: Bar[]): number | null {
  const valid = validCloses(bars);
  if (!valid.length) return null;
  const year = valid[valid.length - 1][0].slice(0, 4);
  const first = valid.find(([day]) => day.startsWith(year));
  if (!first || !first[1]) return null;
  return valid[valid.length - 1][1] / first[1] - 1;
}

export function summaryMetrics(bars: Bar[], benchmarkBars: Bar[]) {
  const valid = validCloses(bars);
  const latest = valid.length ? valid[valid.length - 1] : null;
  const recent = (valid.length >= 252 ? valid.slice(-252) : valid).map(([, value]) => value);
  const total = valid.length >= 2 && valid[0][1] ? valid[valid.length - 1][1] / valid[0][1] - 1 : null;

  let benchmarkReturn: number | null = null;
  if (benchmarkBars.length && valid.length) {
    const matching = [...cumulativeSeries(benchmarkBars).entries()].filter(([day]) => day >= valid[0][0]);
    if (matching.length) benchmarkReturn = matching[matching.length - 1][1];
  }

  return {
    latest_price: latest ? latest[1] : null,
    latest_price_date: latest ? latest[0] : null,
    ytd_return: ytdReturn(bars),
    high_52w: recent.length ? Math.max(...recent) : null,
    low_52w: recent.length ? Math.min(...recent) : null,
    annualized_volatility: annualizedVolatility(bars),
    max_drawdown: maxDrawdown(bars),
    total_return: total,
    benchmark_return: benchmarkReturn,
    relative_return: total !== null && benchmarkReturn !== null ? total - benchmarkReturn : null,
  };
}

function correlation(left: number[], right: number[]): number | null {
  if (left.length < 2 || right.length < 2) return null;
  const leftMean = sum(left) / left.length;
  const rightMean = sum(right) / right.length;
  const numerator = sum(left.map((value, index) => (value - leftMean) * (right[index] - rightMean)));
  const leftDen = Math.sqrt(sum(left.map((value) => (value - leftMean) ** 2)));
  const rightDen = Math.sqrt(sum(right.map((value) => (value - rightMean) ** 2)));
  return leftDen === 0 || rightDen === 0 ? null : numerator / (leftDen * rightDen);
}

export function marketComparison(priceMap: Record<string, Bar[]>, benchmarkBars: Bar[]) {
  const tickers = Object.keys(priceMap).filter((ticker) => ticker !== BENCHMARK_TICKER).sort();
  const benchmark = cumulativeSeries(benchmarkBars);
  const seriesByTicker = Object.fromEntries(tickers.map((ticker) => [ticker, cumulativeSeries(priceMap[ticker])]));
  const allDates = new Set<string>(benchmark.keys());
  Object.values(seriesByTicker).forEach((series) => series.forEach((_, day) => allDates.add(day)));

  const cumulative = [...allDates].sort().map((day) => {
    const row: Record<string, number | string | null> = { date: day };
    for (const ticker of tickers) {
      const value = seriesByTicker[ticker].get(day);
      if (value !== undefined) row[ticker] = value;
    }
    const benchmarkValue = benchmark.get(day);
    if (benchmarkValue !== undefined) row[BENCHMARK_TICKER] = benchmarkValue;
    return row;
  });

  const returns = Object.fromEntries(tickers.map((ticker) => [ticker, dailyReturns(priceMap[ticker])]));
  const correlationRows = tickers.map((left) => {
    const row: Record<string, number | string | null> = { ticker: left };
    for (const right of tickers) {
      const common = [...returns[left].keys()].filter((day) => returns[right].has(day)).sort();
      row[right] = correlation(
        common.map((day) => returns[left].get(day) as number),
        common.map((day) => returns[right].get(day) as number),
      );
    }
    return row;
  });

  return { benchmark: BENCHMARK_TICKER, tickers, cumulative_returns: cumulative, correlation: correlationRows };
}

export function portfolioAnalytics(holdings: HoldingInput[], priceMap: Record<string, Bar[]>, benchmarkBars: Bar[]) {
  const benchmark = cumulativeSeries(benchmarkBars);
  const valuesByDate = new Map<string, number>();
  for (const holding of holdings) {
    for (const bar of priceMap[holding.ticker] ?? []) {
      const close = closeValue(bar);
      if (close !== null) valuesByDate.set(bar.date, (valuesByDate.get(bar.date) ?? 0) + holding.shares * close);
    }
  }

  const ordered = [...valuesByDate.entries()].sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)).filter(([, value]) => value > 0);
  const start = ordered.length ? ordered[0][1] : null;
  const returns: number[] = [];
  let previous: number | null = null;
  const series = ordered.map(([day, value]) => {
    if (previous) returns.push(value / previous - 1);
    previous = value;
    return { date: day, portfolio: start ? value / start - 1 : null, [BENCHMARK_TICKER]: benchmark.get(day) ?? null };
  });

  let volatility: number | null = null;
  let sharpe: number | null = null;
  if (returns.length >= 2) {
    volatility = stdev(returns) * Math.sqrt(TRADING_DAYS);
    sharpe = volatility ? ((sum(returns) / returns.length) * TRADING_DAYS) / volatility : null;
  }
  const totalReturn = ordered.length >= 2 && ordered[0][1] ? ordered[ordered.length - 1][1] / ordered[0][1] - 1 : null;
  const currentValue = ordered.length ? ordered[ordered.length - 1][1] : 0;

  const allocation = holdings.map((holding) => {
    const latest = latestClose(priceMap[holding.ticker] ?? []);
    const marketValue = latest !== null ? holding.shares * latest : 0;
    const currentAllocation = currentValue ? marketValue / currentValue : 0;
    return {
      ticker: holding.ticker,
      current_allocation: currentAllocation,
      target_allocation: holding.target_allocation,
      drift: currentAllocation - holding.target_allocation,
    };
  });

  return {
    benchmark: BENCHMARK_TICKER,
    total_value: currentValue,
    total_return: totalReturn,
    annualized_volatility: volatility,
    sharpe_ratio: sharpe,
    series,
    allocation,
  };
}

export function latestClose(bars: Bar[]): number | null {
  for (let index = bars.length - 1; index >= 0; index -= 1) {
    const close = closeValue(bars[index]);
    if (close !== null) return close;
  }
  return null;
}

export function sum(values: number[]): number {
  return values.reduce((total, value) => total + value, 0);
}

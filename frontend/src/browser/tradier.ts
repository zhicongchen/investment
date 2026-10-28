// Tradier market data, called straight from the browser (Tradier sends CORS headers).
// Sandbox tokens get 15-minute delayed data; live tokens get real-time data.

export type Bar = {
  date: string;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  volume: number | null;
};

export type StockQuote = {
  symbol: string;
  name: string;
  exchange: string;
  last: number | null;
  prevclose: number | null;
  tradeDate: string | null;
};

export type OptionQuote = {
  ticker: string;
  code: string;
  expiry: string;
  strike: number;
  bid: number | null;
  ask: number | null;
  openInterest: number | null;
  volume: number | null;
  delta: number | null;
  iv: number | null;
};

const EXCHANGES: Record<string, string> = {
  A: 'NYSE American',
  N: 'NYSE',
  P: 'NYSE Arca',
  Q: 'NASDAQ',
  Z: 'Cboe BZX',
};

type Json = Record<string, unknown>;

export class TradierClient {
  private readonly baseUrl: string;

  constructor(private readonly token: string, sandbox: boolean) {
    if (!token) throw new Error('Add your Tradier API token in Settings to load market data.');
    this.baseUrl = sandbox ? 'https://sandbox.tradier.com/v1' : 'https://api.tradier.com/v1';
  }

  async quotes(symbols: string[]): Promise<Record<string, StockQuote>> {
    const result: Record<string, StockQuote> = {};
    for (let index = 0; index < symbols.length; index += 100) {
      const payload = await this.get('/markets/quotes', { symbols: symbols.slice(index, index + 100).join(',') });
      for (const row of asArray<Json>((payload.quotes as Json | null)?.quote)) {
        const symbol = String(row.symbol ?? '').toUpperCase();
        if (!symbol) continue;
        const tradeMs = numberOrNull(row.trade_date);
        result[symbol] = {
          symbol,
          name: String(row.description ?? symbol),
          exchange: EXCHANGES[String(row.exch ?? '')] ?? String(row.exch ?? ''),
          last: numberOrNull(row.last),
          prevclose: numberOrNull(row.prevclose),
          tradeDate: tradeMs ? new Date(tradeMs).toISOString().slice(0, 10) : null,
        };
      }
    }
    return result;
  }

  async dailyBars(symbol: string, start: string): Promise<Bar[]> {
    const payload = await this.get('/markets/history', { symbol, interval: 'daily', start });
    return asArray<Json>((payload.history as Json | null)?.day).map((row) => ({
      date: String(row.date),
      open: numberOrNull(row.open),
      high: numberOrNull(row.high),
      low: numberOrNull(row.low),
      close: numberOrNull(row.close),
      volume: numberOrNull(row.volume),
    }));
  }

  async expirations(symbol: string): Promise<string[]> {
    const payload = await this.get('/markets/options/expirations', { symbol, includeAllRoots: 'true' });
    return asArray<string>((payload.expirations as Json | null)?.date).map(String);
  }

  async putChain(symbol: string, expiration: string): Promise<OptionQuote[]> {
    const payload = await this.get('/markets/options/chains', { symbol, expiration, greeks: 'true' });
    return asArray<Json>((payload.options as Json | null)?.option)
      .filter((row) => row.option_type === 'put')
      .map((row) => {
        const greeks = (row.greeks ?? {}) as Json;
        return {
          ticker: symbol,
          code: String(row.symbol),
          expiry: String(row.expiration_date ?? expiration),
          strike: Number(row.strike),
          bid: numberOrNull(row.bid),
          ask: numberOrNull(row.ask),
          openInterest: numberOrNull(row.open_interest),
          volume: numberOrNull(row.volume),
          delta: numberOrNull(greeks.delta),
          iv: positiveOrNull(greeks.mid_iv) ?? positiveOrNull(greeks.smv_vol),
        };
      });
  }

  private async get(path: string, params: Record<string, string>): Promise<Json> {
    const url = `${this.baseUrl}${path}?${new URLSearchParams(params).toString()}`;
    for (let attempt = 0; ; attempt += 1) {
      let response: Response;
      try {
        response = await fetch(url, { headers: { Authorization: `Bearer ${this.token}`, Accept: 'application/json' } });
      } catch {
        throw new Error('Could not reach Tradier. Check your connection, and that the token matches the sandbox/live setting.');
      }
      if (response.status === 429 && attempt < 2) {
        await new Promise((resolve) => setTimeout(resolve, 15_000));
        continue;
      }
      if (response.status === 401) throw new Error('Tradier rejected the API token. Check it, and the sandbox/live setting, in Settings.');
      if (!response.ok) throw new Error(`Tradier ${path} failed: ${response.status} ${(await response.text()).slice(0, 200)}`);
      return (await response.json()) as Json;
    }
  }
}

// Tradier returns a bare object instead of a one-item list, and null for none.
function asArray<T>(value: unknown): T[] {
  if (value === null || value === undefined || value === 'null') return [];
  return (Array.isArray(value) ? value : [value]) as T[];
}

function numberOrNull(value: unknown): number | null {
  if (value === null || value === undefined || value === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function positiveOrNull(value: unknown): number | null {
  const parsed = numberOrNull(value);
  return parsed !== null && parsed > 0 ? parsed : null;
}

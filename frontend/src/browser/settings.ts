import { readJson, writeJson } from './storage';

export type DataSettings = {
  tradierToken: string;
  tradierSandbox: boolean;
  fmpKey: string;
};

// Mirrors options/config.yaml. The universe is the dashboard watchlist plus extra_tickers.
export type ScanConfig = {
  extra_tickers: string[];
  exclude_tickers: string[];
  dip: {
    rule: 'any' | 'all';
    checks: {
      off_high: { enabled: boolean; lookback_days: number; min_pct: number };
      drop_n_days: { enabled: boolean; days: number; min_pct: number };
      rsi: { enabled: boolean; period: number; max: number };
      day_drop: { enabled: boolean; min_pct: number };
    };
    require_above_ma: { enabled: boolean; days: number };
  };
  puts: {
    min_dte: number;
    max_dte: number;
    delta_min: number;
    delta_max: number;
    min_otm_pct: number;
    max_otm_pct: number;
    min_open_interest: number;
    min_bid: number;
    max_spread_pct: number;
    price_basis: 'mid' | 'bid';
    risk_free_rate: number;
    top_per_ticker: number;
  };
  ranking: {
    metric: 'annualized_yield' | 'iv_hv_ratio' | 'blend';
    blend_weights: Record<string, number>;
    top_n: number;
  };
  sizing: { max_collateral_per_trade: number | null };
};

export const DEFAULT_SCAN_CONFIG: ScanConfig = {
  extra_tickers: [],
  exclude_tickers: [],
  dip: {
    rule: 'any',
    checks: {
      off_high: { enabled: true, lookback_days: 20, min_pct: 0.1 },
      drop_n_days: { enabled: true, days: 5, min_pct: 0.05 },
      rsi: { enabled: false, period: 14, max: 35 },
      day_drop: { enabled: false, min_pct: 0.03 },
    },
    require_above_ma: { enabled: false, days: 200 },
  },
  puts: {
    min_dte: 7,
    max_dte: 45,
    delta_min: -0.35,
    delta_max: -0.15,
    min_otm_pct: 0,
    max_otm_pct: 0.3,
    min_open_interest: 100,
    min_bid: 0.1,
    max_spread_pct: 0.1,
    price_basis: 'mid',
    risk_free_rate: 0.04,
    top_per_ticker: 3,
  },
  ranking: {
    metric: 'annualized_yield',
    blend_weights: { annualized_yield: 0.6, iv_hv_ratio: 0.4 },
    top_n: 20,
  },
  sizing: { max_collateral_per_trade: null },
};

export function loadSettings(): DataSettings {
  return { tradierToken: '', tradierSandbox: true, fmpKey: '', ...readJson<Partial<DataSettings>>('settings', {}) };
}

export function saveSettings(settings: DataSettings): void {
  writeJson('settings', settings);
}

export function loadScanConfig(): ScanConfig {
  return mergeDefaults(DEFAULT_SCAN_CONFIG, readJson<unknown>('scan-config', {})) as ScanConfig;
}

export function saveScanConfig(raw: string): ScanConfig {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch (err) {
    throw new Error(`Scanner settings are not valid JSON: ${err instanceof Error ? err.message : String(err)}`);
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error('Scanner settings must be a JSON object.');
  }
  const config = mergeDefaults(DEFAULT_SCAN_CONFIG, parsed) as ScanConfig;
  if (!['annualized_yield', 'iv_hv_ratio', 'blend'].includes(config.ranking.metric)) {
    throw new Error('ranking.metric must be annualized_yield, iv_hv_ratio or blend.');
  }
  writeJson('scan-config', config);
  return config;
}

// Fill keys missing from a saved config with defaults so older saves keep working.
function mergeDefaults(defaults: unknown, value: unknown): unknown {
  if (!isPlainObject(defaults)) return value === undefined ? defaults : value;
  if (!isPlainObject(value)) return defaults;
  const merged: Record<string, unknown> = { ...value };
  for (const [key, fallback] of Object.entries(defaults)) {
    merged[key] = key === 'blend_weights' && isPlainObject(value[key]) ? value[key] : mergeDefaults(fallback, value[key]);
  }
  return merged;
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

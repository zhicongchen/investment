// Financial Modeling Prep fundamentals, same fields as backend/app/providers/fmp_provider.py.

export type Profile = { name: string; sector: string; exchange: string };

export type FundamentalsData = {
  date: string;
  pe: number | null;
  forward_pe: number | null;
  eps_growth: number | null;
  revenue_growth: number | null;
  debt_to_equity: number | null;
  free_cash_flow: number | null;
  dividend_yield: number | null;
};

type Json = Record<string, unknown>;

const BASE_URL = 'https://financialmodelingprep.com/stable';

export class FmpClient {
  constructor(private readonly apiKey: string) {}

  async profile(symbol: string): Promise<Profile> {
    const profile = await this.first('profile', symbol);
    return {
      name: String(profile.companyName ?? profile.name ?? symbol),
      sector: String(profile.sector ?? ''),
      exchange: String(profile.exchangeShortName ?? profile.exchange ?? ''),
    };
  }

  async fundamentals(symbol: string, today: string): Promise<FundamentalsData> {
    const [profile, ratios, keyMetrics, growth, cashFlow, balanceSheet] = await Promise.all(
      ['profile', 'ratios-ttm', 'key-metrics-ttm', 'financial-growth', 'cash-flow-statement', 'balance-sheet-statement'].map((endpoint) =>
        this.first(endpoint, symbol),
      ),
    );
    const totalDebt = firstNumber(balanceSheet, 'totalDebt', 'shortTermDebt', 'longTermDebt');
    const equity = firstNumber(balanceSheet, 'totalStockholdersEquity', 'totalEquity', 'totalShareholdersEquity');
    const calculatedDebtToEquity = totalDebt !== null && equity ? totalDebt / equity : null;
    return {
      date: today,
      pe:
        firstNumber(ratios, 'priceToEarningsRatioTTM', 'priceEarningsRatioTTM', 'peRatioTTM', 'priceEarningsRatio') ??
        firstNumber(keyMetrics, 'peRatioTTM', 'peRatio'),
      forward_pe: firstNumber(profile, 'forwardPE', 'forwardPe'),
      eps_growth: asDecimal(firstNumber(growth, 'growthEPS', 'epsgrowth', 'growthNetIncomePerShare')),
      revenue_growth: asDecimal(firstNumber(growth, 'growthRevenue', 'revenueGrowth')),
      debt_to_equity: firstNumber(ratios, 'debtEquityRatioTTM', 'debtToEquityRatioTTM', 'debtEquityRatio') ?? calculatedDebtToEquity,
      free_cash_flow: firstNumber(cashFlow, 'freeCashFlow') ?? firstNumber(keyMetrics, 'freeCashFlowTTM', 'freeCashFlowPerShareTTM'),
      dividend_yield: asDecimal(
        firstNumber(keyMetrics, 'dividendYieldTTM', 'dividendYield') ?? firstNumber(ratios, 'dividendYieldTTM', 'dividendYield'),
      ),
    };
  }

  private async first(endpoint: string, symbol: string): Promise<Json> {
    const url = `${BASE_URL}/${endpoint}?${new URLSearchParams({ symbol, apikey: this.apiKey }).toString()}`;
    let response: Response;
    try {
      response = await fetch(url);
    } catch {
      throw new Error('Could not reach Financial Modeling Prep.');
    }
    if (!response.ok) throw new Error(`FMP ${endpoint} failed: ${response.status} ${(await response.text()).slice(0, 200)}`);
    const payload = (await response.json()) as unknown;
    if (payload && typeof payload === 'object' && !Array.isArray(payload) && 'Error Message' in payload) {
      throw new Error(`FMP ${endpoint}: ${String((payload as Json)['Error Message'])}`);
    }
    const first = Array.isArray(payload) ? payload[0] : payload;
    return first && typeof first === 'object' ? (first as Json) : {};
  }
}

// Zero counts as missing (FMP reports 0 for fields it does not have).
function firstNumber(payload: Json, ...keys: string[]): number | null {
  for (const key of keys) {
    const value = payload[key];
    if (value === null || value === undefined || value === '') continue;
    const parsed = Number(value);
    if (Number.isFinite(parsed) && parsed !== 0) return parsed;
  }
  return null;
}

function asDecimal(value: number | null): number | null {
  if (value === null) return null;
  return Math.abs(value) > 1 ? value / 100 : value;
}

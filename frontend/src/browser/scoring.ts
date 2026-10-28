// Port of backend/app/services/scoring.py.
import { closeValue, latestClose, sum } from './analytics';
import type { FundamentalsData } from './fmp';
import type { Bar } from './tradier';

export type SignalDecision = {
  date: string;
  action: string;
  score: number;
  confidence: string;
  reasons: string[];
  risk_flags: string[];
};

export type HoldingRecord = { shares: number; average_cost: number; target_allocation: number; notes: string };

const clamp = (value: number, low = 0, high = 100) => Math.max(low, Math.min(high, value));

// Python's round(): ties go to the even digit, so scores match the local backend.
function roundHalfEven(value: number, digits: number): number {
  const scaled = value * 10 ** digits;
  const rounded = Math.round(scaled);
  const tie = Math.abs(scaled % 1) === 0.5;
  return (tie && rounded % 2 !== 0 ? rounded - 1 : rounded) / 10 ** digits;
}

function movingAverage(bars: Bar[], window: number): number | null {
  const closes = bars.map(closeValue).filter((value): value is number => value !== null);
  return closes.length < window ? null : sum(closes.slice(-window)) / window;
}

export function computeSignal(
  bars: Bar[],
  fundamentals: FundamentalsData | null,
  holding: HoldingRecord | null,
  portfolioValue: number,
  today: string,
): SignalDecision {
  if (!bars.length) {
    return { date: today, action: 'WATCH', score: 0, confidence: 'low', reasons: [], risk_flags: ['No price history available'] };
  }

  const latestDate = bars[bars.length - 1].date;
  const close = latestClose(bars);
  const reasons: string[] = [];
  const riskFlags: string[] = [];

  let valuation = 50;
  let quality = 50;
  let growth = 50;
  let trend = 50;
  let portfolioFit = 50;

  if (fundamentals) {
    const pe = fundamentals.forward_pe || fundamentals.pe;
    if (pe === null || pe === undefined) {
      riskFlags.push('Valuation data unavailable');
    } else if (pe <= 15) {
      valuation = 85;
      reasons.push('Valuation appears attractive versus a 15x earnings reference');
    } else if (pe <= 25) {
      valuation = 70;
      reasons.push('Valuation is reasonable versus earnings');
    } else if (pe <= 40) {
      valuation = 45;
      riskFlags.push('Valuation is elevated versus earnings');
    } else {
      valuation = 20;
      riskFlags.push('Valuation is severely stretched versus earnings');
    }

    if (fundamentals.debt_to_equity === null) {
      riskFlags.push('Debt data unavailable');
    } else if (fundamentals.debt_to_equity <= 75) {
      quality += 25;
      reasons.push('Debt-to-equity is within a conservative range');
    } else if (fundamentals.debt_to_equity > 200) {
      quality -= 25;
      riskFlags.push('Debt-to-equity is high');
    }

    if (fundamentals.free_cash_flow !== null && fundamentals.free_cash_flow > 0) {
      quality += 20;
      reasons.push('Free cash flow is positive');
    } else if (fundamentals.free_cash_flow !== null) {
      quality -= 20;
      riskFlags.push('Free cash flow is negative');
    }

    const growthInputs = [fundamentals.eps_growth, fundamentals.revenue_growth].filter((value): value is number => value !== null);
    if (growthInputs.length) {
      const avgGrowth = sum(growthInputs) / growthInputs.length;
      if (avgGrowth >= 0.15) {
        growth = 85;
        reasons.push('Growth metrics are strong');
      } else if (avgGrowth >= 0.03) {
        growth = 65;
        reasons.push('Growth metrics are positive');
      } else if (avgGrowth < 0) {
        growth = 25;
        riskFlags.push('Growth metrics are negative');
      }
    } else {
      riskFlags.push('Growth data unavailable');
    }
  } else {
    riskFlags.push('Fundamentals unavailable');
  }

  const ma50 = movingAverage(bars, 50);
  const ma200 = movingAverage(bars, 200);
  if (close && ma50 && ma200) {
    if (close >= ma50 && ma50 >= ma200) {
      trend = 85;
      reasons.push('Price is above both 50-day and 200-day moving averages');
    } else if (close >= ma200) {
      trend = 65;
      reasons.push('Price remains above the 200-day moving average');
    } else {
      trend = 25;
      riskFlags.push('Price is below the 200-day moving average');
    }
  } else {
    riskFlags.push('Not enough price history for long-term trend');
  }

  if (holding && close && portfolioValue > 0) {
    const currentAllocation = (holding.shares * close) / portfolioValue;
    const target = holding.target_allocation;
    if (target > 0 && currentAllocation > target * 1.35) {
      portfolioFit = 20;
      riskFlags.push('Position is materially above target allocation');
    } else if (target > 0 && currentAllocation < target * 0.75) {
      portfolioFit = 80;
      reasons.push('Position is below target allocation');
    } else {
      portfolioFit = 65;
      reasons.push('Position is near target allocation');
    }
  } else if (holding) {
    portfolioFit = 60;
    reasons.push('Holding exists but allocation cannot be fully evaluated yet');
  } else {
    portfolioFit = 60;
    reasons.push('No current holding recorded');
  }

  quality = clamp(quality);
  const score = roundHalfEven(valuation * 0.3 + quality * 0.25 + growth * 0.2 + trend * 0.15 + portfolioFit * 0.1, 1);

  const severeRisk = riskFlags.some((flag) => flag.toLowerCase().includes('severely') || flag.toLowerCase().includes('materially above'));
  const owned = Boolean(holding && holding.shares > 0);

  let action: string;
  if (owned && portfolioFit <= 25) action = 'TRIM';
  else if (severeRisk && owned) action = 'SELL_REVIEW';
  else if (owned && score >= 45) action = 'HOLD';
  else if (!owned && score >= 72 && !severeRisk) action = 'BUY';
  else if (score < 35 && owned) action = 'SELL_REVIEW';
  else action = 'WATCH';

  const confidence = bars.length >= 200 && fundamentals ? 'high' : fundamentals ? 'medium' : 'low';
  return { date: latestDate, action, score, confidence, reasons: reasons.slice(0, 8), risk_flags: riskFlags.slice(0, 8) };
}

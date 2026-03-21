/**
 * Pricing Engine — core rate calculation logic.
 *
 * Computes daily prices considering:
 * - Base rates
 * - Weekend rates (Fri/Sat nights)
 * - Seasonal multipliers
 * - Minimum stay requirements
 */
import type { Rate, DailyPrice } from "../types.ts";

/**
 * Check if a date string (YYYY-MM-DD) falls on a weekend night.
 * Weekend nights = Friday and Saturday (check-in nights).
 */
export function isWeekendNight(dateStr: string): boolean {
  const d = new Date(dateStr + "T12:00:00Z"); // noon UTC to avoid TZ issues
  const day = d.getUTCDay();
  return day === 5 || day === 6; // Friday = 5, Saturday = 6
}

/**
 * Generate all dates in a range [start, end) — end is exclusive (checkout date).
 */
export function generateDateRange(start: string, end: string): string[] {
  const dates: string[] = [];
  const current = new Date(start + "T12:00:00Z");
  const endDate = new Date(end + "T12:00:00Z");

  while (current < endDate) {
    dates.push(current.toISOString().split("T")[0]);
    current.setUTCDate(current.getUTCDate() + 1);
  }

  return dates;
}

/**
 * Calculate the nightly price for a single date given its rate.
 */
export function calculateNightlyPrice(rate: Rate, dateStr: string): number {
  const weekend = isWeekendNight(dateStr);
  let price: number;

  if (weekend && rate.weekendRate !== null) {
    price = rate.weekendRate;
  } else {
    price = rate.baseRate;
  }

  // Apply seasonal multiplier
  price = Math.round(price * rate.seasonalMultiplier);

  return price;
}

/**
 * Build a map of date -> Rate for fast lookup.
 */
export function buildRateMap(rates: Rate[]): Map<string, Rate> {
  const map = new Map<string, Rate>();
  for (const rate of rates) {
    map.set(rate.date, rate);
  }
  return map;
}

/**
 * Default rate used when no specific rate exists for a date.
 */
export function defaultRate(unitId: string, date: string): Rate {
  return {
    id: "default",
    unitId,
    date,
    baseRate: 15000, // $150.00 default
    weekendRate: null,
    seasonalMultiplier: 1.0,
    minimumStay: 1,
    currency: "USD",
    createdAt: new Date(),
    updatedAt: new Date(),
  };
}

/**
 * Calculate daily prices for a date range.
 * Returns array of DailyPrice for each night.
 */
export function calculateDailyPrices(
  unitId: string,
  rates: Rate[],
  startDate: string,
  endDate: string,
): DailyPrice[] {
  const dateRange = generateDateRange(startDate, endDate);
  const rateMap = buildRateMap(rates);
  const dailyPrices: DailyPrice[] = [];

  for (const date of dateRange) {
    const rate = rateMap.get(date) ?? defaultRate(unitId, date);
    const weekend = isWeekendNight(date);
    const price = calculateNightlyPrice(rate, date);

    dailyPrices.push({
      date,
      price,
      isWeekend: weekend,
      seasonalMultiplier: rate.seasonalMultiplier,
      minimumStay: rate.minimumStay,
      currency: rate.currency,
    });
  }

  return dailyPrices;
}

/**
 * Check if minimum stay requirement is met.
 * Uses the maximum minimumStay across all nights in the range.
 */
export function isMinimumStayMet(
  dailyPrices: DailyPrice[],
  nights: number,
): boolean {
  if (dailyPrices.length === 0) return true;
  const maxMinStay = Math.max(...dailyPrices.map((p) => p.minimumStay));
  return nights >= maxMinStay;
}

/**
 * Calculate taxes (default 12% lodging tax).
 */
export function calculateTaxes(subtotal: number, taxRate = 0.12): number {
  return Math.round(subtotal * taxRate);
}

/**
 * Calculate fees (default: $75 cleaning fee + 3% service fee).
 */
export function calculateFees(
  subtotal: number,
  cleaningFee = 7500,
  serviceRate = 0.03,
): number {
  return cleaningFee + Math.round(subtotal * serviceRate);
}

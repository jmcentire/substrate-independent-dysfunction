/**
 * Pricing Service — orchestrates rate lookup, caching, and quote generation.
 */
import type { Config } from "../config.ts";
import type { RateRepository } from "../db/rate_repository.ts";
import type { RedisCache } from "../cache/redis_cache.ts";
import type { DailyPrice, PriceQuote, QuoteRequest } from "../types.ts";
import {
  calculateDailyPrices,
  calculateFees,
  calculateTaxes,
  generateDateRange,
  isMinimumStayMet,
} from "./pricing_engine.ts";

export class PricingService {
  constructor(
    private rateRepo: RateRepository,
    private cache: RedisCache,
    private config: Config,
  ) {}

  /**
   * Get pricing for a unit across a date range.
   * Checks cache first, falls back to DB.
   */
  async getPricing(
    unitId: string,
    startDate: string,
    endDate: string,
  ): Promise<DailyPrice[]> {
    // Try cache first
    const cached = await this.cache.getRates(unitId, startDate, endDate);
    if (cached) {
      return cached;
    }

    // Fetch from DB
    const rates = await this.rateRepo.getRates(unitId, startDate, endDate);
    const dailyPrices = calculateDailyPrices(unitId, rates, startDate, endDate);

    // Cache the result
    await this.cache.setRates(unitId, startDate, endDate, dailyPrices);

    return dailyPrices;
  }

  /**
   * Generate a price quote for a booking.
   * Integrates with Availability Service to confirm dates are available.
   */
  async generateQuote(request: QuoteRequest): Promise<PriceQuote> {
    const { unitId, checkIn, checkOut } = request;

    // Calculate nights
    const dates = generateDateRange(checkIn, checkOut);
    const nights = dates.length;

    if (nights <= 0) {
      throw new PricingError(
        "checkOut must be after checkIn",
        "INVALID_DATE_RANGE",
      );
    }

    // Fetch pricing
    const dailyPrices = await this.getPricing(unitId, checkIn, checkOut);

    // Check minimum stay
    const minimumStayMet = isMinimumStayMet(dailyPrices, nights);

    // Calculate totals
    const subtotal = dailyPrices.reduce((sum, dp) => sum + dp.price, 0);
    const taxes = calculateTaxes(subtotal);
    const fees = calculateFees(subtotal);
    const total = subtotal + taxes + fees;

    // Check availability (best-effort — don't fail quote if service is down)
    let availabilityConfirmed = false;
    try {
      availabilityConfirmed = await this.checkAvailability(
        unitId,
        checkIn,
        checkOut,
      );
    } catch (err) {
      console.error(
        "[PricingService] Availability check failed (non-blocking):",
        err,
      );
    }

    // Quote expires in 30 minutes
    const expiresAt = new Date(Date.now() + 30 * 60 * 1000);

    return {
      unitId,
      checkIn,
      checkOut,
      nights,
      dailyPrices,
      subtotal,
      taxes,
      fees,
      total,
      currency: dailyPrices[0]?.currency ?? "USD",
      minimumStayMet,
      availabilityConfirmed,
      quoteExpiresAt: expiresAt.toISOString(),
    };
  }

  /**
   * Update rates for a unit, invalidating cache.
   */
  async updateRates(
    unitId: string,
    rates: {
      date: string;
      baseRate: number;
      weekendRate?: number | null;
      seasonalMultiplier?: number;
      minimumStay?: number;
      currency?: string;
    }[],
  ) {
    // Upsert in DB
    const updated = await this.rateRepo.upsertRates(unitId, rates);

    // Invalidate cache for this unit
    await this.cache.invalidateUnit(unitId);

    return updated;
  }

  /**
   * Check availability with the Availability Service.
   */
  private async checkAvailability(
    unitId: string,
    checkIn: string,
    checkOut: string,
  ): Promise<boolean> {
    const url = `${this.config.availabilityServiceUrl}/availability/${unitId}?start=${checkIn}&end=${checkOut}`;
    const response = await fetch(url, {
      method: "GET",
      headers: { "Content-Type": "application/json" },
      signal: AbortSignal.timeout(5000), // 5s timeout
    });

    if (!response.ok) {
      return false;
    }

    const data = await response.json();
    // Expect the availability service to return { available: boolean }
    // or an array of dates with availability
    if (typeof data.available === "boolean") {
      return data.available;
    }

    // If array of date objects, check all are available
    if (Array.isArray(data.dates)) {
      return data.dates.every(
        (d: { available: boolean }) => d.available === true,
      );
    }

    return false;
  }
}

export class PricingError extends Error {
  constructor(
    message: string,
    public code: string,
  ) {
    super(message);
    this.name = "PricingError";
  }
}

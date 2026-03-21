/**
 * Core domain types for the Pricing Service.
 */
import { z } from "zod";

// ---------- Rate types ----------

export interface Rate {
  id: string;
  unitId: string;
  date: string; // YYYY-MM-DD
  baseRate: number; // cents
  weekendRate: number | null; // cents, null means use baseRate
  seasonalMultiplier: number; // 1.0 = normal
  minimumStay: number; // minimum nights
  currency: string;
  createdAt: Date;
  updatedAt: Date;
}

export interface DailyPrice {
  date: string;
  price: number; // cents
  isWeekend: boolean;
  seasonalMultiplier: number;
  minimumStay: number;
  currency: string;
}

export interface PriceQuote {
  unitId: string;
  checkIn: string;
  checkOut: string;
  nights: number;
  dailyPrices: DailyPrice[];
  subtotal: number; // cents
  taxes: number; // cents
  fees: number; // cents
  total: number; // cents
  currency: string;
  minimumStayMet: boolean;
  availabilityConfirmed: boolean;
  quoteExpiresAt: string;
}

// ---------- Zod schemas for validation ----------

export const GetPricingQuerySchema = z.object({
  start: z.string().regex(/^\d{4}-\d{2}-\d{2}$/, "start must be YYYY-MM-DD"),
  end: z.string().regex(/^\d{4}-\d{2}-\d{2}$/, "end must be YYYY-MM-DD"),
});

export const QuoteRequestSchema = z.object({
  unitId: z.string().uuid(),
  checkIn: z.string().regex(/^\d{4}-\d{2}-\d{2}$/, "checkIn must be YYYY-MM-DD"),
  checkOut: z.string().regex(/^\d{4}-\d{2}-\d{2}$/, "checkOut must be YYYY-MM-DD"),
  guestCount: z.number().int().positive().optional().default(1),
});

export const RateUpdateSchema = z.object({
  rates: z.array(
    z.object({
      date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/, "date must be YYYY-MM-DD"),
      baseRate: z.number().int().positive(),
      weekendRate: z.number().int().positive().nullable().optional(),
      seasonalMultiplier: z.number().positive().optional().default(1.0),
      minimumStay: z.number().int().positive().optional().default(1),
      currency: z.string().length(3).optional().default("USD"),
    }),
  ).min(1),
});

export type GetPricingQuery = z.infer<typeof GetPricingQuerySchema>;
export type QuoteRequest = z.infer<typeof QuoteRequestSchema>;
export type RateUpdate = z.infer<typeof RateUpdateSchema>;

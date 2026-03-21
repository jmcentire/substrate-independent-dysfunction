import { z } from "zod";

const dateRegex = /^\d{4}-\d{2}-\d{2}$/;

export const dateRangeQuerySchema = z.object({
  start: z.string().regex(dateRegex, "start must be YYYY-MM-DD"),
  end: z.string().regex(dateRegex, "end must be YYYY-MM-DD"),
});

export const rateUpdateSchema = z.object({
  rates: z.array(
    z.object({
      startDate: z.string().regex(dateRegex),
      endDate: z.string().regex(dateRegex),
      baseRate: z.number().int().positive(),
      currency: z.string().length(3).optional().default("USD"),
      rateType: z.enum(["standard", "seasonal", "promotional", "custom"]).optional().default("standard"),
      seasonalMultiplier: z.number().min(0).optional().default(1.0),
      weekendMultiplier: z.number().min(0).optional().default(1.0),
      minStay: z.number().int().min(1).optional().default(1),
      weekendRate: z.number().int().positive().optional(),
    })
  ).min(1),
});

export const quoteRequestSchema = z.object({
  unitId: z.string().min(1),
  startDate: z.string().regex(dateRegex),
  endDate: z.string().regex(dateRegex),
  guests: z.number().int().positive().optional().default(1),
  promoCode: z.string().optional(),
});

export type DateRangeQuery = z.infer<typeof dateRangeQuerySchema>;
export type RateUpdatePayload = z.infer<typeof rateUpdateSchema>;
export type QuoteRequestPayload = z.infer<typeof quoteRequestSchema>;

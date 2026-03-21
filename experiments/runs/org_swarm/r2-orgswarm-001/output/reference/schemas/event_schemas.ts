/**
 * Event Schemas — Unified Segment/Clickstream Events (from real codebase)
 *
 * These Zod schemas define the event contracts used across the platform.
 * Events are emitted by services and routed through the Event Service.
 *
 * Key pattern: content-addressable event IDs (SHA256 of event content)
 * give automatic idempotency.
 */

import { z } from "zod";

// --- Shared Types ---

export const SegmentSimilarUnitSchema = z.object({
  id: z.string(),
  city: z.string(),
  state: z.string(),
  name: z.string(),
  url: z.string(),
  slug: z.string(),
  image_url: z.string().optional(),
});

export const SegmentCheckoutProductSchema = z.object({
  brand: z.string(),
  categories: z.array(z.string()),
  city: z.string(),
  name: z.string(),
  nights: z.number().nullable(),
  price: z.number().optional(),
  product_id: z.string(),
  quantity: z.number().nullable(),
  region: z.string(),
  state: z.string(),
  url: z.string(),
  slug: z.string().optional(),
  image_url: z.string().nullable().optional(),
});

// --- Booking Flow Events ---

export const CheckoutStartedSchema = z.object({
  affiliation: z.string(),
  categories: z.array(z.string()),
  cart_value: z.number(),
  checkin_date: z.string(),
  checkout_date: z.string(),
  coupon: z.number(),
  currency: z.string(),
  order_id: z.string(),
  price: z.number(),
  quantity: z.number(),
  total: z.number(),
  value: z.number(),
  eligible_for_make_an_offer: z.boolean(),
  user_id: z.string(),
  email: z.string(),
  products: z.array(SegmentCheckoutProductSchema),
});

export const PaymentInfoEnteredSchema = z.object({
  user_id: z.string(),
  order_id: z.string().optional(),
  affiliation: z.string(),
  email: z.string().optional(),
  categories: z.array(z.string()),
  cart_value: z.number(),
  checkin_date: z.string().optional(),
  checkout_date: z.string().optional(),
  coupon: z.number(),
  currency: z.string(),
  payment_split: z.boolean(),
  quantity: z.number().nullable(),
  total: z.number(),
  value: z.number(),
  products: z.array(SegmentCheckoutProductSchema),
});

export const PurchasedSchema = z.object({
  affiliation: z.string(),
  categories: z.array(z.string()),
  checkin_date: z.string(),
  checkout_date: z.string(),
  coupon: z.string().optional(),
  currency: z.string(),
  order_id: z.string(),
  price: z.number(),
  quantity: z.number(),
  revenue: z.number(),
  total: z.number(),
  value: z.number(),
  products: z.array(SegmentCheckoutProductSchema),
  numberOfGuests: z.number().optional(),
});

// --- Search & Browse Events ---

export const ProductsSearchedSchema = z.object({
  search_query: z.union([z.string(), z.record(z.any())]),
  user_id: z.string().optional(),
  email: z.string().optional(),
});

export const ProductViewedSchema = z.object({
  product_id: z.string(),
  categories: z.array(z.string()),
  name: z.string(),
  brand: z.string(),
  image_url: z.string().optional(),
  city: z.string(),
  country: z.string().optional(),
  region: z.string(),
  url: z.string(),
  user_id: z.string().optional(),
  email: z.string().optional(),
});

// --- Internal Service Events (defined by contract, not Zod) ---
//
// booking.requested:  { booking_id, property_id, dates, guest_id }
// booking.confirmed:  { booking_id, ubr, pms_confirmation_id, total_cents }
// booking.cancelled:  { booking_id, reason, refund_amount_cents }
// booking.failed:     { booking_id, step, error_code, error_message }
//
// availability.changed: { unit_id, start_date, end_date, blocked: boolean }
// pricing.rates_updated: { unit_id, dates: string[] }
//
// property.created:   { property_id, organization_id }
// property.updated:   { property_id, fields_changed: string[] }
// property.override_set: { property_id, field, actor }
// property.override_conflict: { property_id, field, old_base, new_base }
//
// payment.invoice_created: { invoice_id, booking_id, total_cents }
// payment.succeeded:  { invoice_id, amount_cents }
// payment.refunded:   { refund_id, invoice_id, amount_cents }
// payment.payout_created: { payout_id, owner_id, total_cents }
//
// sync.property_pulled: { property_id, pms_type }
// sync.availability_pulled: { property_id, dates_count }
// sync.pricing_pulled: { property_id, dates_count }
// sync.rate_limit_backoff: { partner, backoff_seconds }
// sync.reconciliation_drift: { property_id, subsystem, diff }

// ── Booking States ──────────────────────────────────────────────────────
export type BookingStatus =
  | "pending"
  | "confirmed"
  | "checked_in"
  | "checked_out"
  | "cancelled";

export const VALID_TRANSITIONS: Record<BookingStatus, BookingStatus[]> = {
  pending: ["confirmed", "cancelled"],
  confirmed: ["checked_in", "cancelled"],
  checked_in: ["checked_out"],
  checked_out: [],
  cancelled: [],
};

// ── Booking Entity ──────────────────────────────────────────────────────
export interface Booking {
  id: string;
  property_id: string;
  unit_id: string;
  guest_id: string;
  check_in: string; // ISO date YYYY-MM-DD
  check_out: string; // ISO date YYYY-MM-DD
  status: BookingStatus;
  total_price: number;
  currency: string;
  guests: number;
  payment_id: string | null;
  idempotency_key: string | null;
  cancelled_at: string | null;
  cancellation_reason: string | null;
  created_at: string;
  updated_at: string;
}

// ── Request / Response DTOs ─────────────────────────────────────────────
export interface CreateBookingRequest {
  property_id: string;
  unit_id: string;
  guest_id: string;
  check_in: string;
  check_out: string;
  guests: number;
  payment_method_id: string;
  idempotency_key?: string;
}

export interface BookingResponse {
  id: string;
  property_id: string;
  unit_id: string;
  guest_id: string;
  check_in: string;
  check_out: string;
  status: BookingStatus;
  total_price: number;
  currency: string;
  guests: number;
  payment_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface BookingListQuery {
  guest_id?: string;
  property_id?: string;
  status?: BookingStatus;
  limit?: number;
  offset?: number;
}

export interface CancelBookingRequest {
  reason?: string;
}

// ── Downstream Service Types ────────────────────────────────────────────
export interface AvailabilityCheckResult {
  available: boolean;
  property_id: string;
  unit_id: string;
  check_in: string;
  check_out: string;
}

export interface AvailabilityHold {
  hold_id: string;
  property_id: string;
  unit_id: string;
  check_in: string;
  check_out: string;
  expires_at: string;
}

export interface PriceQuote {
  quote_id: string;
  property_id: string;
  unit_id: string;
  check_in: string;
  check_out: string;
  total_price: number;
  currency: string;
  nightly_rates: { date: string; amount: number }[];
}

export interface PaymentResult {
  payment_id: string;
  status: "succeeded" | "failed" | "pending";
  amount: number;
  currency: string;
}

export interface PropertyInfo {
  id: string;
  name: string;
  max_guests: number;
  status: string;
}

// ── Errors ──────────────────────────────────────────────────────────────
export class BookingError extends Error {
  constructor(
    message: string,
    public statusCode: number = 400,
    public code: string = "BOOKING_ERROR",
  ) {
    super(message);
    this.name = "BookingError";
  }
}

export class ConflictError extends BookingError {
  constructor(message: string) {
    super(message, 409, "CONFLICT");
    this.name = "ConflictError";
  }
}

export class NotFoundError extends BookingError {
  constructor(message: string) {
    super(message, 404, "NOT_FOUND");
    this.name = "NotFoundError";
  }
}

export class ServiceUnavailableError extends BookingError {
  constructor(message: string) {
    super(message, 503, "SERVICE_UNAVAILABLE");
    this.name = "ServiceUnavailableError";
  }
}

export class TimeoutError extends BookingError {
  constructor(message: string) {
    super(message, 504, "TIMEOUT");
    this.name = "TimeoutError";
  }
}

import type { CreateBookingRequest, BookingStatus } from "../types.ts";
import { BookingError } from "../types.ts";

const UUID_REGEX = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const DATE_REGEX = /^\d{4}-\d{2}-\d{2}$/;

export function validateCreateBookingRequest(body: unknown): CreateBookingRequest {
  if (!body || typeof body !== "object") {
    throw new BookingError("Request body is required", 400, "VALIDATION_ERROR");
  }

  const req = body as Record<string, unknown>;

  // Required fields
  const requiredFields = [
    "property_id",
    "unit_id",
    "guest_id",
    "check_in",
    "check_out",
    "guests",
    "payment_method_id",
  ];

  for (const field of requiredFields) {
    if (!req[field]) {
      throw new BookingError(
        `Missing required field: ${field}`,
        400,
        "VALIDATION_ERROR",
      );
    }
  }

  // UUID validation
  for (const field of ["property_id", "unit_id", "guest_id", "payment_method_id"]) {
    if (typeof req[field] !== "string" || !UUID_REGEX.test(req[field] as string)) {
      throw new BookingError(
        `Invalid UUID format for ${field}`,
        400,
        "VALIDATION_ERROR",
      );
    }
  }

  // Date validation
  for (const field of ["check_in", "check_out"]) {
    if (typeof req[field] !== "string" || !DATE_REGEX.test(req[field] as string)) {
      throw new BookingError(
        `Invalid date format for ${field}, expected YYYY-MM-DD`,
        400,
        "VALIDATION_ERROR",
      );
    }
  }

  const checkIn = new Date(req.check_in as string);
  const checkOut = new Date(req.check_out as string);
  const today = new Date();
  today.setHours(0, 0, 0, 0);

  if (isNaN(checkIn.getTime()) || isNaN(checkOut.getTime())) {
    throw new BookingError(
      "Invalid date values",
      400,
      "VALIDATION_ERROR",
    );
  }

  if (checkIn >= checkOut) {
    throw new BookingError(
      "check_in must be before check_out",
      400,
      "VALIDATION_ERROR",
    );
  }

  if (checkIn < today) {
    throw new BookingError(
      "check_in cannot be in the past",
      400,
      "VALIDATION_ERROR",
    );
  }

  // Guests validation
  if (typeof req.guests !== "number" || req.guests < 1) {
    throw new BookingError(
      "guests must be a positive number",
      400,
      "VALIDATION_ERROR",
    );
  }

  // Idempotency key (optional)
  if (req.idempotency_key !== undefined && typeof req.idempotency_key !== "string") {
    throw new BookingError(
      "idempotency_key must be a string",
      400,
      "VALIDATION_ERROR",
    );
  }

  return {
    property_id: req.property_id as string,
    unit_id: req.unit_id as string,
    guest_id: req.guest_id as string,
    check_in: req.check_in as string,
    check_out: req.check_out as string,
    guests: req.guests as number,
    payment_method_id: req.payment_method_id as string,
    idempotency_key: req.idempotency_key as string | undefined,
  };
}

export function validateBookingStatus(status: string): BookingStatus {
  const validStatuses: BookingStatus[] = [
    "pending",
    "confirmed",
    "checked_in",
    "checked_out",
    "cancelled",
  ];
  if (!validStatuses.includes(status as BookingStatus)) {
    throw new BookingError(
      `Invalid status: ${status}. Valid statuses: ${validStatuses.join(", ")}`,
      400,
      "VALIDATION_ERROR",
    );
  }
  return status as BookingStatus;
}

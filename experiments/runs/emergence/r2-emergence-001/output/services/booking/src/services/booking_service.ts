import type { BookingRepository } from "../repositories/booking_repository.ts";
import type { IdempotencyStore } from "../repositories/idempotency_store.ts";
import type { AvailabilityClient } from "../clients/availability_client.ts";
import type { PricingClient } from "../clients/pricing_client.ts";
import type { PaymentsClient } from "../clients/payments_client.ts";
import type { EventClient } from "../clients/event_client.ts";
import {
  type Booking,
  type BookingListQuery,
  type BookingResponse,
  type BookingStatus,
  BookingError,
  ConflictError,
  NotFoundError,
  type CreateBookingRequest,
  type CancelBookingRequest,
  VALID_TRANSITIONS,
} from "../types.ts";

export interface BookingServiceDeps {
  bookingRepo: BookingRepository;
  idempotencyStore: IdempotencyStore;
  availabilityClient: AvailabilityClient;
  pricingClient: PricingClient;
  paymentsClient: PaymentsClient;
  eventClient: EventClient;
  idempotencyTtlSeconds: number;
  totalFlowTimeoutMs: number;
}

export class BookingService {
  constructor(private deps: BookingServiceDeps) {}

  /**
   * The critical <3s booking flow.
   * Steps:
   *  1. Validate + idempotency check
   *  2. Check availability
   *  3. Get price quote
   *  4. Reserve dates (hold)
   *  5. Process payment
   *  6. Create booking record
   *  7. Emit booking.created event
   *
   * Compensating transactions:
   *  - If payment fails → release date hold
   *  - If booking record creation fails → refund payment + release date hold
   */
  async createBooking(request: CreateBookingRequest): Promise<BookingResponse> {
    const startTime = Date.now();
    const bookingId = crypto.randomUUID();
    const idempotencyKey = request.idempotency_key || crypto.randomUUID();

    // Step 1: Idempotency check
    if (request.idempotency_key) {
      const acquired = await this.deps.idempotencyStore.tryAcquire(
        idempotencyKey,
        bookingId,
        this.deps.idempotencyTtlSeconds,
      );

      if (!acquired) {
        // Return existing booking
        const existingBookingId = await this.deps.idempotencyStore.getBookingId(
          idempotencyKey,
        );
        if (existingBookingId) {
          const existing = await this.deps.bookingRepo.findById(existingBookingId);
          if (existing) {
            return this.toResponse(existing);
          }
        }
        // Key exists but no booking found — might be in-flight. Retry later.
        throw new ConflictError(
          "A booking with this idempotency key is already being processed",
        );
      }
    }

    let holdId: string | null = null;
    let paymentId: string | null = null;

    try {
      this.checkTimeout(startTime);

      // Step 2: Check availability
      const availability = await this.deps.availabilityClient.checkAvailability(
        request.property_id,
        request.unit_id,
        request.check_in,
        request.check_out,
      );

      if (!availability.available) {
        throw new BookingError(
          "Dates are not available for this unit",
          409,
          "NOT_AVAILABLE",
        );
      }

      this.checkTimeout(startTime);

      // Step 3: Get price quote
      const quote = await this.deps.pricingClient.getQuote(
        request.property_id,
        request.unit_id,
        request.check_in,
        request.check_out,
        request.guests,
      );

      this.checkTimeout(startTime);

      // Step 4: Reserve dates (hold)
      const hold = await this.deps.availabilityClient.holdDates(
        request.property_id,
        request.unit_id,
        request.check_in,
        request.check_out,
      );
      holdId = hold.hold_id;

      this.checkTimeout(startTime);

      // Step 5: Process payment
      const paymentResult = await this.deps.paymentsClient.processPayment(
        bookingId,
        quote.total_price,
        quote.currency,
        request.payment_method_id,
        `booking:${bookingId}`,
      );

      if (paymentResult.status === "failed") {
        // Compensate: release date hold
        await this.compensateReleaseDates(holdId);
        holdId = null;
        throw new BookingError(
          "Payment processing failed",
          402,
          "PAYMENT_FAILED",
        );
      }

      paymentId = paymentResult.payment_id;

      this.checkTimeout(startTime);

      // Step 6: Create booking record
      const booking = await this.deps.bookingRepo.create({
        id: bookingId,
        property_id: request.property_id,
        unit_id: request.unit_id,
        guest_id: request.guest_id,
        check_in: request.check_in,
        check_out: request.check_out,
        status: "confirmed",
        total_price: quote.total_price,
        currency: quote.currency,
        guests: request.guests,
        payment_id: paymentId,
        idempotency_key: idempotencyKey,
        cancelled_at: null,
        cancellation_reason: null,
      });

      // Step 7: Emit event (fire-and-forget, don't block on failure)
      this.deps.eventClient.emit({
        type: "booking.created",
        source: "booking-service",
        data: {
          booking_id: booking.id,
          property_id: booking.property_id,
          unit_id: booking.unit_id,
          guest_id: booking.guest_id,
          check_in: booking.check_in,
          check_out: booking.check_out,
          total_price: booking.total_price,
          currency: booking.currency,
          status: booking.status,
        },
      }).catch((err) =>
        console.error("Failed to emit booking.created event:", err)
      );

      const elapsed = Date.now() - startTime;
      console.log(`Booking ${bookingId} created in ${elapsed}ms`);

      return this.toResponse(booking);
    } catch (error) {
      // Compensating transactions
      if (paymentId) {
        await this.compensateRefundPayment(paymentId);
      }
      if (holdId) {
        await this.compensateReleaseDates(holdId);
      }
      // Release idempotency key on failure so it can be retried
      if (request.idempotency_key) {
        await this.deps.idempotencyStore.release(idempotencyKey).catch(() => {});
      }
      throw error;
    }
  }

  async getBooking(bookingId: string): Promise<BookingResponse> {
    const booking = await this.deps.bookingRepo.findById(bookingId);
    if (!booking) {
      throw new NotFoundError(`Booking ${bookingId} not found`);
    }
    return this.toResponse(booking);
  }

  async listBookings(query: BookingListQuery): Promise<BookingResponse[]> {
    const bookings = await this.deps.bookingRepo.list(query);
    return bookings.map((b) => this.toResponse(b));
  }

  async cancelBooking(
    bookingId: string,
    request: CancelBookingRequest,
  ): Promise<BookingResponse> {
    const booking = await this.deps.bookingRepo.findById(bookingId);
    if (!booking) {
      throw new NotFoundError(`Booking ${bookingId} not found`);
    }

    this.validateTransition(booking.status, "cancelled");

    const updated = await this.deps.bookingRepo.updateStatus(
      bookingId,
      "cancelled",
      {
        cancelled_at: new Date().toISOString(),
        cancellation_reason: request.reason || null,
      },
    );

    if (!updated) {
      throw new NotFoundError(`Booking ${bookingId} not found`);
    }

    // Refund payment if exists
    if (booking.payment_id) {
      this.deps.paymentsClient
        .refundPayment(booking.payment_id)
        .catch((err) =>
          console.error(`Failed to refund payment ${booking.payment_id}:`, err)
        );
    }

    // Emit cancellation event
    this.deps.eventClient.emit({
      type: "booking.cancelled",
      source: "booking-service",
      data: {
        booking_id: bookingId,
        property_id: booking.property_id,
        unit_id: booking.unit_id,
        reason: request.reason || "No reason provided",
      },
    }).catch((err) =>
      console.error("Failed to emit booking.cancelled event:", err)
    );

    return this.toResponse(updated);
  }

  async confirmBooking(bookingId: string): Promise<BookingResponse> {
    const booking = await this.deps.bookingRepo.findById(bookingId);
    if (!booking) {
      throw new NotFoundError(`Booking ${bookingId} not found`);
    }

    this.validateTransition(booking.status, "confirmed");

    const updated = await this.deps.bookingRepo.updateStatus(
      bookingId,
      "confirmed",
    );

    if (!updated) {
      throw new NotFoundError(`Booking ${bookingId} not found`);
    }

    // Emit confirmation event
    this.deps.eventClient.emit({
      type: "booking.confirmed",
      source: "booking-service",
      data: {
        booking_id: bookingId,
        property_id: booking.property_id,
        unit_id: booking.unit_id,
      },
    }).catch((err) =>
      console.error("Failed to emit booking.confirmed event:", err)
    );

    return this.toResponse(updated);
  }

  // ── Private Helpers ──────────────────────────────────────────────────

  private checkTimeout(startTime: number): void {
    const elapsed = Date.now() - startTime;
    const remaining = this.deps.totalFlowTimeoutMs - elapsed;
    if (remaining < 200) {
      // Leave 200ms buffer
      throw new BookingError(
        `Booking flow timeout: ${elapsed}ms elapsed, only ${remaining}ms remaining`,
        504,
        "TIMEOUT",
      );
    }
  }

  private validateTransition(
    currentStatus: BookingStatus,
    targetStatus: BookingStatus,
  ): void {
    const allowed = VALID_TRANSITIONS[currentStatus];
    if (!allowed || !allowed.includes(targetStatus)) {
      throw new BookingError(
        `Cannot transition from '${currentStatus}' to '${targetStatus}'`,
        409,
        "INVALID_STATE_TRANSITION",
      );
    }
  }

  private async compensateReleaseDates(holdId: string): Promise<void> {
    try {
      await this.deps.availabilityClient.releaseDates(holdId);
      console.log(`Compensated: released hold ${holdId}`);
    } catch (err) {
      console.error(`Failed to release hold ${holdId} during compensation:`, err);
    }
  }

  private async compensateRefundPayment(paymentId: string): Promise<void> {
    try {
      await this.deps.paymentsClient.refundPayment(paymentId);
      console.log(`Compensated: refunded payment ${paymentId}`);
    } catch (err) {
      console.error(
        `Failed to refund payment ${paymentId} during compensation:`,
        err,
      );
    }
  }

  private toResponse(booking: Booking): BookingResponse {
    return {
      id: booking.id,
      property_id: booking.property_id,
      unit_id: booking.unit_id,
      guest_id: booking.guest_id,
      check_in: booking.check_in,
      check_out: booking.check_out,
      status: booking.status,
      total_price: booking.total_price,
      currency: booking.currency,
      guests: booking.guests,
      payment_id: booking.payment_id,
      created_at: booking.created_at,
      updated_at: booking.updated_at,
    };
  }
}

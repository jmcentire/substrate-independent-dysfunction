import { Context, Status } from "../deps.ts";
import { BookingService } from "../services/booking_service.ts";
import { validateCreateBookingRequest, validateBookingStatus } from "../services/validator.ts";
import { BookingError } from "../types.ts";
import type { BookingListQuery, BookingStatus, CancelBookingRequest } from "../types.ts";

export class BookingHandler {
  constructor(private service: BookingService) {}

  /**
   * POST /bookings — Create a new booking (the <3s checkout flow)
   */
  createBooking = async (ctx: Context) => {
    const body = await this.parseBody(ctx);
    const request = validateCreateBookingRequest(body);
    const booking = await this.service.createBooking(request);
    ctx.response.status = Status.Created;
    ctx.response.body = { data: booking };
  };

  /**
   * GET /bookings/:booking_id — Get booking details
   */
  getBooking = async (ctx: Context) => {
    const bookingId = this.getParam(ctx, "booking_id");
    const booking = await this.service.getBooking(bookingId);
    ctx.response.status = Status.OK;
    ctx.response.body = { data: booking };
  };

  /**
   * GET /bookings — List/search bookings
   */
  listBookings = async (ctx: Context) => {
    const params = ctx.request.url.searchParams;
    const query: BookingListQuery = {};

    if (params.has("guest_id")) query.guest_id = params.get("guest_id")!;
    if (params.has("property_id")) query.property_id = params.get("property_id")!;
    if (params.has("status")) {
      query.status = validateBookingStatus(params.get("status")!);
    }
    if (params.has("limit")) query.limit = parseInt(params.get("limit")!);
    if (params.has("offset")) query.offset = parseInt(params.get("offset")!);

    const bookings = await this.service.listBookings(query);
    ctx.response.status = Status.OK;
    ctx.response.body = { data: bookings };
  };

  /**
   * PUT /bookings/:booking_id/cancel — Cancel a booking
   */
  cancelBooking = async (ctx: Context) => {
    const bookingId = this.getParam(ctx, "booking_id");
    const body = await this.parseBody(ctx).catch(() => ({}));
    const request: CancelBookingRequest = {
      reason: (body as Record<string, unknown>)?.reason as string | undefined,
    };
    const booking = await this.service.cancelBooking(bookingId, request);
    ctx.response.status = Status.OK;
    ctx.response.body = { data: booking };
  };

  /**
   * PUT /bookings/:booking_id/confirm — Confirm a booking
   */
  confirmBooking = async (ctx: Context) => {
    const bookingId = this.getParam(ctx, "booking_id");
    const booking = await this.service.confirmBooking(bookingId);
    ctx.response.status = Status.OK;
    ctx.response.body = { data: booking };
  };

  // ── Helpers ──────────────────────────────────────────────────────────

  private async parseBody(ctx: Context): Promise<unknown> {
    try {
      const body = ctx.request.body();
      if (body.type === "json") {
        return await body.value;
      }
      throw new BookingError("Request body must be JSON", 400, "VALIDATION_ERROR");
    } catch (error) {
      if (error instanceof BookingError) throw error;
      throw new BookingError("Invalid request body", 400, "VALIDATION_ERROR");
    }
  }

  private getParam(ctx: Context, name: string): string {
    // Oak router puts params on the context's params
    const params = (ctx as unknown as { params: Record<string, string> }).params;
    const value = params?.[name];
    if (!value) {
      throw new BookingError(`Missing path parameter: ${name}`, 400, "VALIDATION_ERROR");
    }
    return value;
  }
}

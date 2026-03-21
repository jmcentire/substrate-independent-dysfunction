import { Router } from "./deps.ts";
import type { BookingHandler } from "./handlers/booking_handler.ts";

export function createRouter(handler: BookingHandler): Router {
  const router = new Router();

  // Health check
  router.get("/health", (ctx) => {
    ctx.response.status = 200;
    ctx.response.body = {
      status: "healthy",
      service: "booking",
      timestamp: new Date().toISOString(),
    };
  });

  // Booking routes
  router.post("/bookings", handler.createBooking);
  router.get("/bookings", handler.listBookings);
  router.get("/bookings/:booking_id", handler.getBooking);
  router.put("/bookings/:booking_id/cancel", handler.cancelBooking);
  router.put("/bookings/:booking_id/confirm", handler.confirmBooking);

  return router;
}

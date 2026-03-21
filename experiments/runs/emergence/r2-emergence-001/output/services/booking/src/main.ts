import { loadConfig } from "./config.ts";
import { getPool } from "./db/client.ts";
import { getRedis } from "./db/redis_client.ts";
import { PostgresBookingRepository } from "./repositories/booking_repository.ts";
import { RedisIdempotencyStore } from "./repositories/idempotency_store.ts";
import { HttpAvailabilityClient } from "./clients/availability_client.ts";
import { HttpPricingClient } from "./clients/pricing_client.ts";
import { HttpPaymentsClient } from "./clients/payments_client.ts";
import { HttpEventClient } from "./clients/event_client.ts";
import { BookingService } from "./services/booking_service.ts";
import { BookingHandler } from "./handlers/booking_handler.ts";
import { createRouter } from "./router.ts";
import { createServer } from "./server.ts";

async function main() {
  const config = loadConfig();

  console.log("Booking Service starting...");
  console.log(`Port: ${config.port}`);

  // Initialize infrastructure
  const pool = getPool(config);
  const redis = await getRedis(config);

  // Initialize repositories
  const bookingRepo = new PostgresBookingRepository(pool);
  const idempotencyStore = new RedisIdempotencyStore(redis as any);

  // Initialize service clients
  const availabilityClient = new HttpAvailabilityClient(
    config.availabilityServiceUrl,
    config.serviceCallTimeout,
  );
  const pricingClient = new HttpPricingClient(
    config.pricingServiceUrl,
    config.serviceCallTimeout,
  );
  const paymentsClient = new HttpPaymentsClient(
    config.paymentsServiceUrl,
    config.serviceCallTimeout,
  );
  const eventClient = new HttpEventClient(
    config.eventServiceUrl,
    config.serviceCallTimeout,
  );

  // Initialize service
  const bookingService = new BookingService({
    bookingRepo,
    idempotencyStore,
    availabilityClient,
    pricingClient,
    paymentsClient,
    eventClient,
    idempotencyTtlSeconds: config.idempotencyTtlSeconds,
    totalFlowTimeoutMs: config.totalFlowTimeout,
  });

  // Initialize handler and router
  const handler = new BookingHandler(bookingService);
  const router = createRouter(handler);
  const app = createServer(router);

  // Start server
  console.log(`Booking Service listening on ${config.hostname}:${config.port}`);
  await app.listen({ hostname: config.hostname, port: config.port });
}

main().catch((err) => {
  console.error("Failed to start Booking Service:", err);
  Deno.exit(1);
});

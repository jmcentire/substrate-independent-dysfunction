/**
 * Availability Service - Entry Point
 *
 * Service #1: Bitmask cache with O(1) availability checks.
 * No dependencies on other services.
 */

import { loadConfig } from "./config.ts";
import { createPool } from "./db.ts";
import { startServer } from "./server.ts";

async function main() {
  console.log("Initializing Availability Service...");

  const config = loadConfig();

  // Connect to PostgreSQL
  console.log(`Connecting to PostgreSQL at ${config.database.host}:${config.database.port}...`);
  const pool = await createPool(config.database);

  // Connect to Redis
  console.log(`Connecting to Redis at ${config.redis.host}:${config.redis.port}...`);
  const { connect } = await import("redis");
  const redis = await connect({
    hostname: config.redis.host,
    port: config.redis.port,
    password: config.redis.password,
    db: config.redis.db,
  });

  // Handle shutdown gracefully
  const shutdown = async () => {
    console.log("Shutting down Availability Service...");
    await pool.end();
    redis.close();
    Deno.exit(0);
  };

  Deno.addSignalListener("SIGINT", shutdown);
  Deno.addSignalListener("SIGTERM", shutdown);

  // Start server
  await startServer({ pool, redis, config });
}

main().catch((err) => {
  console.error("Failed to start Availability Service:", err);
  Deno.exit(1);
});

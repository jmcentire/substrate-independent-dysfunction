// Re-export all dependencies for centralized management
export { Application, Router, Context } from "oak";
export type { RouterContext } from "oak";
export { Pool } from "postgres";
export type { PoolClient } from "postgres";
export { connect as connectRedis } from "redis";
export type { Redis } from "redis";
export {
  assert,
  assertEquals,
  assertExists,
  assertRejects,
  assertThrows,
  assertStrictEquals,
} from "testing";
export { describe, it, beforeEach, afterEach, beforeAll, afterAll } from "testing/bdd";
export { stub, spy, returnsNext, assertSpyCalls } from "testing/mock";
export type { Stub, Spy } from "testing/mock";

/**
 * Unit tests for the repository layer.
 * Tests the repository methods with a mock database pool.
 */

import { assertEquals } from "testing";
import { describe, it, beforeEach } from "testing/bdd";

import { AvailabilityRepository } from "../../src/repository.ts";
import { fullMonthMask } from "../../src/bitmask.ts";
import type { DbPool, DbClient } from "../../src/db.ts";

/** Simple mock pool that tracks queries */
function createMockPool(): DbPool & { queries: Array<{ query: string; args: unknown[] }> } {
  const queries: Array<{ query: string; args: unknown[] }> = [];
  const data = new Map<string, Record<string, unknown>>();

  return {
    queries,
    async connect(): Promise<DbClient> {
      return {
        async queryObject<T = Record<string, unknown>>(query: string, args?: unknown[]): Promise<{ rows: T[] }> {
          queries.push({ query, args: args || [] });

          if (query.includes("SELECT") && query.includes("FROM availability") && !query.includes("availability_log")) {
            const unitId = args?.[0] as string;
            const year = args?.[1] as number;
            const month = args?.[2] as number;
            const key = `${unitId}:${year}:${month}`;
            const record = data.get(key);
            if (record) {
              return { rows: [record] as unknown as T[] };
            }
            return { rows: [] };
          }

          if (query.includes("INSERT INTO availability")) {
            const unitId = args?.[0] as string;
            const year = args?.[1] as number;
            const month = args?.[2] as number;
            const bitmask = args?.[3] as number;
            const key = `${unitId}:${year}:${month}`;
            const record = {
              id: "mock-id",
              unit_id: unitId,
              year,
              month,
              bitmask,
              updated_at: new Date(),
            };
            data.set(key, record);
            return { rows: [record] as unknown as T[] };
          }

          if (query.includes("INSERT INTO availability_log")) {
            return { rows: [] };
          }

          return { rows: [] };
        },
        release() {},
      };
    },
    async end() {},
  };
}

describe("AvailabilityRepository", () => {
  let pool: ReturnType<typeof createMockPool>;
  let repo: AvailabilityRepository;

  beforeEach(() => {
    pool = createMockPool();
    repo = new AvailabilityRepository(pool);
  });

  describe("getMonthAvailability", () => {
    it("should return null when no record exists", async () => {
      const result = await repo.getMonthAvailability("unit-1", 2024, 3);
      assertEquals(result, null);
    });

    it("should return record when it exists", async () => {
      // First create a record
      await repo.upsertMonthAvailability("unit-1", 2024, 3, fullMonthMask(2024, 3));

      const result = await repo.getMonthAvailability("unit-1", 2024, 3);
      assertEquals(result?.unitId, "unit-1");
      assertEquals(result?.year, 2024);
      assertEquals(result?.month, 3);
      assertEquals(result?.bitmask, fullMonthMask(2024, 3));
    });
  });

  describe("getEffectiveBitmask", () => {
    it("should return full month mask when no record exists", async () => {
      const bitmask = await repo.getEffectiveBitmask("unit-1", 2024, 3);
      assertEquals(bitmask, fullMonthMask(2024, 3));
    });

    it("should return stored bitmask when record exists", async () => {
      await repo.upsertMonthAvailability("unit-1", 2024, 3, 42);
      const bitmask = await repo.getEffectiveBitmask("unit-1", 2024, 3);
      assertEquals(bitmask, 42);
    });
  });

  describe("upsertMonthAvailability", () => {
    it("should create a new record", async () => {
      const result = await repo.upsertMonthAvailability("unit-1", 2024, 3, 12345);
      assertEquals(result.unitId, "unit-1");
      assertEquals(result.bitmask, 12345);
    });

    it("should update existing record", async () => {
      await repo.upsertMonthAvailability("unit-1", 2024, 3, 100);
      const result = await repo.upsertMonthAvailability("unit-1", 2024, 3, 200);
      assertEquals(result.bitmask, 200);
    });
  });

  describe("logChange", () => {
    it("should log a change without error", async () => {
      await repo.logChange("unit-1", "block", "2024-03-10", "2024-03-15", "admin");
      // Verify the log insert query was executed
      const logQuery = pool.queries.find((q) => q.query.includes("availability_log"));
      assertEquals(logQuery !== undefined, true);
    });
  });
});

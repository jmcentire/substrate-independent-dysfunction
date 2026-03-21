/**
 * Availability Service — Calendar Logic Tests (from real codebase)
 *
 * Tests the bitmask-based calendar operation. Each month's availability is
 * stored as a 32-bit integer where bit N (1-31) represents day N.
 * 1 = blocked, 0 = available.
 *
 * Uses Effect-TS for dependency injection (CacheDaoTest provides mock data).
 */

import { assertEquals, assertNotEquals } from "@std/assert";
import { Effect } from "effect";
import { CacheDaoTest } from "../../src/dao/cache.ts";
import { calendar } from "../../src/logic/calendar.ts";
import { UnitId } from "../../src/dao/types.ts";
import { NotFound } from "../../src/dao/errors.ts";

Deno.test("Single month", () => {
  const results = Effect.runSync(
    Effect.provide(
      CacheDaoTest({
        fetchMonthBitmap: ({ unitId, year_month }) => {
          assertEquals(unitId, "DEADBEEF-CAFE-0000-0000-000000000000");
          assertEquals(year_month, "2025-12");
          return Effect.succeed({ days: 0b11100111111111110000000011111110 });
        },
      }),
    )(
      calendar({
        unitId: UnitId("DEADBEEF-CAFE-0000-0000-000000000000"),
        fromYearMonth: "2025-12",
        toYearMonth: "2025-12",
      }),
    ).pipe(Effect.catchAllCause(Effect.die)),
  );

  assertEquals(results.length, 1);
  const [dec] = results;
  assertEquals(dec, "1111111000000001111111111100111");
});

Deno.test("Multiple months", () => {
  const results = Effect.runSync(
    Effect.provide(
      CacheDaoTest({
        fetchMonthBitmap: ({ unitId, year_month }) => {
          assertEquals(unitId, "DEADBEEF-CAFE-0000-0000-000000000000");
          const bitmap = {
            "2025-12": { days: 0b11100111111111110000000011111110 },
            "2026-01": { days: 0b00000111111111111111111111111110 },
            "2026-02": { days: 0b11111111111111111111111111000000 },
            "2026-03": { days: 0b00000000000000000000000000000000 },
            "2026-04": { days: 0b00000000000000000000000000011110 },
          }[year_month];
          assertNotEquals(bitmap, undefined);
          return Effect.succeed(bitmap!);
        },
      }),
    )(
      calendar({
        unitId: UnitId("DEADBEEF-CAFE-0000-0000-000000000000"),
        fromYearMonth: "2025-12",
        toYearMonth: "2026-04",
      }),
    ).pipe(Effect.catchAllCause(Effect.die)),
  );

  assertEquals(results.length, 5);
  const [dec, jan, feb, mar, apr] = results;
  assertEquals(dec, "1111111000000001111111111100111");
  assertEquals(jan, "1111111111111111111111111100000");
  assertEquals(feb, "0000011111111111111111111111");
  assertEquals(mar, "0000000000000000000000000000000");
  assertEquals(apr, "111100000000000000000000000000");
});

Deno.test("No bitmap for a given month fails gracefully", () => {
  const results = Effect.runSync(
    Effect.provide(
      CacheDaoTest({
        fetchMonthBitmap: ({ unitId, year_month }) => {
          if (year_month === "2026-02") {
            return Effect.fail(new NotFound());
          }
          const bitmap = {
            "2025-12": { days: 0b11100111111111110000000011111110 },
            "2026-01": { days: 0b00000111111111111111111111111110 },
          }[year_month];
          assertNotEquals(bitmap, undefined);
          return Effect.succeed(bitmap!);
        },
      }),
    )(
      calendar({
        unitId: UnitId("DEADBEEF-CAFE-0000-0000-000000000000"),
        fromYearMonth: "2025-12",
        toYearMonth: "2026-02",
      }),
    ).pipe(Effect.catchAllCause(Effect.die)),
  );

  assertEquals(results.length, 3);
  const [dec, jan, feb] = results;
  assertEquals(dec, "1111111000000001111111111100111");
  assertEquals(jan, "1111111111111111111111111100000");
  // Missing month returns all-available (all bits = 1)
  assertEquals(feb, "1111111111111111111111111111");
});

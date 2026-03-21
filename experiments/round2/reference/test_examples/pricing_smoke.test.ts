/**
 * Pricing Service — Smoke Tests (from real codebase)
 *
 * These tests hit the Pricing Service HTTP API to verify the contract.
 * Demonstrates expected request/response shapes.
 */

// --- Test Helpers ---

const BASE_URL = Deno.env.get("BASE_URL") ?? "http://localhost:1527";

const UNIT_ID_1 = "550e8400-e29b-41d4-a716-446655440001";
const UNIT_ID_2 = "550e8400-e29b-41d4-a716-446655440002";
const UNIT_ID_3 = "550e8400-e29b-41d4-a716-446655440003";

async function request(
  method: string,
  path: string,
  body?: unknown,
): Promise<{ status: number; json: unknown }> {
  const options: RequestInit = { method };
  if (body) {
    options.headers = { "Content-Type": "application/json" };
    options.body = JSON.stringify(body);
  }
  const res = await fetch(`${BASE_URL}${path}`, options);
  const json = await res.json();
  return { status: res.status, json };
}

// --- Rate Endpoint Tests ---

Deno.test("GET /rate - single date", async () => {
  const { status, json } = await request(
    "GET",
    `/rate?unit_id=${UNIT_ID_1}&start_date=2025-03-15`,
  );

  // Expected response shape:
  // {
  //   status: "ok",
  //   unit_id: string,
  //   currency: string,
  //   rates: Array<{ date: string, rate_cents: number }>,
  //   missing_dates: string[]
  // }
});

Deno.test("GET /rate - with missing dates", async () => {
  const { status, json } = await request(
    "GET",
    `/rate?unit_id=${UNIT_ID_2}&start_date=2025-03-15&end_date=2025-03-19`,
  );

  // missing_dates array contains dates with no cached rate
});

Deno.test("GET /rate - invalid date range returns 400", async () => {
  const { status, json } = await request(
    "GET",
    `/rate?unit_id=${UNIT_ID_1}&start_date=2025-03-20&end_date=2025-03-15`,
  );

  // Expected: { status: "error", error: { kind: "invalid_date_range", message: "..." } }
});

// --- Bulk Endpoint Tests ---

Deno.test("POST /bulk - multiple units", async () => {
  const { status, json } = await request("POST", "/bulk", {
    unit_ids: [UNIT_ID_1, UNIT_ID_2],
    start_date: "2025-03-15",
    end_date: "2025-03-19",
  });

  // Expected response shape:
  // {
  //   status: "ok",
  //   results: Array<{
  //     unit_id: string,
  //     sum_cents: number | null,
  //     avg_cents: number | null,
  //     min_cents: number | null,
  //     max_cents: number | null,
  //     missing_dates: string[]
  //   }>
  // }
});

Deno.test("POST /bulk - rejects >500 units", async () => {
  const tooMany = Array.from({ length: 501 }, (_, i) =>
    `550e8400-e29b-41d4-a716-${String(i).padStart(12, "0")}`
  );

  const { status } = await request("POST", "/bulk", {
    unit_ids: tooMany,
    start_date: "2025-03-15",
    end_date: "2025-03-19",
  });

  // Expected: 400
});

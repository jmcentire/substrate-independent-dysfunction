import { assertEquals, assertRejects } from "https://deno.land/std@0.208.0/assert/mod.ts";
import { BaseServiceClient } from "../../src/clients/base_client.ts";
import { ServiceUnavailableError } from "../../src/types.ts";

// We create a testable subclass since BaseServiceClient has protected methods
class TestClient extends BaseServiceClient {
  async testFetch(path: string, options?: RequestInit): Promise<Response> {
    return this.fetchWithTimeout(path, options);
  }
  async testParseJson<T>(response: Response): Promise<T> {
    return this.parseJsonResponse<T>(response);
  }
}

Deno.test("BaseServiceClient - parseJsonResponse throws on non-OK status", async () => {
  const client = new TestClient("http://localhost:9999", 1000);
  const response = new Response("Bad Request", { status: 400 });
  await assertRejects(
    () => client.testParseJson(response),
    ServiceUnavailableError,
    "400",
  );
});

Deno.test("BaseServiceClient - parseJsonResponse returns parsed JSON", async () => {
  const client = new TestClient("http://localhost:9999", 1000);
  const response = new Response(JSON.stringify({ ok: true }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
  const result = await client.testParseJson<{ ok: boolean }>(response);
  assertEquals(result.ok, true);
});

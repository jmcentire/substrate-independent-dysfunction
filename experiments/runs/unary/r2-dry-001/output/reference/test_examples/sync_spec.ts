/**
 * Sync Service — HTTP Connector Tests (from real codebase)
 *
 * Tests the HTTP connector layer that handles transport to PMS APIs.
 * Uses Effect-TS dependency injection with mock SendFn providers.
 * Demonstrates error handling: rate limits, auth errors, server errors.
 */

import { assertEquals, assertInstanceOf } from "@std/assert";
import { Cause, Effect, Exit, Option, Schema } from "effect";
import { HttpConnector, HttpConnectorTest } from "../src/connectors/http.ts";
import type {
  HttpRequest,
  HttpResponse,
  SendFn,
} from "../src/connectors/types.ts";
import {
  AuthError,
  ConnectorError,
  RateLimitError,
} from "../src/connectors/errors.ts";

// --- Test Utilities ---

const createMockSend = (
  handler: (request: HttpRequest) => HttpResponse<unknown>,
): SendFn => {
  return (request: HttpRequest) => {
    const result = handler(request);
    return Effect.succeed(result);
  };
};

const runWithMock = <A, E>(
  handler: (request: HttpRequest) => HttpResponse<unknown>,
  testEffect: Effect.Effect<A, E, HttpConnector>,
) =>
  Effect.runPromise(
    testEffect.pipe(Effect.provide(HttpConnectorTest(createMockSend(handler)))),
  );

// --- Response Schemas ---

const UserSchema = Schema.Struct({
  id: Schema.Number,
  name: Schema.optional(Schema.String),
});

// --- Success Cases ---

Deno.test("HttpConnector - validates response with Effect Schema", async () => {
  const mockResponse: HttpResponse<unknown> = {
    status: 200,
    headers: { "content-type": "application/json" },
    body: { id: 123 },
  };

  await runWithMock(
    () => mockResponse,
    Effect.gen(function* () {
      const connector = yield* HttpConnector;
      const result = yield* connector.send({
        method: "GET",
        path: "/users/123",
      });
      assertEquals(result.status, 200);
      const user = yield* Schema.decodeUnknown(UserSchema)(result.body);
      assertEquals(user.id, 123);
    }),
  );
});

// --- Error Cases ---

Deno.test("HttpConnector - rate limit error", async () => {
  const mockSend: SendFn = () =>
    Effect.fail(
      new RateLimitError({
        message: "Rate limit exceeded",
        retryAfterMs: 60000,
      }),
    );

  const program = Effect.gen(function* () {
    const connector = yield* HttpConnector;
    yield* connector.send({ method: "GET", path: "/api" });
  });

  const exit = await Effect.runPromiseExit(
    program.pipe(Effect.provide(HttpConnectorTest(mockSend))),
  );

  if (Exit.isFailure(exit)) {
    const errorOpt = Cause.failureOption(exit.cause);
    if (Option.isSome(errorOpt)) {
      assertInstanceOf(errorOpt.value, RateLimitError);
      assertEquals((errorOpt.value as RateLimitError).retryAfterMs, 60000);
    }
  }
});

Deno.test("HttpConnector - auth error (401)", async () => {
  const mockSend: SendFn = () =>
    Effect.fail(
      new AuthError({ message: "Unauthorized", statusCode: 401 }),
    );

  const program = Effect.gen(function* () {
    const connector = yield* HttpConnector;
    yield* connector.send({ method: "GET", path: "/protected" });
  });

  const exit = await Effect.runPromiseExit(
    program.pipe(Effect.provide(HttpConnectorTest(mockSend))),
  );

  if (Exit.isFailure(exit)) {
    const errorOpt = Cause.failureOption(exit.cause);
    if (Option.isSome(errorOpt)) {
      assertInstanceOf(errorOpt.value, AuthError);
      assertEquals((errorOpt.value as AuthError).statusCode, 401);
    }
  }
});

Deno.test("HttpConnector - server error (500)", async () => {
  const mockSend: SendFn = () =>
    Effect.fail(
      new ConnectorError({
        message: "Server error",
        statusCode: 500,
        responseBody: { error: "Internal Server Error" },
      }),
    );

  const program = Effect.gen(function* () {
    const connector = yield* HttpConnector;
    yield* connector.send({ method: "GET", path: "/api" });
  });

  const exit = await Effect.runPromiseExit(
    program.pipe(Effect.provide(HttpConnectorTest(mockSend))),
  );

  if (Exit.isFailure(exit)) {
    const errorOpt = Cause.failureOption(exit.cause);
    if (Option.isSome(errorOpt)) {
      assertInstanceOf(errorOpt.value, ConnectorError);
      assertEquals((errorOpt.value as ConnectorError).statusCode, 500);
    }
  }
});

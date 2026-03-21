// ============================================================
// Error Handler Middleware
// ============================================================

import type { Context, Next } from "https://deno.land/x/oak@v12.6.1/mod.ts";

export async function errorHandler(ctx: Context, next: Next) {
  try {
    await next();
  } catch (error) {
    const status = (error as any).statusCode ?? (error as any).status ?? 500;
    const message = error instanceof Error ? error.message : "Internal Server Error";

    console.error(`[Error] ${status} - ${message}`, error);

    ctx.response.status = status;
    ctx.response.body = {
      success: false,
      error: message,
    };
  }
}

export async function requestLogger(ctx: Context, next: Next) {
  const start = Date.now();
  await next();
  const ms = Date.now() - start;
  console.log(`${ctx.request.method} ${ctx.request.url.pathname} - ${ctx.response.status} (${ms}ms)`);
}

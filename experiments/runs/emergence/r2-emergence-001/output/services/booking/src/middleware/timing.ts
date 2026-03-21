import type { Context } from "../deps.ts";

export async function timingMiddleware(
  ctx: Context,
  next: () => Promise<unknown>,
): Promise<void> {
  const start = Date.now();
  await next();
  const elapsed = Date.now() - start;
  ctx.response.headers.set("X-Response-Time", `${elapsed}ms`);

  const method = ctx.request.method;
  const url = ctx.request.url.pathname;
  const status = ctx.response.status;
  console.log(`${method} ${url} ${status} ${elapsed}ms`);
}

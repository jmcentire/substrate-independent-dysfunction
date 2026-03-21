import { Application } from "./deps.ts";
import type { Router } from "./deps.ts";
import { errorHandler } from "./middleware/error_handler.ts";
import { timingMiddleware } from "./middleware/timing.ts";

export function createServer(router: Router): Application {
  const app = new Application();

  // Middleware pipeline
  app.use(timingMiddleware);
  app.use(errorHandler);

  // CORS headers
  app.use(async (ctx, next) => {
    ctx.response.headers.set("Access-Control-Allow-Origin", "*");
    ctx.response.headers.set(
      "Access-Control-Allow-Methods",
      "GET, POST, PUT, DELETE, OPTIONS",
    );
    ctx.response.headers.set(
      "Access-Control-Allow-Headers",
      "Content-Type, Authorization, X-Idempotency-Key",
    );
    if (ctx.request.method === "OPTIONS") {
      ctx.response.status = 204;
      return;
    }
    await next();
  });

  // Routes
  app.use(router.routes());
  app.use(router.allowedMethods());

  return app;
}

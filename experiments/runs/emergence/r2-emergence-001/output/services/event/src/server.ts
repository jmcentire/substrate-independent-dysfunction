import { serve } from "../deps.ts";
import { createRouter } from "./router.ts";
import { EventService } from "./service.ts";

export interface ServerHandle {
  close(): void;
  port: number;
}

/**
 * Start the HTTP server for the Event Service.
 */
export async function startServer(
  service: EventService,
  port: number
): Promise<ServerHandle> {
  const handler = createRouter(service);

  const ac = new AbortController();

  const serverPromise = serve(handler, {
    port,
    signal: ac.signal,
    onListen({ hostname, port: actualPort }) {
      console.log(`Event service listening on http://${hostname}:${actualPort}`);
    },
  });

  // Give the server a moment to start
  await new Promise((resolve) => setTimeout(resolve, 100));

  return {
    close() {
      ac.abort();
    },
    port,
  };
}

import { BaseServiceClient } from "./base_client.ts";

export interface EventClient {
  emit(event: {
    type: string;
    source: string;
    data: Record<string, unknown>;
  }): Promise<void>;
}

export class HttpEventClient extends BaseServiceClient implements EventClient {
  async emit(event: {
    type: string;
    source: string;
    data: Record<string, unknown>;
  }): Promise<void> {
    try {
      await this.fetchWithTimeout(`/events`, {
        method: "POST",
        body: JSON.stringify(event),
      });
    } catch (_error) {
      // Event emission is fire-and-forget; don't fail the booking flow
      console.error(`Failed to emit event ${event.type}:`, _error);
    }
  }
}

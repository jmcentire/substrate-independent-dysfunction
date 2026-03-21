import { BaseServiceClient } from "./base_client.ts";
import type { PriceQuote } from "../types.ts";

export interface PricingClient {
  getQuote(
    propertyId: string,
    unitId: string,
    checkIn: string,
    checkOut: string,
    guests: number,
  ): Promise<PriceQuote>;
}

export class HttpPricingClient extends BaseServiceClient implements PricingClient {
  async getQuote(
    propertyId: string,
    unitId: string,
    checkIn: string,
    checkOut: string,
    guests: number,
  ): Promise<PriceQuote> {
    const response = await this.fetchWithTimeout(`/pricing/quote`, {
      method: "POST",
      body: JSON.stringify({
        property_id: propertyId,
        unit_id: unitId,
        check_in: checkIn,
        check_out: checkOut,
        guests,
      }),
    });
    return this.parseJsonResponse<PriceQuote>(response);
  }
}

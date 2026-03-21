import { BaseServiceClient } from "./base_client.ts";
import type { PaymentResult } from "../types.ts";

export interface PaymentsClient {
  processPayment(
    bookingId: string,
    amount: number,
    currency: string,
    paymentMethodId: string,
    idempotencyKey: string,
  ): Promise<PaymentResult>;

  refundPayment(paymentId: string): Promise<void>;
}

export class HttpPaymentsClient extends BaseServiceClient implements PaymentsClient {
  async processPayment(
    bookingId: string,
    amount: number,
    currency: string,
    paymentMethodId: string,
    idempotencyKey: string,
  ): Promise<PaymentResult> {
    const response = await this.fetchWithTimeout(`/payments/charge`, {
      method: "POST",
      body: JSON.stringify({
        booking_id: bookingId,
        amount,
        currency,
        payment_method_id: paymentMethodId,
        idempotency_key: idempotencyKey,
      }),
    });
    return this.parseJsonResponse<PaymentResult>(response);
  }

  async refundPayment(paymentId: string): Promise<void> {
    await this.fetchWithTimeout(`/payments/${paymentId}/refund`, {
      method: "POST",
    });
  }
}

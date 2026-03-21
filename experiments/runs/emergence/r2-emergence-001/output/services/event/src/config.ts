export interface Config {
  port: number;
  databaseUrl: string;
  kafkaBrokers: string[];
  kafkaTopicPrefix: string;
  useMockKafka: boolean;
  useMockDb: boolean;
  webhookTimeoutMs: number;
  webhookMaxRetries: number;
}

export function loadConfig(): Config {
  return {
    port: parseInt(Deno.env.get("PORT") || "8004", 10),
    databaseUrl: Deno.env.get("DATABASE_URL") || "postgresql://localhost:5432/wander_events",
    kafkaBrokers: (Deno.env.get("KAFKA_BROKERS") || "localhost:9092").split(","),
    kafkaTopicPrefix: Deno.env.get("KAFKA_TOPIC_PREFIX") || "wander.",
    useMockKafka: Deno.env.get("USE_MOCK_KAFKA") !== "false",
    useMockDb: Deno.env.get("USE_MOCK_DB") !== "false",
    webhookTimeoutMs: parseInt(Deno.env.get("WEBHOOK_TIMEOUT_MS") || "5000", 10),
    webhookMaxRetries: parseInt(Deno.env.get("WEBHOOK_MAX_RETRIES") || "3", 10),
  };
}

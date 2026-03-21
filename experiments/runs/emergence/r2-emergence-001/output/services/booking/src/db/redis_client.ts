import { connect } from "redis";
import type { Redis } from "redis";
import { Config } from "../config.ts";

let client: Redis | null = null;

export async function getRedis(config: Config): Promise<Redis> {
  if (!client) {
    client = await connect({
      hostname: config.redisHost,
      port: config.redisPort,
      password: config.redisPassword,
    });
  }
  return client;
}

export async function closeRedis(): Promise<void> {
  if (client) {
    client.close();
    client = null;
  }
}

export type { Redis };

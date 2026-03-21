FROM denoland/deno:1.38.0

WORKDIR /app

# Cache dependencies
COPY deno.json .
RUN deno cache --reload src/main.ts || true

# Copy source
COPY . .

# Cache all dependencies
RUN deno cache src/main.ts

# Run as non-root
USER deno

EXPOSE 8020

CMD ["deno", "run", "--allow-net", "--allow-env", "--allow-read", "src/main.ts"]

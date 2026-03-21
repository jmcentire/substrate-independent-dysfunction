FROM denoland/deno:1.38.0

WORKDIR /app

# Cache dependencies
COPY deno.json .
COPY src/deps.ts src/
RUN deno cache src/deps.ts

# Copy source
COPY . .

# Cache all source files
RUN deno cache src/main.ts

EXPOSE 8007

CMD ["deno", "run", "--allow-net", "--allow-env", "--allow-read", "src/main.ts"]

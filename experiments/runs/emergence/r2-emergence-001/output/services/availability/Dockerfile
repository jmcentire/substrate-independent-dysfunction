FROM denoland/deno:1.38.4

WORKDIR /app

# Cache dependencies
COPY deno.json .
COPY deps.ts .
RUN deno cache deps.ts

# Copy source
COPY . .

# Cache main module
RUN deno cache src/main.ts

# Run
EXPOSE 8001
CMD ["deno", "run", "--allow-net", "--allow-env", "--allow-read", "src/main.ts"]

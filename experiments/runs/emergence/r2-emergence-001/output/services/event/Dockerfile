FROM denoland/deno:1.38.0

WORKDIR /app

# Cache dependencies
COPY deps.ts .
RUN deno cache deps.ts

# Copy source
COPY . .

# Cache main entry point
RUN deno cache src/main.ts

EXPOSE 8004

CMD ["deno", "run", "--allow-net", "--allow-env", "--allow-read", "src/main.ts"]

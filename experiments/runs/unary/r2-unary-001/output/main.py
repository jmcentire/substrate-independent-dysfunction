"""
Wander Backend Services Platform
Main entry point - mounts all 7 services on a single process for development.
In production, each service runs independently.

Services:
  /api/v1/availability  - Availability Service (bitmask cache, O(1) checks)
  /api/v1/pricing       - Pricing Service (rate cache, hash-sharded)
  /api/v1/properties    - Property Service (4-tier override hierarchy)
  /api/v1/events        - Event Service (content-addressable, audit log)
  /api/v1/payments      - Payments Service (Stripe mirror, hash-chained payouts)
  /api/v1/sync          - Sync Service (bidirectional PMS sync)
  /api/v1/bookings      - Booking Service (synchronous orchestrator, <3s flow)
"""
from fastapi import FastAPI
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("wander")

app = FastAPI(
    title="Wander Backend Services",
    description="Vacation rental booking and property management platform",
    version="2.0.0",
)


# Mount services
from services.availability.api import app as availability_app
from services.pricing.api import app as pricing_app
from services.property.api import app as property_app
from services.event.api import app as event_app
from services.payments.api import app as payments_app
from services.sync.api import app as sync_app
from services.booking.api import app as booking_app

app.mount("/api/v1/availability", availability_app)
app.mount("/api/v1/pricing", pricing_app)
app.mount("/api/v1/properties", property_app)
app.mount("/api/v1/events", event_app)
app.mount("/api/v1/payments", payments_app)
app.mount("/api/v1/sync", sync_app)
app.mount("/api/v1/bookings", booking_app)


@app.get("/")
async def root():
    return {
        "platform": "Wander Backend Services",
        "version": "2.0.0",
        "services": {
            "availability": "/api/v1/availability",
            "pricing": "/api/v1/pricing",
            "properties": "/api/v1/properties",
            "events": "/api/v1/events",
            "payments": "/api/v1/payments",
            "sync": "/api/v1/sync",
            "bookings": "/api/v1/bookings",
        },
        "constraint": "User at checkout with credit card. 3 seconds.",
    }


@app.get("/health")
async def health():
    return {"status": "ok", "services": 7}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

"""
Sync Service — PMS Adapters (translation layer).

Each adapter translates between PMS-specific formats and canonical Wander structs.
Adapters are stateless — they only translate data.

Supported PMSs: Guesty, Hostaway, Streamline, Escapia, OwnerRez, Lodgify,
                RentalsUnited, TrackPMS
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from datetime import date


# --- Canonical Structs (frozen dataclasses) ---

@dataclass(frozen=True)
class PropertyCore:
    external_id: str
    name: str
    description: Optional[str] = None
    property_type: str = "HOUSE"
    bedrooms: int = 0
    bathrooms: float = 0
    max_guests: int = 1
    address_line1: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip: Optional[str] = None
    country: str = "US"
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    timezone: str = "America/New_York"
    currency: str = "USD"


@dataclass(frozen=True)
class AvailabilityBlock:
    start_date: str  # YYYY-MM-DD
    end_date: str    # YYYY-MM-DD
    blocked: bool = True


@dataclass(frozen=True)
class NightlyRate:
    date: str  # YYYY-MM-DD
    rate_cents: int
    currency: str = "USD"


@dataclass(frozen=True)
class Fee:
    kind: str       # CLEANING, PET, EXTRA_GUEST, etc.
    label: str
    amount_cents: int
    per: str = "PER_STAY"


@dataclass(frozen=True)
class PmsReservation:
    external_id: str
    property_external_id: str
    check_in: str
    check_out: str
    guest_name: str
    guest_email: Optional[str] = None
    status: str = "CONFIRMED"
    total_cents: Optional[int] = None


@dataclass(frozen=True)
class PmsQuote:
    quote_id: str
    rates: List[NightlyRate] = field(default_factory=list)
    fees: List[Fee] = field(default_factory=list)
    total_cents: int = 0
    expires_at: Optional[str] = None


# --- Base Adapter ---

class BasePmsAdapter:
    """Base class for PMS adapters. Subclasses implement translation."""

    pms_type: str = "unknown"

    def translate_property(self, raw: dict) -> PropertyCore:
        raise NotImplementedError

    def translate_availability(self, raw: dict) -> List[AvailabilityBlock]:
        raise NotImplementedError

    def translate_rates(self, raw: dict) -> List[NightlyRate]:
        raise NotImplementedError

    def translate_reservation(self, raw: dict) -> PmsReservation:
        raise NotImplementedError

    def format_reservation_create(self, check_in: str, check_out: str,
                                   guest: dict, quote_id: str = None) -> dict:
        """Format a reservation creation request for this PMS."""
        raise NotImplementedError


# --- Guesty Adapter ---

class GuestyAdapter(BasePmsAdapter):
    pms_type = "guesty"

    def translate_property(self, raw: dict) -> PropertyCore:
        addr = raw.get("address", {})
        return PropertyCore(
            external_id=raw.get("_id", ""),
            name=raw.get("title", raw.get("nickname", "")),
            description=raw.get("publicDescription", {}).get("summary"),
            property_type=self._map_type(raw.get("type")),
            bedrooms=raw.get("bedrooms", 0),
            bathrooms=raw.get("bathrooms", 0),
            max_guests=raw.get("accommodates", 1),
            address_line1=addr.get("full"),
            city=addr.get("city"),
            state=addr.get("state"),
            zip=addr.get("zipcode"),
            country=addr.get("country", "US"),
            latitude=addr.get("lat"),
            longitude=addr.get("lng"),
            timezone=raw.get("timezone", "America/New_York"),
            currency=raw.get("prices", {}).get("currency", "USD"),
        )

    def translate_availability(self, raw: dict) -> List[AvailabilityBlock]:
        blocks = []
        for cal_entry in raw.get("calendar", []):
            if cal_entry.get("status") in ("blocked", "booked"):
                blocks.append(AvailabilityBlock(
                    start_date=cal_entry["date"],
                    end_date=cal_entry["date"],
                    blocked=True
                ))
        return blocks

    def translate_rates(self, raw: dict) -> List[NightlyRate]:
        rates = []
        for cal_entry in raw.get("calendar", []):
            if "price" in cal_entry:
                rates.append(NightlyRate(
                    date=cal_entry["date"],
                    rate_cents=int(cal_entry["price"] * 100),
                    currency=raw.get("currency", "USD")
                ))
        return rates

    def translate_reservation(self, raw: dict) -> PmsReservation:
        guest = raw.get("guest", {})
        return PmsReservation(
            external_id=raw.get("_id", raw.get("confirmationCode", "")),
            property_external_id=raw.get("listingId", ""),
            check_in=raw.get("checkIn", "")[:10],
            check_out=raw.get("checkOut", "")[:10],
            guest_name=f"{guest.get('firstName', '')} {guest.get('lastName', '')}".strip(),
            guest_email=guest.get("email"),
            status=self._map_status(raw.get("status")),
            total_cents=int(raw.get("money", {}).get("totalPaid", 0) * 100) if raw.get("money") else None
        )

    def format_reservation_create(self, check_in: str, check_out: str,
                                   guest: dict, quote_id: str = None) -> dict:
        return {
            "listingId": guest.get("property_external_id"),
            "checkIn": check_in,
            "checkOut": check_out,
            "guest": {
                "firstName": guest.get("first_name"),
                "lastName": guest.get("last_name"),
                "email": guest.get("email"),
                "phone": guest.get("phone"),
            },
            "source": "wander",
        }

    def _map_type(self, pms_type: str) -> str:
        mapping = {"APARTMENT": "APARTMENT", "HOUSE": "HOUSE", "VILLA": "VILLA"}
        return mapping.get(pms_type, "HOUSE")

    def _map_status(self, status: str) -> str:
        mapping = {"confirmed": "CONFIRMED", "canceled": "CANCELLED", "inquiry": "PENDING"}
        return mapping.get(status, "CONFIRMED")


# --- Hostaway Adapter ---

class HostawayAdapter(BasePmsAdapter):
    pms_type = "hostaway"

    def translate_property(self, raw: dict) -> PropertyCore:
        return PropertyCore(
            external_id=str(raw.get("id", "")),
            name=raw.get("name", ""),
            description=raw.get("description"),
            bedrooms=raw.get("bedroomsNumber", 0),
            bathrooms=raw.get("bathroomsNumber", 0),
            max_guests=raw.get("maxGuestsNumber", 1),
            city=raw.get("city"),
            state=raw.get("state"),
            country=raw.get("countryCode", "US"),
            latitude=raw.get("lat"),
            longitude=raw.get("lng"),
            currency=raw.get("currencyCode", "USD"),
        )

    def translate_availability(self, raw: dict) -> List[AvailabilityBlock]:
        blocks = []
        for entry in raw.get("result", []):
            if entry.get("isAvailable") == 0:
                blocks.append(AvailabilityBlock(
                    start_date=entry["date"],
                    end_date=entry["date"],
                    blocked=True
                ))
        return blocks

    def translate_rates(self, raw: dict) -> List[NightlyRate]:
        rates = []
        for entry in raw.get("result", []):
            if entry.get("price"):
                rates.append(NightlyRate(
                    date=entry["date"],
                    rate_cents=int(float(entry["price"]) * 100),
                ))
        return rates

    def translate_reservation(self, raw: dict) -> PmsReservation:
        return PmsReservation(
            external_id=str(raw.get("id", "")),
            property_external_id=str(raw.get("listingMapId", "")),
            check_in=raw.get("arrivalDate", "")[:10],
            check_out=raw.get("departureDate", "")[:10],
            guest_name=raw.get("guestName", ""),
            guest_email=raw.get("guestEmail"),
            status="CONFIRMED" if raw.get("status") == "new" else raw.get("status", "CONFIRMED").upper(),
            total_cents=int(float(raw.get("totalPrice", 0)) * 100),
        )

    def format_reservation_create(self, check_in: str, check_out: str,
                                   guest: dict, quote_id: str = None) -> dict:
        return {
            "listingMapId": guest.get("property_external_id"),
            "arrivalDate": check_in,
            "departureDate": check_out,
            "guestName": f"{guest.get('first_name', '')} {guest.get('last_name', '')}",
            "guestEmail": guest.get("email"),
            "channelId": 2001,
            "source": "wander",
        }


# --- Streamline Adapter ---

class StreamlineAdapter(BasePmsAdapter):
    pms_type = "streamline"

    def translate_property(self, raw: dict) -> PropertyCore:
        return PropertyCore(
            external_id=str(raw.get("unit_id", "")),
            name=raw.get("name", ""),
            description=raw.get("description"),
            bedrooms=raw.get("bedrooms_number", 0),
            bathrooms=raw.get("bathrooms_number", 0),
            max_guests=raw.get("max_occupancy", 1),
            city=raw.get("city"),
            state=raw.get("state_code"),
            country="US",
            currency="USD",
        )

    def translate_availability(self, raw: dict) -> List[AvailabilityBlock]:
        return [AvailabilityBlock(
            start_date=b["start_date"], end_date=b["end_date"], blocked=True
        ) for b in raw.get("blocked_dates", [])]

    def translate_rates(self, raw: dict) -> List[NightlyRate]:
        return [NightlyRate(
            date=r["date"], rate_cents=int(r["rate"] * 100)
        ) for r in raw.get("rates", [])]

    def translate_reservation(self, raw: dict) -> PmsReservation:
        return PmsReservation(
            external_id=str(raw.get("reservation_id", "")),
            property_external_id=str(raw.get("unit_id", "")),
            check_in=raw.get("checkin_date", "")[:10],
            check_out=raw.get("checkout_date", "")[:10],
            guest_name=raw.get("guest_name", ""),
        )

    def format_reservation_create(self, check_in: str, check_out: str,
                                   guest: dict, quote_id: str = None) -> dict:
        return {
            "unit_id": guest.get("property_external_id"),
            "checkin_date": check_in,
            "checkout_date": check_out,
            "guest_name": f"{guest.get('first_name', '')} {guest.get('last_name', '')}",
            "guest_email": guest.get("email"),
        }


# --- Simplified adapters for remaining PMSes ---

class EscapiaAdapter(StreamlineAdapter):
    pms_type = "escapia"


class OwnerRezAdapter(StreamlineAdapter):
    pms_type = "ownerrez"


class LodgifyAdapter(StreamlineAdapter):
    pms_type = "lodgify"


class RentalsUnitedAdapter(StreamlineAdapter):
    pms_type = "rentals_united"


class TrackPmsAdapter(StreamlineAdapter):
    pms_type = "trackpms"


# --- Adapter Registry ---

ADAPTERS: Dict[str, BasePmsAdapter] = {
    "guesty": GuestyAdapter(),
    "hostaway": HostawayAdapter(),
    "streamline": StreamlineAdapter(),
    "escapia": EscapiaAdapter(),
    "ownerrez": OwnerRezAdapter(),
    "lodgify": LodgifyAdapter(),
    "rentals_united": RentalsUnitedAdapter(),
    "trackpms": TrackPmsAdapter(),
}


def get_adapter(pms_type: str) -> BasePmsAdapter:
    adapter = ADAPTERS.get(pms_type.lower())
    if not adapter:
        raise ValueError(f"Unknown PMS type: {pms_type}")
    return adapter

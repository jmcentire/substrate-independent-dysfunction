"""
Sync Service Adapters
Translation layer: PMS format ↔ canonical structs.
Each PMS adapter translates between external API formats and Wander's internal models.
"""
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional, List, Dict
import hashlib
import uuid


# --- Canonical Structs (frozen dataclasses) ---

@dataclass(frozen=True)
class PropertyCore:
    external_id: str
    pms_type: str
    name: str
    description: str = ""
    property_type: str = "HOUSE"
    bedrooms: int = 0
    bathrooms: float = 0
    max_guests: int = 1
    beds: int = 0


@dataclass(frozen=True)
class PropertyLocation:
    address_line1: str = ""
    city: str = ""
    state: str = ""
    country: str = "US"
    postal_code: str = ""
    latitude: float = 0
    longitude: float = 0


@dataclass(frozen=True)
class AvailabilityBlock:
    start_date: date
    end_date: date
    blocked: bool = True


@dataclass(frozen=True)
class NightlyRate:
    date: date
    rate_cents: int
    currency: str = "USD"


@dataclass(frozen=True)
class Fee:
    code: str
    label: str
    amount_cents: int
    per: str = "PER_STAY"  # PER_STAY, PER_NIGHT, PER_GUEST


@dataclass(frozen=True)
class Reservation:
    external_id: str
    property_external_id: str
    pms_type: str
    check_in: date
    check_out: date
    guest_name: str = ""
    guest_email: str = ""
    status: str = "CONFIRMED"
    confirmation_code: str = ""
    total_cents: int = 0
    currency: str = "USD"


# --- Base Adapter Interface ---

class PMSAdapter:
    """Base adapter interface. Each PMS adapter implements these methods."""
    
    pms_type: str = "unknown"
    
    def translate_property(self, raw_data: dict) -> PropertyCore:
        raise NotImplementedError
    
    def translate_location(self, raw_data: dict) -> PropertyLocation:
        raise NotImplementedError
    
    def translate_availability(self, raw_data: dict) -> List[AvailabilityBlock]:
        raise NotImplementedError
    
    def translate_rates(self, raw_data: dict) -> List[NightlyRate]:
        raise NotImplementedError
    
    def translate_fees(self, raw_data: dict) -> List[Fee]:
        raise NotImplementedError
    
    def translate_reservation(self, raw_data: dict) -> Reservation:
        raise NotImplementedError
    
    def format_reservation_request(self, check_in: date, check_out: date,
                                    guest_name: str, guest_email: str,
                                    guests: int) -> dict:
        """Format a reservation creation request for the PMS API."""
        raise NotImplementedError


# --- Guesty Adapter ---

class GuestyAdapter(PMSAdapter):
    pms_type = "guesty"
    
    def translate_property(self, raw_data: dict) -> PropertyCore:
        return PropertyCore(
            external_id=raw_data.get("_id", ""),
            pms_type=self.pms_type,
            name=raw_data.get("title", ""),
            description=raw_data.get("publicDescription", {}).get("summary", ""),
            property_type=raw_data.get("type", "HOUSE").upper(),
            bedrooms=raw_data.get("bedrooms", 0),
            bathrooms=raw_data.get("bathrooms", 0),
            max_guests=raw_data.get("accommodates", 1),
            beds=raw_data.get("beds", 0),
        )
    
    def translate_availability(self, raw_data: dict) -> List[AvailabilityBlock]:
        blocks = []
        for block in raw_data.get("calendar", []):
            if block.get("status") in ("booked", "blocked"):
                blocks.append(AvailabilityBlock(
                    start_date=date.fromisoformat(block["startDate"][:10]),
                    end_date=date.fromisoformat(block["endDate"][:10]),
                    blocked=True,
                ))
        return blocks
    
    def translate_rates(self, raw_data: dict) -> List[NightlyRate]:
        rates = []
        for day in raw_data.get("calendar", []):
            if "price" in day:
                rates.append(NightlyRate(
                    date=date.fromisoformat(day["date"][:10]),
                    rate_cents=int(day["price"] * 100),
                    currency=raw_data.get("currency", "USD"),
                ))
        return rates
    
    def translate_reservation(self, raw_data: dict) -> Reservation:
        guest = raw_data.get("guest", {})
        return Reservation(
            external_id=raw_data.get("_id", ""),
            property_external_id=raw_data.get("listingId", ""),
            pms_type=self.pms_type,
            check_in=date.fromisoformat(raw_data["checkIn"][:10]),
            check_out=date.fromisoformat(raw_data["checkOut"][:10]),
            guest_name=f"{guest.get('firstName', '')} {guest.get('lastName', '')}".strip(),
            guest_email=guest.get("email", ""),
            status=_map_guesty_status(raw_data.get("status", "")),
            confirmation_code=raw_data.get("confirmationCode", ""),
            total_cents=int(raw_data.get("money", {}).get("totalPaid", 0) * 100),
            currency=raw_data.get("money", {}).get("currency", "USD"),
        )
    
    def format_reservation_request(self, check_in, check_out, guest_name, guest_email, guests):
        names = guest_name.split(" ", 1)
        return {
            "checkIn": check_in.isoformat(),
            "checkOut": check_out.isoformat(),
            "guest": {
                "firstName": names[0],
                "lastName": names[1] if len(names) > 1 else "",
                "email": guest_email,
            },
            "guestsCount": guests,
        }


def _map_guesty_status(status: str) -> str:
    mapping = {
        "confirmed": "CONFIRMED",
        "checked_in": "CONFIRMED",
        "checked_out": "COMPLETED",
        "canceled": "CANCELLED",
        "cancelled": "CANCELLED",
    }
    return mapping.get(status.lower(), "CONFIRMED")


# --- Hostaway Adapter ---

class HostawayAdapter(PMSAdapter):
    pms_type = "hostaway"
    
    def translate_property(self, raw_data: dict) -> PropertyCore:
        return PropertyCore(
            external_id=str(raw_data.get("id", "")),
            pms_type=self.pms_type,
            name=raw_data.get("name", ""),
            description=raw_data.get("description", ""),
            property_type=raw_data.get("propertyTypeId", "HOUSE"),
            bedrooms=raw_data.get("bedroomsNumber", 0),
            bathrooms=raw_data.get("bathroomsNumber", 0),
            max_guests=raw_data.get("personCapacity", 1),
        )
    
    def translate_availability(self, raw_data: dict) -> List[AvailabilityBlock]:
        blocks = []
        for item in raw_data.get("result", []):
            if item.get("isAvailable") == 0:
                blocks.append(AvailabilityBlock(
                    start_date=date.fromisoformat(item["date"]),
                    end_date=date.fromisoformat(item["date"]),
                    blocked=True,
                ))
        return blocks
    
    def translate_rates(self, raw_data: dict) -> List[NightlyRate]:
        rates = []
        for item in raw_data.get("result", []):
            if "price" in item:
                rates.append(NightlyRate(
                    date=date.fromisoformat(item["date"]),
                    rate_cents=int(float(item["price"]) * 100),
                ))
        return rates
    
    def translate_reservation(self, raw_data: dict) -> Reservation:
        return Reservation(
            external_id=str(raw_data.get("id", "")),
            property_external_id=str(raw_data.get("listingMapId", "")),
            pms_type=self.pms_type,
            check_in=date.fromisoformat(raw_data["arrivalDate"][:10]),
            check_out=date.fromisoformat(raw_data["departureDate"][:10]),
            guest_name=raw_data.get("guestName", ""),
            guest_email=raw_data.get("guestEmail", ""),
            status="CONFIRMED" if raw_data.get("status") == "new" else raw_data.get("status", "CONFIRMED").upper(),
            total_cents=int(float(raw_data.get("totalPrice", 0)) * 100),
        )
    
    def format_reservation_request(self, check_in, check_out, guest_name, guest_email, guests):
        return {
            "arrivalDate": check_in.isoformat(),
            "departureDate": check_out.isoformat(),
            "guestName": guest_name,
            "guestEmail": guest_email,
            "numberOfGuests": guests,
        }


# --- Streamline Adapter ---

class StreamlineAdapter(PMSAdapter):
    pms_type = "streamline"
    
    def translate_property(self, raw_data: dict) -> PropertyCore:
        return PropertyCore(
            external_id=str(raw_data.get("unit_id", "")),
            pms_type=self.pms_type,
            name=raw_data.get("unit_name", ""),
            description=raw_data.get("description", ""),
            bedrooms=raw_data.get("bedrooms_number", 0),
            bathrooms=raw_data.get("bathrooms_number", 0),
            max_guests=raw_data.get("max_occupancy", 1),
        )
    
    def translate_rates(self, raw_data: dict) -> List[NightlyRate]:
        rates = []
        for item in raw_data.get("rates", []):
            rates.append(NightlyRate(
                date=date.fromisoformat(item["date"]),
                rate_cents=int(float(item["rate"]) * 100),
            ))
        return rates
    
    def translate_availability(self, raw_data: dict) -> List[AvailabilityBlock]:
        blocks = []
        for item in raw_data.get("blocked_dates", []):
            blocks.append(AvailabilityBlock(
                start_date=date.fromisoformat(item["from"]),
                end_date=date.fromisoformat(item["to"]),
                blocked=True,
            ))
        return blocks
    
    def format_reservation_request(self, check_in, check_out, guest_name, guest_email, guests):
        return {
            "checkin_date": check_in.isoformat(),
            "checkout_date": check_out.isoformat(),
            "guest_name": guest_name,
            "guest_email": guest_email,
            "occupants": guests,
        }


# --- Adapter Registry ---

ADAPTER_REGISTRY: Dict[str, PMSAdapter] = {
    "guesty": GuestyAdapter(),
    "hostaway": HostawayAdapter(),
    "streamline": StreamlineAdapter(),
    # Additional adapters: escapia, ownerrez, lodgify, rentalsunited, trackpms
}


def get_adapter(pms_type: str) -> PMSAdapter:
    """Get the adapter for a PMS type."""
    adapter = ADAPTER_REGISTRY.get(pms_type.lower())
    if not adapter:
        raise ValueError(f"Unknown PMS type: {pms_type}. Available: {list(ADAPTER_REGISTRY.keys())}")
    return adapter

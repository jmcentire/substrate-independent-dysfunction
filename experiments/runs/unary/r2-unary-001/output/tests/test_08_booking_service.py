"""
Tests for Booking Service — the core orchestrator.
Verifies UBR generation, booking lifecycle, policy enforcement, idempotency.
"""
import pytest
from services.booking.ubr import generate_ubr, validate_ubr


class TestUBRGeneration:
    """Test Universal Booking Reference generation."""
    
    def test_generate_ubr_format(self):
        ubr = generate_ubr("prop_123", "2025-06-15", "2025-06-20", "guest_456")
        assert ubr.startswith("UBR-")
        assert len(ubr) > 20
    
    def test_ubr_uniqueness(self):
        """Different calls generate different UBRs (due to nonce)."""
        ubr1 = generate_ubr("prop_123", "2025-06-15", "2025-06-20", "guest_456")
        ubr2 = generate_ubr("prop_123", "2025-06-15", "2025-06-20", "guest_456")
        assert ubr1 != ubr2  # Nonce ensures uniqueness
    
    def test_validate_ubr(self):
        ubr = generate_ubr("prop_123", "2025-06-15", "2025-06-20", "guest_456")
        assert validate_ubr(ubr) == True
    
    def test_validate_invalid_ubr(self):
        assert validate_ubr("NOT-A-UBR") == False
        assert validate_ubr("UBR-") == False
        assert validate_ubr("") == False


class TestBookingPolicy:
    """Test policy enforcement (Booking Service owns all policy)."""
    
    def test_policy_rejects_too_many_guests(self):
        from services.booking.api import _validate_booking_policy
        from datetime import date
        
        error = _validate_booking_policy(
            {"is_bookable": True, "max_guests": 4},
            date(2025, 6, 15), date(2025, 6, 20),
            total_guests=6,
        )
        assert error is not None
        assert "Maximum 4 guests" in error
    
    def test_policy_rejects_non_bookable(self):
        from services.booking.api import _validate_booking_policy
        from datetime import date
        
        error = _validate_booking_policy(
            {"is_bookable": False, "max_guests": 10},
            date(2025, 6, 15), date(2025, 6, 20),
            total_guests=2,
        )
        assert error is not None
        assert "not accepting bookings" in error
    
    def test_policy_passes_valid_booking(self):
        from services.booking.api import _validate_booking_policy
        from datetime import date
        
        error = _validate_booking_policy(
            {"is_bookable": True, "max_guests": 8},
            date(2025, 6, 15), date(2025, 6, 20),
            total_guests=4,
        )
        assert error is None
    
    def test_policy_rejects_zero_nights(self):
        from services.booking.api import _validate_booking_policy
        from datetime import date
        
        error = _validate_booking_policy(
            {"is_bookable": True, "max_guests": 8},
            date(2025, 6, 15), date(2025, 6, 15),  # Same day = 0 nights
            total_guests=2,
        )
        assert error is not None

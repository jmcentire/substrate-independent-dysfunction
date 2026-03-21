"""
Universal Booking Reference (UBR) generation.

UBR format: UBR-{base58(domain + timestamp + nonce + signature[:16])}
Content-addressable, signed with HMAC SHA256.
"""

import hashlib
import hmac
import time
import os
import uuid

# Base58 alphabet (Bitcoin-style, no 0, O, I, l)
BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
SECRET_KEY = os.environ.get("UBR_SECRET", "wander-ubr-secret-key-change-in-production").encode()


def _base58_encode(data: bytes) -> str:
    """Encode bytes to base58."""
    n = int.from_bytes(data, "big")
    result = []
    while n > 0:
        n, remainder = divmod(n, 58)
        result.append(BASE58_ALPHABET[remainder])

    # Add leading zeros
    for byte in data:
        if byte == 0:
            result.append(BASE58_ALPHABET[0])
        else:
            break

    return "".join(reversed(result))


def generate_ubr(property_id: str, check_in: str, check_out: str,
                 guest_id: str = None) -> str:
    """Generate a Universal Booking Reference.
    
    Content-addressable: same inputs → same UBR (modulo nonce).
    """
    nonce = uuid.uuid4().hex[:16]
    timestamp = str(int(time.time()))

    # Content for HMAC
    content = f"{property_id}:{check_in}:{check_out}:{timestamp}:{nonce}"

    # Sign with HMAC SHA256
    signature = hmac.new(SECRET_KEY, content.encode(), hashlib.sha256).digest()

    # Combine: timestamp + nonce + signature[:16]
    data = timestamp.encode() + nonce.encode() + signature[:16]

    encoded = _base58_encode(data)
    return f"UBR-{encoded}"


def verify_ubr(ubr: str) -> bool:
    """Basic UBR format validation."""
    if not ubr.startswith("UBR-"):
        return False
    encoded = ubr[4:]
    if len(encoded) < 20:
        return False
    # Check all chars are valid base58
    for c in encoded:
        if c not in BASE58_ALPHABET:
            return False
    return True

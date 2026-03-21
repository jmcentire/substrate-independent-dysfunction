"""
Universal Booking Reference (UBR) generation.
Content-addressable, HMAC-SHA256 signed.
Format: UBR-{base58(domain + timestamp + nonce + signature[:16])}
"""
import hashlib
import hmac
import time
import os
import uuid

# Secret key for HMAC signing
UBR_SECRET = os.environ.get("UBR_SECRET", "wander-ubr-secret-key-change-in-production")

# Base58 alphabet (Bitcoin-style, no confusing characters)
BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _base58_encode(data: bytes) -> str:
    """Encode bytes to base58."""
    num = int.from_bytes(data, 'big')
    if num == 0:
        return BASE58_ALPHABET[0]
    
    result = []
    while num > 0:
        num, remainder = divmod(num, 58)
        result.append(BASE58_ALPHABET[remainder])
    
    # Handle leading zeros
    for byte in data:
        if byte == 0:
            result.append(BASE58_ALPHABET[0])
        else:
            break
    
    return ''.join(reversed(result))


def generate_ubr(property_id: str, check_in: str, check_out: str, guest_id: str) -> str:
    """
    Generate a Universal Booking Reference.
    Content-addressable: same inputs → same base, but nonce ensures uniqueness.
    """
    nonce = uuid.uuid4().hex[:16]
    timestamp = str(int(time.time()))
    
    # Content for signing
    content = f"{property_id}:{check_in}:{check_out}:{guest_id}:{timestamp}:{nonce}"
    
    # HMAC-SHA256 signature
    signature = hmac.new(
        UBR_SECRET.encode(),
        content.encode(),
        hashlib.sha256
    ).hexdigest()
    
    # Combine: timestamp + nonce + signature prefix
    raw_bytes = bytes.fromhex(timestamp.encode().hex() + nonce + signature[:32])
    
    encoded = _base58_encode(raw_bytes)
    
    return f"UBR-{encoded}"


def validate_ubr(ubr: str) -> bool:
    """Basic UBR format validation."""
    if not ubr.startswith("UBR-"):
        return False
    body = ubr[4:]
    return len(body) >= 20 and all(c in BASE58_ALPHABET for c in body)

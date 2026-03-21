"""
Cryptographic primitives for HermesP2P:
- Onion routing with constant-size packets
- Channel encryption (public, private, direct)

Onion Routing Design (Sphinx-inspired):
=======================================
Each packet is exactly PACKET_SIZE bytes.

Structure: [32 bytes: ephemeral_pubkey] + [BODY_SIZE bytes: encrypted_body]

Each relay:
1. Extracts ephemeral_pubkey from packet
2. Computes shared_secret = ECDH(relay_privkey, ephemeral_pubkey) 
3. Derives: stream_key, header_key from shared_secret
4. Decrypts the HEADER portion (first HEADER_AREA bytes of body) with header_key
5. Reads: [1 byte is_final][32 bytes next_hop/zeros]
6. Shifts the body left by 33 bytes (removing consumed header)
7. Appends 33 bytes of random padding at the end
8. Derives new ephemeral key for next hop
9. Forwards [new_ephemeral (32 bytes)] + [shifted body] = PACKET_SIZE bytes

The key insight: we DON'T nest encryption. Instead, we use stream encryption 
with per-hop keys applied as layers. The sender pre-encrypts the body such that
when each relay XOR-decrypts their layer, the next layer's header becomes readable.

This is how Sphinx works and guarantees constant size.
"""

import os
import struct
import hashlib
from typing import List, Tuple, Optional

from nacl.public import PrivateKey, PublicKey, Box, SealedBox
from nacl.signing import SigningKey, VerifyKey
from nacl.secret import SecretBox
from nacl.utils import random as nacl_random
from nacl.exceptions import CryptoError


# --- Constants ---
EPHEMERAL_SIZE = 32
HOP_HEADER_SIZE = 33  # 1 byte is_final + 32 bytes next_hop_key
MAX_HOPS = 5
# Body must hold MAX_HOPS * HOP_HEADER_SIZE + payload
# Body is large enough for headers of all hops + max payload
MAX_PAYLOAD_SIZE = 4096
BODY_SIZE = MAX_HOPS * HOP_HEADER_SIZE + MAX_PAYLOAD_SIZE  # 165 + 4096 = 4261
PACKET_SIZE = EPHEMERAL_SIZE + BODY_SIZE  # 32 + 4261 = 4293


def _ecdh(private_key: PrivateKey, public_key: PublicKey) -> bytes:
    """Compute shared secret using NaCl Box."""
    box = Box(private_key, public_key)
    return box.shared_key()


def _derive_stream_key(shared_secret: bytes, label: bytes = b"stream") -> bytes:
    """Derive a stream cipher key from shared secret."""
    return hashlib.sha256(b"hermes-" + label + b"-" + shared_secret).digest()


def _derive_blinding(shared_secret: bytes) -> bytes:
    """Derive a blinding factor for ephemeral key transformation."""
    return hashlib.sha256(b"hermes-blind-" + shared_secret).digest()


def _stream_cipher(data: bytes, key: bytes) -> bytes:
    """
    XOR-based stream cipher using SHA256 in counter mode.
    Produces a keystream of len(data) bytes and XORs.
    """
    result = bytearray(len(data))
    block_size = 32
    for i in range(0, len(data), block_size):
        counter = struct.pack(">I", i // block_size)
        keystream = hashlib.sha256(key + counter).digest()
        chunk_len = min(block_size, len(data) - i)
        for j in range(chunk_len):
            result[i + j] = data[i + j] ^ keystream[j]
    return bytes(result)


def _scalar_mult_pubkey(pubkey: PublicKey, scalar_bytes: bytes) -> PublicKey:
    """
    'Blind' a public key by computing a new ephemeral.
    
    For simplicity, we pre-compute per-hop ephemerals at the sender
    and embed them. This avoids the complex EC scalar multiplication
    that Sphinx requires (which NaCl doesn't directly expose for Curve25519 points).
    
    We'll use a different approach: each layer's encrypted header also contains
    the ephemeral public key for the next hop.
    """
    pass  # Not used; see build_onion for our approach


class OnionRouter:
    """
    Constructs and processes onion-routed packets.
    
    Approach: Per-hop stream encryption with embedded ephemeral keys.
    
    Packet structure: [32 bytes: ephemeral_pubkey] + [BODY_SIZE bytes: body]
    
    Building (inside-out):
    - Start with body = [final_header] + [payload] + [padding]
    - For each relay from innermost to outermost:
      - Encrypt entire body with relay's stream key
      - Embed next_hop info by prepending header and removing from tail
      - But we need the header to be readable after the relay decrypts...
    
    Actually, let's use the straightforward approach:
    
    Building (outside-in conceptually):
    
    The body contains up to MAX_HOPS headers (each 33 bytes) followed by payload+padding.
    Headers are layered-encrypted: the outermost relay's header is encrypted only with 
    that relay's key. The second relay's header is encrypted with both relay1 and relay2 keys.
    
    Specifically, the sender:
    1. Generates N ephemeral keypairs (one per hop including recipient)
    2. Computes shared secrets with each hop
    3. Derives stream keys for each hop
    4. Constructs the raw body:
       [header_0][header_1]...[header_{n-1}][payload][random_padding]
       where header_i contains routing info for hop i
    5. Applies stream encryption in reverse order:
       - Encrypt everything from header_n-1 onward with key_n-1
       - Encrypt everything from header_n-2 onward with key_n-2
       - ... 
       - Encrypt everything from header_0 onward with key_0
       
    This way, when hop_0 decrypts with key_0, header_0 becomes plaintext
    but headers 1..n-1 and payload remain encrypted under their respective keys.
    
    After hop_0 decrypts and reads header_0 (33 bytes), it shifts the body left
    by 33 bytes (removing header_0, appending 33 bytes of random padding at end),
    and forwards with the next ephemeral key.
    
    When hop_1 decrypts, header_1 is now at the start and becomes readable
    (because hop_0 already removed its encryption layer over header_1's area,
    and hop_1 removes the remaining layer).
    
    Wait, this doesn't quite work because each layer encrypts different ranges.
    
    Let me simplify: each layer encrypts the ENTIRE body. When hop_0 decrypts
    the entire body, all bytes are decrypted with key_0. Then the header at the
    start is readable. But headers 1..n-1 are still encrypted under keys 1..n-1.
    The payload is encrypted under ALL keys.
    
    So: 
    - Sender encrypts body with key_{n-1}, then key_{n-2}, ..., then key_0.
    - Hop_0 decrypts with key_0 -> reveals header_0, rest still encrypted.
    - Shifts left by 33, pads end with 33 random bytes.
    - Hop_1 decrypts with key_1 -> reveals header_1.
    - etc.
    - Final hop decrypts with key_{n-1} -> reveals header_{n-1} (is_final) + payload.
    
    But there's a problem: how does hop_1 know key_1? It uses its own private key
    with the ephemeral public key. After hop_0 shifts, the ephemeral key needs to
    change. We embed the next ephemeral in the header.
    
    Revised header: [1 byte: is_final][32 bytes: next_hop_pubkey][32 bytes: next_ephemeral]
    = 65 bytes per hop
    
    After decrypting, hop reads 65 bytes, shifts body left by 65, pads end.
    Forwards with next_ephemeral + shifted body.
    
    But the payload area shrinks by 65 bytes per hop in the encrypted construction...
    No, the body size is fixed. We just lose 65 bytes of padding space per hop.
    Since we have MAX_HOPS * 65 bytes of header space pre-allocated, the payload
    area is BODY_SIZE - MAX_HOPS * 65 bytes.
    
    Let me implement this correctly.
    """

    # Per-hop header: [1 byte is_final] [32 bytes next_hop] [32 bytes next_ephemeral]
    HOP_HEADER = 65

    @staticmethod
    def build_onion(
        payload: bytes,
        route: List[PublicKey],
        recipient_pubkey: PublicKey,
    ) -> bytes:
        """
        Build a layered onion packet.
        
        Args:
            payload: The encrypted payload for the recipient.
            route: List of relay public keys [R1, R2, ..., Rn].
            recipient_pubkey: The final recipient's public key.
        
        Returns:
            PACKET_SIZE-byte onion packet.
        """
        all_hops = list(route) + [recipient_pubkey]
        n_hops = len(all_hops)
        
        assert n_hops <= MAX_HOPS, f"Too many hops: {n_hops} > {MAX_HOPS}"
        
        # Generate ephemeral keypairs for each hop
        eph_privs = [PrivateKey.generate() for _ in range(n_hops)]
        eph_pubs = [k.public_key for k in eph_privs]
        
        # Compute shared secrets and derive stream keys
        shared_secrets = []
        stream_keys = []
        for i in range(n_hops):
            ss = _ecdh(eph_privs[i], all_hops[i])
            shared_secrets.append(ss)
            stream_keys.append(_derive_stream_key(ss))
        
        # Construct the plaintext body (before any encryption):
        # [header_0][header_1]...[header_{n-1}][payload][padding]
        # 
        # header_i for relay i (not final): [0][next_hop_pubkey][next_ephemeral_pubkey]
        # header_{n-1} for recipient (final): [1][zeros_32][zeros_32]
        
        HH = OnionRouter.HOP_HEADER
        body = bytearray(BODY_SIZE)
        
        for i in range(n_hops):
            offset = i * HH
            if i == n_hops - 1:
                # Final hop (recipient)
                body[offset] = 1  # is_final
                body[offset+1:offset+33] = bytes(32)  # no next hop
                body[offset+33:offset+65] = bytes(32)  # no next ephemeral
            else:
                # Relay
                body[offset] = 0  # not final
                body[offset+1:offset+33] = bytes(all_hops[i+1])  # next hop pubkey
                body[offset+33:offset+65] = bytes(eph_pubs[i+1])  # next ephemeral
        
        # Place payload after all headers
        payload_offset = n_hops * HH
        payload_end = payload_offset + len(payload)
        if payload_end > BODY_SIZE:
            raise ValueError(f"Payload too large: {len(payload)} bytes")
        body[payload_offset:payload_end] = payload
        
        # Fill remaining with random padding
        if payload_end < BODY_SIZE:
            body[payload_end:] = os.urandom(BODY_SIZE - payload_end)
        
        # Now apply stream encryption in reverse order (innermost key first):
        # Encrypt with key_{n-1}, then key_{n-2}, ..., then key_0
        # 
        # But we need to encrypt selectively: when hop_i decrypts, only header_i
        # should become readable. The trick: encrypt from position i*HH onward
        # with key_i (in reverse order).
        #
        # Actually, the simpler approach that works: encrypt the ENTIRE body
        # with each key in reverse. When hop_0 decrypts the entire body with key_0,
        # header_0 becomes readable (it was encrypted last with key_0).
        # But headers 1..n-1 are still encrypted under keys 1..n-1.
        #
        # Wait: if we encrypt entire body with key_{n-1} then key_{n-2} etc,
        # then decrypting with key_0 removes only the key_0 layer from the
        # entire body. Header_0 was only encrypted with key_0 (it was placed
        # last in the encryption chain), so yes, it becomes plaintext.
        # Header_1 was encrypted with key_1 then key_0. After removing key_0,
        # it's still encrypted with key_1. ✓
        # 
        # BUT: after hop_0 shifts the body left by HH bytes, the positions change.
        # When hop_1 decrypts, it decrypts the shifted body. The stream cipher
        # is position-dependent, so we need to account for the shift.
        #
        # Solution: Don't shift. Instead, each hop decrypts only its portion.
        #
        # Better solution: Each hop decrypts the ENTIRE remaining body.
        # After hop_0 removes its header (shifts), the remaining body starts
        # at what was position HH. We encrypt such that:
        # - body[0:HH] is encrypted with only key_0
        # - body[HH:2*HH] is encrypted with key_0 (full body) and key_1 (from 0 onward after shift)
        #
        # This is getting complex. Let me use the simplest correct approach:
        #
        # Each relay decrypts the FULL body with its stream key.
        # Then reads the FIRST HH bytes as its header.
        # Then REMOVES those HH bytes (shifts everything left), pads HH random bytes at end.
        # The next relay does the same.
        #
        # For this to work, we need encryption to be position-independent,
        # or we need to account for shifts in our encryption.
        #
        # Position-independent approach: use ECB-like mode where each block 
        # is independent. OR just use CTR mode where we adjust the counter
        # based on the hop number.
        #
        # Simplest: each hop uses its stream key to XOR the entire body it receives.
        # The sender must pre-apply all layers accounting for the shifts.
        
        # Let me use a clean matrix-based approach:
        # After hop 0 decrypts and shifts:
        #   what was at position [HH:] moves to position [0:]
        #   and HH random bytes are appended
        # After hop 1 decrypts and shifts similarly.
        
        # So position p in the body seen by hop i was originally at position p + i*HH.
        
        # For hop i, applying stream_cipher with key_i transforms byte at position p 
        # using keystream at position p.
        
        # We need: after hop 0 decrypts, bytes at positions [0:HH] are the plaintext header_0.
        # After shift, bytes at positions [0:BODY_SIZE-HH] are the remaining encrypted body.
        # Then hop 1 decrypts, and bytes at [0:HH] become plaintext header_1.
        
        # Let's define decrypt_i(body, key_i) = body XOR keystream(key_i).
        # And shift(body) = body[HH:] + random_padding(HH).
        
        # What hop 0 does: body_0' = decrypt_0(body_0, key_0); then body_1 = shift(body_0')
        # What hop 1 does: body_1' = decrypt_1(body_1, key_1); then body_2 = shift(body_1')
        
        # We want body_0'[0:HH] = header_0 (plaintext)
        # We want body_1'[0:HH] = header_1 (plaintext)
        
        # body_1 = shift(body_0') = body_0'[HH:] + padding
        # body_1'= decrypt_1(body_1, key_1) = (body_0'[HH:] + padding) XOR keystream_1
        
        # For body_1'[0:HH] = header_1:
        # body_0'[HH:2*HH] XOR keystream_1[0:HH] = header_1
        # body_0'[HH:2*HH] = header_1 XOR keystream_1[0:HH]
        
        # And body_0' = decrypt_0(body_0, key_0) = body_0 XOR keystream_0
        # So body_0[HH:2*HH] XOR keystream_0[HH:2*HH] = header_1 XOR keystream_1[0:HH]
        # body_0[HH:2*HH] = header_1 XOR keystream_1[0:HH] XOR keystream_0[HH:2*HH]
        
        # In general, the byte at original position (i*HH + j) needs to be:
        # For header_i[j]:
        #   It needs to be encrypted with: keystream_0[i*HH+j] XOR keystream_1[(i-1)*HH+j] XOR ... XOR keystream_i[j]
        # Wait, let me think again more carefully.
        #
        # For payload bytes (at original position n_hops*HH + p):
        #   After all n_hops decryptions and shifts, position p should contain payload[p].
        
        # Let me implement this directly.
        # 
        # Build the body from inside out:
        # Start with what the last hop (recipient) should see after decryption.
        # The recipient sees: [header_{n-1}][payload][padding]
        # Before decryption, the body the recipient receives is:
        #   encrypt_{n-1}([header_{n-1}][payload][padding])
        # 
        # But the recipient's body comes after n-1 shifts. Let's build it.
        
        # What the recipient (hop n-1) receives:
        body_for_recipient = bytearray(BODY_SIZE)
        # After decryption, it should be: [header_{n-1}][payload][padding]
        hop_n = n_hops - 1
        # header for final hop
        final_header = bytearray(HH)
        final_header[0] = 1  # is_final
        # [1:33] and [33:65] are zeros (no next hop, no next ephemeral)
        
        body_for_recipient[0:HH] = final_header
        body_for_recipient[HH:HH+len(payload)] = payload
        if HH + len(payload) < BODY_SIZE:
            body_for_recipient[HH+len(payload):] = os.urandom(BODY_SIZE - HH - len(payload))
        
        # Before decryption by recipient:
        current_body = bytes(_stream_cipher(bytes(body_for_recipient), stream_keys[hop_n]))
        
        # Now wrap for each previous hop, from hop n-2 down to 0
        for i in range(n_hops - 2, -1, -1):
            # What hop i will see after decryption:
            # [header_i (HH bytes)] [current_body[:BODY_SIZE-HH]]
            # (the rightmost HH bytes of current_body are lost due to shift,
            #  but they were padding anyway)
            
            header_i = bytearray(HH)
            header_i[0] = 0  # not final
            header_i[1:33] = bytes(all_hops[i+1])  # next hop pubkey
            header_i[33:65] = bytes(eph_pubs[i+1])  # next ephemeral pubkey
            
            # After decryption, hop i sees:
            # [header_i][body_for_next_hop]
            # Then hop i shifts: removes header_i, appends HH random padding
            # -> [body_for_next_hop] + [random HH bytes]
            # But we want the next hop to receive current_body as its input.
            # So: body_for_next_hop = current_body[:BODY_SIZE - HH]
            # And after shift: [current_body[:BODY_SIZE-HH]] + [HH random padding]
            # The next hop will only use [0:BODY_SIZE] of this, which is the full thing.
            # And the next hop expects to find its encrypted body to decrypt.
            # current_body is already the encrypted body for the next hop.
            
            plaintext_for_hop_i = bytearray(BODY_SIZE)
            plaintext_for_hop_i[0:HH] = header_i
            plaintext_for_hop_i[HH:] = current_body[:BODY_SIZE - HH]
            
            # Encrypt for hop i
            current_body = bytes(_stream_cipher(bytes(plaintext_for_hop_i), stream_keys[i]))
        
        # Build final packet: [eph_pubs[0]] + [current_body]
        packet = bytes(eph_pubs[0]) + current_body
        assert len(packet) == PACKET_SIZE, f"{len(packet)} != {PACKET_SIZE}"
        return packet

    @staticmethod
    def peel_layer(
        packet: bytes,
        private_key: PrivateKey,
    ) -> Tuple[bool, Optional[bytes], bytes]:
        """
        Decrypt one onion layer.
        
        Returns:
            (is_final, next_hop_pubkey_bytes, output)
            - is_final: True if this is the final destination.
            - next_hop_pubkey_bytes: 32-byte key of next hop (None if final).
            - output: For final hop: the decrypted body (payload with padding).
                      For relay: the PACKET_SIZE-byte packet to forward to next hop.
        """
        assert len(packet) == PACKET_SIZE, f"Expected {PACKET_SIZE}, got {len(packet)}"
        
        HH = OnionRouter.HOP_HEADER
        
        # Extract ephemeral key and body
        eph_bytes = packet[:EPHEMERAL_SIZE]
        encrypted_body = packet[EPHEMERAL_SIZE:]
        
        eph_pubkey = PublicKey(eph_bytes)
        
        # Compute shared secret and stream key
        ss = _ecdh(private_key, eph_pubkey)
        skey = _derive_stream_key(ss)
        
        # Decrypt body
        body = _stream_cipher(encrypted_body, skey)
        
        # Read header
        is_final = body[0] == 1
        next_hop_bytes = body[1:33]
        next_eph_bytes = body[33:65]
        
        if is_final:
            # Return the remaining body (payload + padding)
            return True, None, body[HH:]
        else:
            # Shift body: remove header, append random padding
            shifted = body[HH:] + os.urandom(HH)
            # Construct next packet
            next_packet = next_eph_bytes + shifted
            assert len(next_packet) == PACKET_SIZE
            return False, next_hop_bytes, next_packet


class ChannelCrypto:
    """
    Handles encryption/decryption for the three channel types.
    """

    @staticmethod
    def sign_public_message(
        message: bytes,
        signing_key: SigningKey,
    ) -> bytes:
        """
        Sign a message for a public channel.
        Returns: signature (64 bytes) + message
        """
        signed = signing_key.sign(message)
        return signed.signature + message

    @staticmethod
    def verify_public_message(
        signed_message: bytes,
        verify_key: VerifyKey,
    ) -> Optional[bytes]:
        """
        Verify and extract a public channel message.
        Returns the message if valid, None otherwise.
        """
        if len(signed_message) < 64:
            return None
        signature = signed_message[:64]
        message = signed_message[64:]
        try:
            verify_key.verify(message, signature)
            return message
        except Exception:
            return None

    @staticmethod
    def encrypt_private_channel(
        message: bytes,
        channel_key: bytes,
    ) -> bytes:
        """Encrypt a message for a private channel using symmetric key."""
        box = SecretBox(channel_key)
        return box.encrypt(message)

    @staticmethod
    def decrypt_private_channel(
        encrypted: bytes,
        channel_key: bytes,
    ) -> Optional[bytes]:
        """Decrypt a private channel message."""
        box = SecretBox(channel_key)
        try:
            return box.decrypt(encrypted)
        except CryptoError:
            return None

    @staticmethod
    def encrypt_direct_message(
        message: bytes,
        recipient_pubkey: PublicKey,
    ) -> bytes:
        """Encrypt a direct message for a specific recipient using SealedBox."""
        sealed = SealedBox(recipient_pubkey)
        return sealed.encrypt(message)

    @staticmethod
    def decrypt_direct_message(
        encrypted: bytes,
        private_key: PrivateKey,
    ) -> Optional[bytes]:
        """Decrypt a direct message."""
        unseal = SealedBox(private_key)
        try:
            return unseal.decrypt(encrypted)
        except CryptoError:
            return None

    @staticmethod
    def generate_channel_key() -> bytes:
        """Generate a random 32-byte symmetric key for a private channel."""
        return nacl_random(32)

"""
Tests for src/channels.py — MessageEnvelope, NodeIdentity, ChannelKey, PublicChannel, PrivateChannel
=====================================================================================================

Verifies:
  - MessageEnvelope is a proper dataclass with all expected fields.
  - NodeIdentity generates Ed25519 key pairs, signs, and verifies correctly.
  - ChannelKey generates AES-256-GCM keys and encrypts/decrypts correctly.
  - PublicChannel creates signed envelopes and verifies them correctly.
  - PrivateChannel encrypts/decrypts payloads with AES-256-GCM, and wrong
    keys raise errors.
  - Negative cases: wrong key fails verification/decryption, tampered
    payloads fail verification.
"""

import dataclasses
import os
import pytest

from src.channels import (
    MessageEnvelope, NodeIdentity, ChannelKey,
    PublicChannel, PrivateChannel,
)


# -----------------------------------------------------------------------
# MessageEnvelope tests
# -----------------------------------------------------------------------

class TestMessageEnvelope:
    """MessageEnvelope should be a dataclass with Optional fields."""

    def test_is_dataclass(self):
        assert dataclasses.is_dataclass(MessageEnvelope)

    def test_default_values_are_none(self):
        env = MessageEnvelope()
        assert env.channel_id is None
        assert env.sender_id is None
        assert env.payload is None
        assert env.signature is None
        assert env.nonce is None
        assert env.encrypted_payload is None
        assert env.onion_layers is None

    def test_set_public_channel_fields(self):
        env = MessageEnvelope(
            channel_id="general",
            sender_id="abcdef1234",
            payload=b"hello",
            signature=b"\x00" * 64,
        )
        assert env.channel_id == "general"
        assert env.sender_id == "abcdef1234"
        assert env.payload == b"hello"
        assert env.signature == b"\x00" * 64
        # Private / DM fields still None
        assert env.nonce is None
        assert env.encrypted_payload is None
        assert env.onion_layers is None

    def test_set_private_channel_fields(self):
        env = MessageEnvelope(
            channel_id="secret",
            nonce=b"\x01" * 12,
            encrypted_payload=b"\xff" * 100,
        )
        assert env.channel_id == "secret"
        assert env.nonce == b"\x01" * 12
        assert env.encrypted_payload == b"\xff" * 100

    def test_set_dm_fields(self):
        env = MessageEnvelope(
            channel_id="dm",
            onion_layers=b"\xaa" * 4096,
        )
        assert env.onion_layers == b"\xaa" * 4096

    def test_equality(self):
        a = MessageEnvelope(channel_id="x", payload=b"y")
        b = MessageEnvelope(channel_id="x", payload=b"y")
        assert a == b

    def test_all_expected_fields_exist(self):
        """Ensure every required field name is present in the dataclass."""
        field_names = {f.name for f in dataclasses.fields(MessageEnvelope)}
        expected = {
            "channel_id",
            "sender_id",
            "payload",
            "signature",
            "nonce",
            "encrypted_payload",
            "onion_layers",
        }
        assert expected.issubset(field_names)


# -----------------------------------------------------------------------
# NodeIdentity tests
# -----------------------------------------------------------------------

class TestNodeIdentity:
    """NodeIdentity wraps Ed25519 key pair: generate, sign, verify."""

    def test_generate(self):
        node = NodeIdentity.generate()
        assert isinstance(node, NodeIdentity)

    def test_public_key_bytes_length(self):
        node = NodeIdentity.generate()
        assert len(node.public_key_bytes) == 32

    def test_two_identities_have_different_keys(self):
        a = NodeIdentity.generate()
        b = NodeIdentity.generate()
        assert a.public_key_bytes != b.public_key_bytes

    def test_sign_returns_64_bytes(self):
        node = NodeIdentity.generate()
        sig = node.sign(b"test data")
        assert isinstance(sig, bytes)
        assert len(sig) == 64

    def test_verify_valid_signature(self):
        node = NodeIdentity.generate()
        data = b"important message"
        sig = node.sign(data)
        assert node.verify(sig, data) is True

    def test_verify_invalid_signature(self):
        node = NodeIdentity.generate()
        data = b"important message"
        sig = node.sign(data)
        # Tamper with signature
        bad_sig = bytearray(sig)
        bad_sig[0] ^= 0xFF
        assert node.verify(bytes(bad_sig), data) is False

    def test_verify_wrong_data(self):
        node = NodeIdentity.generate()
        sig = node.sign(b"original")
        assert node.verify(sig, b"tampered") is False

    def test_verify_with_wrong_key(self):
        alice = NodeIdentity.generate()
        bob = NodeIdentity.generate()
        sig = alice.sign(b"hello")
        # Bob's key should not verify Alice's signature
        assert bob.verify(sig, b"hello") is False

    def test_verify_with_public_key_bytes(self):
        node = NodeIdentity.generate()
        data = b"verify me"
        sig = node.sign(data)
        pub_bytes = node.public_key_bytes
        assert NodeIdentity.verify_with_public_key(pub_bytes, sig, data) is True

    def test_verify_with_public_key_bytes_fails_for_wrong_key(self):
        node = NodeIdentity.generate()
        other = NodeIdentity.generate()
        sig = node.sign(b"data")
        assert NodeIdentity.verify_with_public_key(
            other.public_key_bytes, sig, b"data"
        ) is False

    def test_sign_empty_bytes(self):
        node = NodeIdentity.generate()
        sig = node.sign(b"")
        assert node.verify(sig, b"") is True

    def test_sign_large_payload(self):
        node = NodeIdentity.generate()
        big_data = os.urandom(100_000)
        sig = node.sign(big_data)
        assert node.verify(sig, big_data) is True

    def test_deterministic_from_private_key(self):
        """Constructing from the same private key gives the same public key."""
        node1 = NodeIdentity.generate()
        pk = node1.private_key
        node2 = NodeIdentity(private_key=pk)
        assert node1.public_key_bytes == node2.public_key_bytes
        data = b"check"
        sig = node1.sign(data)
        assert node2.verify(sig, data) is True


# -----------------------------------------------------------------------
# ChannelKey tests
# -----------------------------------------------------------------------

class TestChannelKey:
    """ChannelKey wraps AES-256-GCM: generate, encrypt, decrypt."""

    def test_generate_key_length(self):
        ck = ChannelKey.generate()
        assert len(ck.key_bytes) == 32

    def test_two_keys_are_different(self):
        a = ChannelKey.generate()
        b = ChannelKey.generate()
        assert a.key_bytes != b.key_bytes

    def test_encrypt_produces_bytes(self):
        ck = ChannelKey.generate()
        ct = ck.encrypt(b"hello")
        assert isinstance(ct, bytes)
        # nonce(12) + ciphertext(>=1) + tag(16) = at least 28+len(plaintext)
        assert len(ct) >= 12 + 16 + 5

    def test_encrypt_decrypt_roundtrip(self):
        ck = ChannelKey.generate()
        plaintext = b"This is a secret message!"
        blob = ck.encrypt(plaintext)
        result = ck.decrypt(blob)
        assert result == plaintext

    def test_encrypt_decrypt_empty_plaintext(self):
        ck = ChannelKey.generate()
        blob = ck.encrypt(b"")
        assert ck.decrypt(blob) == b""

    def test_encrypt_decrypt_large_payload(self):
        ck = ChannelKey.generate()
        data = os.urandom(100_000)
        blob = ck.encrypt(data)
        assert ck.decrypt(blob) == data

    def test_decrypt_fails_with_wrong_key(self):
        ck1 = ChannelKey.generate()
        ck2 = ChannelKey.generate()
        blob = ck1.encrypt(b"secret")
        with pytest.raises(ValueError, match="[Dd]ecryption failed"):
            ck2.decrypt(blob)

    def test_decrypt_fails_with_tampered_ciphertext(self):
        ck = ChannelKey.generate()
        blob = bytearray(ck.encrypt(b"data"))
        # Tamper with the ciphertext portion (after the 12-byte nonce)
        blob[15] ^= 0xFF
        with pytest.raises(ValueError):
            ck.decrypt(bytes(blob))

    def test_decrypt_fails_with_short_blob(self):
        ck = ChannelKey.generate()
        with pytest.raises(ValueError, match="[Bb]lob too short"):
            ck.decrypt(b"\x00" * 10)

    def test_invalid_key_length_rejected(self):
        with pytest.raises(ValueError, match="32 bytes"):
            ChannelKey(b"\x00" * 16)
        with pytest.raises(ValueError, match="32 bytes"):
            ChannelKey(b"\x00" * 64)

    def test_encrypt_with_key_static(self):
        key = os.urandom(32)
        blob = ChannelKey.encrypt_with_key(key, b"static test")
        result = ChannelKey.decrypt_with_key(key, blob)
        assert result == b"static test"

    def test_static_and_instance_interop(self):
        """Encrypting with static method, decrypting with instance (and vice versa)."""
        ck = ChannelKey.generate()
        blob = ChannelKey.encrypt_with_key(ck.key_bytes, b"cross-test")
        assert ck.decrypt(blob) == b"cross-test"

        blob2 = ck.encrypt(b"cross-test-2")
        assert ChannelKey.decrypt_with_key(ck.key_bytes, blob2) == b"cross-test-2"

    def test_each_encrypt_produces_different_ciphertext(self):
        """Due to random nonce, encrypting the same plaintext twice should differ."""
        ck = ChannelKey.generate()
        ct1 = ck.encrypt(b"same")
        ct2 = ck.encrypt(b"same")
        assert ct1 != ct2
        # But both decrypt to the same plaintext
        assert ck.decrypt(ct1) == b"same"
        assert ck.decrypt(ct2) == b"same"

    def test_nonce_is_first_12_bytes(self):
        """Verify that the nonce is the first 12 bytes of the blob."""
        ck = ChannelKey.generate()
        blob = ck.encrypt(b"test")
        nonce = blob[:12]
        assert len(nonce) == 12
        # The remaining part should be ciphertext + tag
        ct_with_tag = blob[12:]
        assert len(ct_with_tag) == len(b"test") + 16  # plaintext len + GCM tag


# -----------------------------------------------------------------------
# PublicChannel tests
# -----------------------------------------------------------------------

class TestPublicChannel:
    """PublicChannel: signed broadcast channel with Ed25519 verification."""

    # -- Construction -----------------------------------------------------

    def test_init_with_channel_id_and_subscribers(self):
        ch = PublicChannel(channel_id="general", subscribers={"node-a", "node-b"})
        assert ch.channel_id == "general"
        assert ch.subscribers == {"node-a", "node-b"}

    def test_init_defaults_to_empty_subscribers(self):
        ch = PublicChannel(channel_id="empty")
        assert ch.channel_id == "empty"
        assert ch.subscribers == set()

    def test_subscribe_and_unsubscribe(self):
        ch = PublicChannel("test")
        ch.subscribe("node-1")
        assert "node-1" in ch.subscribers
        ch.unsubscribe("node-1")
        assert "node-1" not in ch.subscribers

    def test_unsubscribe_nonexistent_is_noop(self):
        ch = PublicChannel("test")
        ch.unsubscribe("nobody")  # should not raise

    # -- create_message ---------------------------------------------------

    def test_create_message_returns_envelope(self):
        alice = NodeIdentity.generate()
        ch = PublicChannel("announcements")
        envelope = ch.create_message(alice, b"hello world")
        assert isinstance(envelope, MessageEnvelope)

    def test_create_message_sets_channel_id(self):
        alice = NodeIdentity.generate()
        ch = PublicChannel("announcements")
        envelope = ch.create_message(alice, b"msg")
        assert envelope.channel_id == "announcements"

    def test_create_message_sets_sender_id_as_hex_pubkey(self):
        alice = NodeIdentity.generate()
        ch = PublicChannel("ch1")
        envelope = ch.create_message(alice, b"data")
        assert envelope.sender_id == alice.public_key_bytes.hex()

    def test_create_message_stores_payload(self):
        alice = NodeIdentity.generate()
        ch = PublicChannel("ch1")
        payload = b"important announcement"
        envelope = ch.create_message(alice, payload)
        assert envelope.payload == payload

    def test_create_message_stores_signature(self):
        alice = NodeIdentity.generate()
        ch = PublicChannel("ch1")
        envelope = ch.create_message(alice, b"test")
        assert envelope.signature is not None
        assert isinstance(envelope.signature, bytes)
        assert len(envelope.signature) == 64  # Ed25519 signature is 64 bytes

    def test_create_message_signature_is_valid_ed25519(self):
        """The embedded signature should verify against the sender's pubkey."""
        alice = NodeIdentity.generate()
        ch = PublicChannel("ch1")
        envelope = ch.create_message(alice, b"verify me")
        # Manually verify using NodeIdentity static method
        pub_bytes = bytes.fromhex(envelope.sender_id)
        assert NodeIdentity.verify_with_public_key(
            pub_bytes, envelope.signature, envelope.payload
        ) is True

    def test_create_message_with_empty_payload(self):
        alice = NodeIdentity.generate()
        ch = PublicChannel("ch1")
        envelope = ch.create_message(alice, b"")
        assert envelope.payload == b""
        assert envelope.signature is not None

    def test_create_message_with_large_payload(self):
        alice = NodeIdentity.generate()
        ch = PublicChannel("ch1")
        big_data = os.urandom(100_000)
        envelope = ch.create_message(alice, big_data)
        assert envelope.payload == big_data
        assert envelope.signature is not None

    def test_create_message_private_fields_are_none(self):
        """Public channel envelopes should not set private/DM fields."""
        alice = NodeIdentity.generate()
        ch = PublicChannel("ch1")
        envelope = ch.create_message(alice, b"msg")
        assert envelope.nonce is None
        assert envelope.encrypted_payload is None
        assert envelope.onion_layers is None

    # -- verify_message: valid cases --------------------------------------

    def test_verify_valid_message(self):
        alice = NodeIdentity.generate()
        ch = PublicChannel("ch1")
        envelope = ch.create_message(alice, b"hello")
        assert ch.verify_message(envelope) is True

    def test_verify_valid_message_empty_payload(self):
        alice = NodeIdentity.generate()
        ch = PublicChannel("ch1")
        envelope = ch.create_message(alice, b"")
        assert ch.verify_message(envelope) is True

    def test_verify_valid_message_large_payload(self):
        alice = NodeIdentity.generate()
        ch = PublicChannel("ch1")
        big_data = os.urandom(50_000)
        envelope = ch.create_message(alice, big_data)
        assert ch.verify_message(envelope) is True

    def test_verify_message_from_different_senders(self):
        """Multiple different senders can create verifiable messages."""
        alice = NodeIdentity.generate()
        bob = NodeIdentity.generate()
        ch = PublicChannel("ch1")

        env_alice = ch.create_message(alice, b"from alice")
        env_bob = ch.create_message(bob, b"from bob")

        assert ch.verify_message(env_alice) is True
        assert ch.verify_message(env_bob) is True

    def test_verify_on_different_channel_instance(self):
        """Any PublicChannel instance can verify the envelope (not just the creator)."""
        alice = NodeIdentity.generate()
        ch1 = PublicChannel("ch1")
        ch2 = PublicChannel("ch2")  # different channel, but verify is signature-based
        envelope = ch1.create_message(alice, b"hello")
        # Verification uses the embedded pubkey, so it works on any channel
        assert ch2.verify_message(envelope) is True

    # -- verify_message: tampered payload ---------------------------------

    def test_verify_fails_for_tampered_payload(self):
        """Changing the payload after signing should cause verification to fail."""
        alice = NodeIdentity.generate()
        ch = PublicChannel("ch1")
        envelope = ch.create_message(alice, b"original")
        # Tamper with the payload
        envelope.payload = b"TAMPERED"
        assert ch.verify_message(envelope) is False

    def test_verify_fails_for_single_byte_change_in_payload(self):
        """Even a single byte change should invalidate the signature."""
        alice = NodeIdentity.generate()
        ch = PublicChannel("ch1")
        envelope = ch.create_message(alice, b"hello world")
        tampered = bytearray(envelope.payload)
        tampered[0] ^= 0x01
        envelope.payload = bytes(tampered)
        assert ch.verify_message(envelope) is False

    def test_verify_fails_for_appended_byte(self):
        alice = NodeIdentity.generate()
        ch = PublicChannel("ch1")
        envelope = ch.create_message(alice, b"hello")
        envelope.payload = envelope.payload + b"\x00"
        assert ch.verify_message(envelope) is False

    def test_verify_fails_for_truncated_payload(self):
        alice = NodeIdentity.generate()
        ch = PublicChannel("ch1")
        envelope = ch.create_message(alice, b"hello world")
        envelope.payload = envelope.payload[:5]
        assert ch.verify_message(envelope) is False

    # -- verify_message: tampered signature -------------------------------

    def test_verify_fails_for_tampered_signature(self):
        alice = NodeIdentity.generate()
        ch = PublicChannel("ch1")
        envelope = ch.create_message(alice, b"hello")
        bad_sig = bytearray(envelope.signature)
        bad_sig[0] ^= 0xFF
        envelope.signature = bytes(bad_sig)
        assert ch.verify_message(envelope) is False

    def test_verify_fails_for_zeroed_signature(self):
        alice = NodeIdentity.generate()
        ch = PublicChannel("ch1")
        envelope = ch.create_message(alice, b"hello")
        envelope.signature = b"\x00" * 64
        assert ch.verify_message(envelope) is False

    # -- verify_message: wrong sender -------------------------------------

    def test_verify_fails_for_wrong_sender_id(self):
        """If sender_id is replaced with a different key, verification fails."""
        alice = NodeIdentity.generate()
        bob = NodeIdentity.generate()
        ch = PublicChannel("ch1")
        envelope = ch.create_message(alice, b"hello")
        # Replace sender_id with Bob's public key
        envelope.sender_id = bob.public_key_bytes.hex()
        assert ch.verify_message(envelope) is False

    def test_verify_fails_for_swapped_signature(self):
        """A valid signature from one identity cannot verify for another's payload."""
        alice = NodeIdentity.generate()
        bob = NodeIdentity.generate()
        ch = PublicChannel("ch1")
        env_alice = ch.create_message(alice, b"alice's message")
        env_bob = ch.create_message(bob, b"bob's message")
        # Swap signatures
        env_alice.signature = env_bob.signature
        assert ch.verify_message(env_alice) is False

    # -- verify_message: missing fields -----------------------------------

    def test_verify_fails_for_missing_sender_id(self):
        alice = NodeIdentity.generate()
        ch = PublicChannel("ch1")
        envelope = ch.create_message(alice, b"hello")
        envelope.sender_id = None
        assert ch.verify_message(envelope) is False

    def test_verify_fails_for_missing_payload(self):
        alice = NodeIdentity.generate()
        ch = PublicChannel("ch1")
        envelope = ch.create_message(alice, b"hello")
        envelope.payload = None
        assert ch.verify_message(envelope) is False

    def test_verify_fails_for_missing_signature(self):
        alice = NodeIdentity.generate()
        ch = PublicChannel("ch1")
        envelope = ch.create_message(alice, b"hello")
        envelope.signature = None
        assert ch.verify_message(envelope) is False

    def test_verify_fails_for_invalid_hex_sender_id(self):
        alice = NodeIdentity.generate()
        ch = PublicChannel("ch1")
        envelope = ch.create_message(alice, b"hello")
        envelope.sender_id = "not-valid-hex!!!"
        assert ch.verify_message(envelope) is False

    def test_verify_fails_for_empty_envelope(self):
        ch = PublicChannel("ch1")
        envelope = MessageEnvelope()
        assert ch.verify_message(envelope) is False

    # -- roundtrip / integration ------------------------------------------

    def test_create_then_verify_roundtrip(self):
        """Full create → verify cycle should always succeed."""
        for _ in range(5):
            identity = NodeIdentity.generate()
            ch = PublicChannel("roundtrip-test")
            payload = os.urandom(256)
            envelope = ch.create_message(identity, payload)
            assert ch.verify_message(envelope) is True

    def test_different_payloads_produce_different_signatures(self):
        alice = NodeIdentity.generate()
        ch = PublicChannel("ch1")
        env1 = ch.create_message(alice, b"message one")
        env2 = ch.create_message(alice, b"message two")
        assert env1.signature != env2.signature

    def test_same_payload_same_identity_produces_same_signature(self):
        """Ed25519 is deterministic — same key + same data = same signature."""
        alice = NodeIdentity.generate()
        ch = PublicChannel("ch1")
        env1 = ch.create_message(alice, b"deterministic")
        env2 = ch.create_message(alice, b"deterministic")
        assert env1.signature == env2.signature

    def test_subscriber_set_is_independent_of_message_creation(self):
        """Any NodeIdentity can create messages, not just subscribers."""
        outsider = NodeIdentity.generate()
        ch = PublicChannel("ch1", subscribers={"node-1", "node-2"})
        envelope = ch.create_message(outsider, b"from outsider")
        # Message is still valid even though the sender is not a subscriber
        assert ch.verify_message(envelope) is True


# -----------------------------------------------------------------------
# PrivateChannel tests
# -----------------------------------------------------------------------

class TestPrivateChannel:
    """PrivateChannel: AES-256-GCM encrypted channel with symmetric key."""

    # -- Construction -----------------------------------------------------

    def test_init_with_channel_id_and_key(self):
        key = os.urandom(32)
        ch = PrivateChannel(channel_id="secret-room", key=key)
        assert ch.channel_id == "secret-room"
        assert ch.key == key

    def test_init_rejects_short_key(self):
        with pytest.raises(ValueError, match="32 bytes"):
            PrivateChannel(channel_id="bad", key=b"\x00" * 16)

    def test_init_rejects_long_key(self):
        with pytest.raises(ValueError, match="32 bytes"):
            PrivateChannel(channel_id="bad", key=b"\x00" * 64)

    def test_init_rejects_empty_key(self):
        with pytest.raises(ValueError, match="32 bytes"):
            PrivateChannel(channel_id="bad", key=b"")

    # -- create_message ---------------------------------------------------

    def test_create_message_returns_envelope(self):
        key = os.urandom(32)
        ch = PrivateChannel("secret", key=key)
        envelope = ch.create_message(key, b"hello")
        assert isinstance(envelope, MessageEnvelope)

    def test_create_message_sets_channel_id(self):
        key = os.urandom(32)
        ch = PrivateChannel("secret", key=key)
        envelope = ch.create_message(key, b"hello")
        assert envelope.channel_id == "secret"

    def test_create_message_sets_nonce(self):
        key = os.urandom(32)
        ch = PrivateChannel("secret", key=key)
        envelope = ch.create_message(key, b"hello")
        assert envelope.nonce is not None
        assert isinstance(envelope.nonce, bytes)
        assert len(envelope.nonce) == 12  # AES-GCM nonce is 12 bytes

    def test_create_message_sets_encrypted_payload(self):
        key = os.urandom(32)
        ch = PrivateChannel("secret", key=key)
        envelope = ch.create_message(key, b"hello")
        assert envelope.encrypted_payload is not None
        assert isinstance(envelope.encrypted_payload, bytes)
        # encrypted_payload = nonce(12) + ciphertext(len(payload)) + tag(16)
        assert len(envelope.encrypted_payload) == 12 + 5 + 16

    def test_create_message_encrypted_payload_starts_with_nonce(self):
        """The encrypted_payload should start with the same nonce bytes."""
        key = os.urandom(32)
        ch = PrivateChannel("secret", key=key)
        envelope = ch.create_message(key, b"test")
        assert envelope.encrypted_payload[:12] == envelope.nonce

    def test_create_message_payload_is_not_plaintext(self):
        """The encrypted_payload must not contain the plaintext in the clear."""
        key = os.urandom(32)
        ch = PrivateChannel("secret", key=key)
        payload = b"this is my secret message!"
        envelope = ch.create_message(key, payload)
        # The plaintext should not appear as a substring of encrypted_payload
        assert payload not in envelope.encrypted_payload

    def test_create_message_public_fields_are_none(self):
        """Private channel envelopes should not set public/DM fields."""
        key = os.urandom(32)
        ch = PrivateChannel("secret", key=key)
        envelope = ch.create_message(key, b"msg")
        assert envelope.sender_id is None
        assert envelope.payload is None
        assert envelope.signature is None
        assert envelope.onion_layers is None

    def test_create_message_rejects_wrong_key_length(self):
        key = os.urandom(32)
        ch = PrivateChannel("secret", key=key)
        with pytest.raises(ValueError, match="32 bytes"):
            ch.create_message(b"\x00" * 16, b"hello")

    def test_create_message_with_empty_payload(self):
        key = os.urandom(32)
        ch = PrivateChannel("secret", key=key)
        envelope = ch.create_message(key, b"")
        assert envelope.encrypted_payload is not None
        # nonce(12) + ciphertext(0) + tag(16) = 28
        assert len(envelope.encrypted_payload) == 28

    def test_create_message_with_large_payload(self):
        key = os.urandom(32)
        ch = PrivateChannel("secret", key=key)
        big_data = os.urandom(100_000)
        envelope = ch.create_message(key, big_data)
        assert envelope.encrypted_payload is not None
        assert len(envelope.encrypted_payload) == 12 + 100_000 + 16

    # -- decrypt_message --------------------------------------------------

    def test_decrypt_message_roundtrip(self):
        """Encrypt then decrypt should return the original plaintext."""
        key = os.urandom(32)
        ch = PrivateChannel("secret", key=key)
        plaintext = b"Hello, encrypted world!"
        envelope = ch.create_message(key, plaintext)
        result = ch.decrypt_message(key, envelope)
        assert result == plaintext

    def test_decrypt_message_empty_payload_roundtrip(self):
        key = os.urandom(32)
        ch = PrivateChannel("secret", key=key)
        envelope = ch.create_message(key, b"")
        result = ch.decrypt_message(key, envelope)
        assert result == b""

    def test_decrypt_message_large_payload_roundtrip(self):
        key = os.urandom(32)
        ch = PrivateChannel("secret", key=key)
        big_data = os.urandom(100_000)
        envelope = ch.create_message(key, big_data)
        result = ch.decrypt_message(key, envelope)
        assert result == big_data

    def test_decrypt_message_binary_payload_roundtrip(self):
        """All 256 byte values should survive the roundtrip."""
        key = os.urandom(32)
        ch = PrivateChannel("secret", key=key)
        plaintext = bytes(range(256))
        envelope = ch.create_message(key, plaintext)
        result = ch.decrypt_message(key, envelope)
        assert result == plaintext

    def test_decrypt_message_repeated_roundtrip(self):
        """Multiple encrypt/decrypt cycles should all succeed."""
        key = os.urandom(32)
        ch = PrivateChannel("secret", key=key)
        for _ in range(10):
            payload = os.urandom(128)
            envelope = ch.create_message(key, payload)
            assert ch.decrypt_message(key, envelope) == payload

    # -- Wrong key raises error -------------------------------------------

    def test_decrypt_fails_with_wrong_key(self):
        """Using a different key to decrypt must raise ValueError."""
        key = os.urandom(32)
        wrong_key = os.urandom(32)
        ch = PrivateChannel("secret", key=key)
        envelope = ch.create_message(key, b"secret message")
        with pytest.raises(ValueError, match="[Dd]ecryption failed"):
            ch.decrypt_message(wrong_key, envelope)

    def test_decrypt_fails_with_wrong_key_returns_no_plaintext(self):
        """Ensure the wrong key never silently returns incorrect data."""
        key = os.urandom(32)
        wrong_key = os.urandom(32)
        ch = PrivateChannel("secret", key=key)
        plaintext = b"very secret data"
        envelope = ch.create_message(key, plaintext)
        with pytest.raises(ValueError):
            result = ch.decrypt_message(wrong_key, envelope)
            # Should not reach here, but if it does:
            assert result != plaintext

    def test_decrypt_fails_with_all_zeros_key(self):
        key = os.urandom(32)
        ch = PrivateChannel("secret", key=key)
        envelope = ch.create_message(key, b"hello")
        with pytest.raises(ValueError):
            ch.decrypt_message(b"\x00" * 32, envelope)

    def test_decrypt_fails_with_wrong_key_length(self):
        key = os.urandom(32)
        ch = PrivateChannel("secret", key=key)
        envelope = ch.create_message(key, b"hello")
        with pytest.raises(ValueError, match="32 bytes"):
            ch.decrypt_message(b"\x00" * 16, envelope)

    # -- Tampered ciphertext raises error ----------------------------------

    def test_decrypt_fails_with_tampered_ciphertext(self):
        """Flipping a byte in the ciphertext portion should cause failure."""
        key = os.urandom(32)
        ch = PrivateChannel("secret", key=key)
        envelope = ch.create_message(key, b"data")
        # Tamper with a byte in the ciphertext portion (after the 12-byte nonce)
        tampered = bytearray(envelope.encrypted_payload)
        tampered[15] ^= 0xFF
        envelope.encrypted_payload = bytes(tampered)
        with pytest.raises(ValueError):
            ch.decrypt_message(key, envelope)

    def test_decrypt_fails_with_tampered_nonce_in_payload(self):
        """Flipping a byte in the nonce portion should cause failure."""
        key = os.urandom(32)
        ch = PrivateChannel("secret", key=key)
        envelope = ch.create_message(key, b"data")
        tampered = bytearray(envelope.encrypted_payload)
        tampered[0] ^= 0xFF
        envelope.encrypted_payload = bytes(tampered)
        with pytest.raises(ValueError):
            ch.decrypt_message(key, envelope)

    def test_decrypt_fails_with_truncated_payload(self):
        key = os.urandom(32)
        ch = PrivateChannel("secret", key=key)
        envelope = ch.create_message(key, b"hello world")
        # Truncate the encrypted payload
        envelope.encrypted_payload = envelope.encrypted_payload[:20]
        with pytest.raises(ValueError):
            ch.decrypt_message(key, envelope)

    def test_decrypt_fails_with_none_encrypted_payload(self):
        key = os.urandom(32)
        ch = PrivateChannel("secret", key=key)
        envelope = MessageEnvelope(channel_id="secret")
        with pytest.raises(ValueError, match="no encrypted_payload"):
            ch.decrypt_message(key, envelope)

    def test_decrypt_fails_with_very_short_blob(self):
        key = os.urandom(32)
        ch = PrivateChannel("secret", key=key)
        envelope = MessageEnvelope(
            channel_id="secret",
            encrypted_payload=b"\x00" * 10,
        )
        with pytest.raises(ValueError, match="[Bb]lob too short"):
            ch.decrypt_message(key, envelope)

    # -- Non-members cannot decrypt ----------------------------------------

    def test_non_member_cannot_decrypt(self):
        """A node without the channel key cannot decrypt private messages."""
        member_key = os.urandom(32)
        non_member_key = os.urandom(32)

        ch = PrivateChannel("secret", key=member_key)
        envelope = ch.create_message(member_key, b"members-only content")

        # Non-member creates their own channel object but with wrong key
        ch_non_member = PrivateChannel("secret", key=non_member_key)
        with pytest.raises(ValueError, match="[Dd]ecryption failed"):
            ch_non_member.decrypt_message(non_member_key, envelope)

    # -- Nonce uniqueness --------------------------------------------------

    def test_each_encryption_uses_different_nonce(self):
        """Two encryptions of the same plaintext should produce different nonces."""
        key = os.urandom(32)
        ch = PrivateChannel("secret", key=key)
        env1 = ch.create_message(key, b"same")
        env2 = ch.create_message(key, b"same")
        assert env1.nonce != env2.nonce
        assert env1.encrypted_payload != env2.encrypted_payload
        # But both decrypt to the same plaintext
        assert ch.decrypt_message(key, env1) == b"same"
        assert ch.decrypt_message(key, env2) == b"same"

    # -- Cross-channel isolation -------------------------------------------

    def test_different_channels_different_keys(self):
        """Messages from one channel cannot be decrypted by another channel's key."""
        key_a = os.urandom(32)
        key_b = os.urandom(32)
        ch_a = PrivateChannel("channel-a", key=key_a)
        ch_b = PrivateChannel("channel-b", key=key_b)

        envelope = ch_a.create_message(key_a, b"for channel A")
        with pytest.raises(ValueError):
            ch_b.decrypt_message(key_b, envelope)

    # -- Any member with the key can encrypt and decrypt -------------------

    def test_any_member_can_encrypt_and_decrypt(self):
        """Any node with the channel key can create and read messages."""
        key = os.urandom(32)
        ch_alice = PrivateChannel("secret", key=key)
        ch_bob = PrivateChannel("secret", key=key)

        # Alice encrypts
        envelope = ch_alice.create_message(key, b"from alice")
        # Bob decrypts
        assert ch_bob.decrypt_message(key, envelope) == b"from alice"

        # Bob encrypts
        envelope2 = ch_bob.create_message(key, b"from bob")
        # Alice decrypts
        assert ch_alice.decrypt_message(key, envelope2) == b"from bob"

    # -- Integration: full roundtrip multiple times ------------------------

    def test_full_roundtrip_multiple_payloads(self):
        """Full encrypt → decrypt cycle for various payload sizes."""
        key = os.urandom(32)
        ch = PrivateChannel("integration", key=key)
        payloads = [
            b"",
            b"x",
            b"hello world",
            os.urandom(1),
            os.urandom(256),
            os.urandom(4096),
            os.urandom(65536),
        ]
        for payload in payloads:
            envelope = ch.create_message(key, payload)
            result = ch.decrypt_message(key, envelope)
            assert result == payload, f"Failed for payload of length {len(payload)}"

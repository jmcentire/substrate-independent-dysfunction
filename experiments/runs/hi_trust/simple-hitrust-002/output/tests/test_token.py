"""
Comprehensive tests for src/token.py

Covers:
  - Token dataclass structure and fields
  - create_token: derivation, signing, determinism
  - verify_token: valid signatures, rejection of tampering
  - serialize_token / deserialize_token: JSON round-tripping
  - Integration with crypto.py primitives
  - Spec requirements:
      (a) Same user, two sites → tokens differ and are unlinkable
      (b) Different users, same site → tokens don't collide
      (c) Token signature verifies
      (e) SSO cannot derive site-specific tokens (doesn't know site_id)
      (f) Site cannot derive tokens for other sites (doesn't know user_salt)
"""

import json
import time

import pytest

from src.crypto import generate_ed25519_keypair, ed25519_public_key_from_private
from src.token import (
    Token,
    create_token,
    verify_token,
    serialize_token,
    deserialize_token,
    _build_payload,
)


# ── Fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture
def keypair():
    """Return a (private_key, public_key) tuple of 32-byte raw Ed25519 keys."""
    return generate_ed25519_keypair()


@pytest.fixture
def sample_token(keypair):
    """Return a pre-built Token for 'alice' on 'example.com'."""
    priv, _ = keypair
    return create_token(
        username="alice",
        site_id="example.com",
        user_id="uid-42",
        user_salt="salt-abc",
        signing_key=priv,
        proof_of_human_score=0.95,
        _timestamp=1700000000.0,
    )


# ── Token dataclass structure ──────────────────────────────────────────────

class TestTokenDataclass:
    """The Token dataclass must have the five required fields."""

    def test_fields_exist(self, sample_token):
        assert hasattr(sample_token, "verification_hash")
        assert hasattr(sample_token, "site_id")
        assert hasattr(sample_token, "timestamp")
        assert hasattr(sample_token, "proof_of_human_score")
        assert hasattr(sample_token, "signature")

    def test_verification_hash_is_hex_64(self, sample_token):
        assert isinstance(sample_token.verification_hash, str)
        assert len(sample_token.verification_hash) == 64
        int(sample_token.verification_hash, 16)  # must be valid hex

    def test_site_id_matches_input(self, sample_token):
        assert sample_token.site_id == "example.com"

    def test_timestamp_type(self, sample_token):
        assert isinstance(sample_token.timestamp, float)

    def test_proof_of_human_score_range(self, sample_token):
        assert 0.0 <= sample_token.proof_of_human_score <= 1.0

    def test_signature_is_hex_128(self, sample_token):
        """Ed25519 signature is 64 bytes = 128 hex chars."""
        assert isinstance(sample_token.signature, str)
        assert len(sample_token.signature) == 128
        int(sample_token.signature, 16)  # must be valid hex


# ── create_token ───────────────────────────────────────────────────────────

class TestCreateToken:
    """Tests for create_token()."""

    def test_returns_token_instance(self, keypair):
        priv, _ = keypair
        tok = create_token("alice", "site-a", "uid-1", "salt-1", priv, 0.9)
        assert isinstance(tok, Token)

    def test_deterministic_with_fixed_timestamp(self, keypair):
        priv, _ = keypair
        t1 = create_token("alice", "site-a", "uid-1", "salt-1", priv, 0.9, _timestamp=1000.0)
        t2 = create_token("alice", "site-a", "uid-1", "salt-1", priv, 0.9, _timestamp=1000.0)
        assert t1 == t2

    def test_timestamp_defaults_to_now(self, keypair):
        priv, _ = keypair
        before = time.time()
        tok = create_token("alice", "site-a", "uid-1", "salt-1", priv, 0.9)
        after = time.time()
        assert before <= tok.timestamp <= after

    def test_proof_of_human_score_out_of_range_raises(self, keypair):
        priv, _ = keypair
        with pytest.raises(ValueError, match="proof_of_human_score"):
            create_token("alice", "site-a", "uid-1", "salt-1", priv, 1.5)
        with pytest.raises(ValueError, match="proof_of_human_score"):
            create_token("alice", "site-a", "uid-1", "salt-1", priv, -0.1)

    def test_score_boundary_values(self, keypair):
        priv, _ = keypair
        tok0 = create_token("alice", "s", "u", "salt", priv, 0.0, _timestamp=1.0)
        tok1 = create_token("alice", "s", "u", "salt", priv, 1.0, _timestamp=1.0)
        assert tok0.proof_of_human_score == 0.0
        assert tok1.proof_of_human_score == 1.0

    def test_different_sites_produce_different_verification_hashes(self, keypair):
        """Spec (a): Same user, two sites → different tokens."""
        priv, _ = keypair
        t_a = create_token("alice", "site-a", "uid-1", "salt-1", priv, 0.9, _timestamp=1.0)
        t_b = create_token("alice", "site-b", "uid-1", "salt-1", priv, 0.9, _timestamp=1.0)
        assert t_a.verification_hash != t_b.verification_hash

    def test_different_users_same_site_no_collision(self, keypair):
        """Spec (b): Different users, same site → tokens don't collide."""
        priv, _ = keypair
        t1 = create_token("alice", "site-x", "uid-1", "salt-a", priv, 0.9, _timestamp=1.0)
        t2 = create_token("bob",   "site-x", "uid-2", "salt-b", priv, 0.9, _timestamp=1.0)
        assert t1.verification_hash != t2.verification_hash

    def test_unicode_inputs(self, keypair):
        priv, _ = keypair
        tok = create_token("用户", "サイト", "uid-ü", "盐值", priv, 0.5, _timestamp=1.0)
        assert isinstance(tok.verification_hash, str) and len(tok.verification_hash) == 64


# ── verify_token ───────────────────────────────────────────────────────────

class TestVerifyToken:
    """Tests for verify_token()."""

    def test_valid_signature_passes(self, keypair, sample_token):
        """Spec (c): Token signature verifies."""
        _, pub = keypair
        assert verify_token(sample_token, pub) is True

    def test_tampered_verification_hash_fails(self, keypair, sample_token):
        _, pub = keypair
        sample_token.verification_hash = "a" * 64
        assert verify_token(sample_token, pub) is False

    def test_tampered_site_id_fails(self, keypair, sample_token):
        _, pub = keypair
        sample_token.site_id = "evil.com"
        assert verify_token(sample_token, pub) is False

    def test_tampered_timestamp_fails(self, keypair, sample_token):
        _, pub = keypair
        sample_token.timestamp = 0.0
        assert verify_token(sample_token, pub) is False

    def test_tampered_score_fails(self, keypair, sample_token):
        _, pub = keypair
        sample_token.proof_of_human_score = 0.01
        assert verify_token(sample_token, pub) is False

    def test_tampered_signature_fails(self, keypair, sample_token):
        _, pub = keypair
        # Flip bits in the signature
        sig_bytes = bytes.fromhex(sample_token.signature)
        bad_sig = bytes([b ^ 0xFF for b in sig_bytes])
        sample_token.signature = bad_sig.hex()
        assert verify_token(sample_token, pub) is False

    def test_wrong_public_key_fails(self, keypair, sample_token):
        """A token signed with one key must not verify with another."""
        _, pub2 = generate_ed25519_keypair()
        assert verify_token(sample_token, pub2) is False

    def test_verify_freshly_created_token(self):
        """Full round-trip: create then verify."""
        priv, pub = generate_ed25519_keypair()
        tok = create_token("bob", "site-z", "uid-99", "salt-zz", priv, 0.75)
        assert verify_token(tok, pub) is True


# ── serialize_token / deserialize_token ────────────────────────────────────

class TestSerialization:
    """Tests for JSON serialization round-tripping."""

    def test_serialize_returns_string(self, sample_token):
        s = serialize_token(sample_token)
        assert isinstance(s, str)

    def test_serialize_is_valid_json(self, sample_token):
        s = serialize_token(sample_token)
        d = json.loads(s)
        assert isinstance(d, dict)

    def test_json_has_all_fields(self, sample_token):
        s = serialize_token(sample_token)
        d = json.loads(s)
        for field in ("verification_hash", "site_id", "timestamp",
                      "proof_of_human_score", "signature"):
            assert field in d

    def test_round_trip_preserves_fields(self, sample_token):
        s = serialize_token(sample_token)
        restored = deserialize_token(s)
        assert restored.verification_hash == sample_token.verification_hash
        assert restored.site_id == sample_token.site_id
        assert restored.timestamp == sample_token.timestamp
        assert restored.proof_of_human_score == sample_token.proof_of_human_score
        assert restored.signature == sample_token.signature

    def test_round_trip_equality(self, sample_token):
        s = serialize_token(sample_token)
        restored = deserialize_token(s)
        assert restored == sample_token

    def test_deserialized_token_verifies(self, keypair, sample_token):
        """Signature must survive serialization round-trip."""
        _, pub = keypair
        s = serialize_token(sample_token)
        restored = deserialize_token(s)
        assert verify_token(restored, pub) is True

    def test_double_round_trip(self, sample_token):
        s1 = serialize_token(sample_token)
        t1 = deserialize_token(s1)
        s2 = serialize_token(t1)
        t2 = deserialize_token(s2)
        assert t2 == sample_token
        assert s1 == s2

    def test_deserialize_invalid_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            deserialize_token("not json at all")

    def test_deserialize_missing_field_raises(self):
        bad = json.dumps({"verification_hash": "a" * 64, "site_id": "s"})
        with pytest.raises((KeyError, TypeError)):
            deserialize_token(bad)


# ── Payload construction ───────────────────────────────────────────────────

class TestBuildPayload:
    """Ensure the canonical payload is deterministic and injective."""

    def test_deterministic(self):
        p1 = _build_payload("abc", "site", 1.0, 0.5)
        p2 = _build_payload("abc", "site", 1.0, 0.5)
        assert p1 == p2

    def test_different_inputs_produce_different_payloads(self):
        p1 = _build_payload("abc", "site-a", 1.0, 0.5)
        p2 = _build_payload("abc", "site-b", 1.0, 0.5)
        assert p1 != p2

    def test_returns_bytes(self):
        p = _build_payload("hash", "site", 1.0, 0.9)
        assert isinstance(p, bytes)

    def test_contains_all_fields(self):
        """The payload string should contain the separator and all field values."""
        p = _build_payload("myhash", "mysite", 42.0, 0.77)
        text = p.decode("utf-8")
        assert "myhash" in text
        assert "mysite" in text
        assert "42.0" in text
        assert "0.77" in text
        assert "||" in text


# ── Unlinkability spec requirements ────────────────────────────────────────

class TestUnlinkability:
    """Verify that tokens for the same user across sites are unlinkable."""

    def test_same_user_two_sites_different_tokens(self):
        """Spec (a): tokens for different sites must differ completely."""
        priv, _ = generate_ed25519_keypair()
        t_a = create_token("alice", "site-a", "uid-1", "salt-1", priv, 0.9, _timestamp=1.0)
        t_b = create_token("alice", "site-b", "uid-1", "salt-1", priv, 0.9, _timestamp=1.0)
        # Verification hashes must differ
        assert t_a.verification_hash != t_b.verification_hash
        # Signatures must also differ (different payload)
        assert t_a.signature != t_b.signature

    def test_sso_cannot_derive_token_without_site_id(self):
        """Spec (e): SSO knows username, user_id, user_salt but not site_id."""
        priv, _ = generate_ed25519_keypair()
        real = create_token("alice", "bank.com", "uid-1", "salt-1", priv, 0.9, _timestamp=1.0)
        # SSO guesses with empty or wrong site_id
        guess1 = create_token("alice", "", "uid-1", "salt-1", priv, 0.9, _timestamp=1.0)
        guess2 = create_token("alice", "wrong.com", "uid-1", "salt-1", priv, 0.9, _timestamp=1.0)
        assert real.verification_hash != guess1.verification_hash
        assert real.verification_hash != guess2.verification_hash

    def test_site_cannot_derive_token_for_other_site_without_salt(self):
        """Spec (f): Site A knows its own site_id and token, but not user_salt."""
        priv, _ = generate_ed25519_keypair()
        real_b = create_token("alice", "site-b", "uid-1", "real-salt", priv, 0.9, _timestamp=1.0)
        # Site A tries to compute site-b's token with guessed salt
        guess = create_token("alice", "site-b", "uid-1", "guessed-salt", priv, 0.9, _timestamp=1.0)
        assert real_b.verification_hash != guess.verification_hash

    def test_many_sites_no_pattern(self):
        """Tokens across 50 sites should appear uniformly distributed."""
        priv, _ = generate_ed25519_keypair()
        hashes = []
        for i in range(50):
            tok = create_token("alice", f"site-{i}", "uid-1", "salt", priv, 0.9, _timestamp=1.0)
            hashes.append(int(tok.verification_hash, 16))
        midpoint = 2 ** 255
        below = sum(1 for h in hashes if h < midpoint)
        # Expect ~25; generous margin
        assert 5 < below < 45, f"Biased: {below}/50 below midpoint"


# ── Edge cases ─────────────────────────────────────────────────────────────

class TestEdgeCases:

    def test_empty_site_id(self):
        priv, pub = generate_ed25519_keypair()
        tok = create_token("alice", "", "uid-1", "salt-1", priv, 0.5, _timestamp=1.0)
        assert verify_token(tok, pub) is True

    def test_very_long_site_id(self):
        priv, pub = generate_ed25519_keypair()
        long_site = "x" * 10000
        tok = create_token("alice", long_site, "uid-1", "salt-1", priv, 0.5, _timestamp=1.0)
        assert tok.site_id == long_site
        assert verify_token(tok, pub) is True

    def test_special_characters_in_fields(self):
        priv, pub = generate_ed25519_keypair()
        tok = create_token(
            username='al||ice"\\',
            site_id='site||"evil',
            user_id="uid-🔑",
            user_salt="salt-\n\t",
            signing_key=priv,
            proof_of_human_score=0.42,
            _timestamp=9999999.999,
        )
        assert verify_token(tok, pub) is True
        # Round-trip through JSON
        restored = deserialize_token(serialize_token(tok))
        assert verify_token(restored, pub) is True
        assert restored == tok

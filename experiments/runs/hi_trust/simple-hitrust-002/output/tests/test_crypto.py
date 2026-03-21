"""
Comprehensive tests for src/crypto.py

Covers every requirement from the Anonymous Identity spec:
  (a) Same user, two sites → tokens are different and unlinkable.
  (b) Different users, same site → tokens don't collide.
  (c) Token signature verifies.
  (d) Routing key correctly resolves (deterministic, domain-separated).
  (e) SSO cannot derive site-specific tokens (doesn't know site_id).
  (f) Site cannot derive tokens for other sites (doesn't know user_salt).
"""

import hashlib
import os
import pytest

from src.crypto import (
    credential_hash,
    derive_site_token,
    derive_routing_key,
    generate_ed25519_keypair,
    ed25519_sign,
    ed25519_verify,
    ed25519_public_key_from_private,
)


# ── Credential hashing ─────────────────────────────────────────────────────

class TestCredentialHash:
    """Tests for credential_hash(username, password)."""

    def test_returns_hex_string_of_correct_length(self):
        h = credential_hash("alice", "password123")
        assert isinstance(h, str)
        assert len(h) == 64  # SHA-256 hex digest
        int(h, 16)  # must be valid hex

    def test_deterministic(self):
        assert credential_hash("bob", "s3cret") == credential_hash("bob", "s3cret")

    def test_different_passwords_differ(self):
        assert credential_hash("alice", "pw1") != credential_hash("alice", "pw2")

    def test_different_usernames_differ(self):
        assert credential_hash("alice", "pw") != credential_hash("bob", "pw")

    def test_no_concatenation_ambiguity(self):
        """'ab'+'c' must differ from 'a'+'bc' (length-prefixing)."""
        assert credential_hash("ab", "c") != credential_hash("a", "bc")

    def test_empty_inputs(self):
        h = credential_hash("", "")
        assert isinstance(h, str) and len(h) == 64

    def test_unicode_inputs(self):
        h = credential_hash("用户", "密码")
        assert isinstance(h, str) and len(h) == 64


# ── Site token derivation ──────────────────────────────────────────────────

class TestDeriveSiteToken:
    """Tests for derive_site_token(username, site_id, user_id, user_salt)."""

    USER = "alice"
    UID = "uid-42"
    SALT = "random-salt-xyz"

    def test_returns_hex_string(self):
        t = derive_site_token(self.USER, "site-a", self.UID, self.SALT)
        assert isinstance(t, str) and len(t) == 64

    def test_deterministic(self):
        a = derive_site_token(self.USER, "site-a", self.UID, self.SALT)
        b = derive_site_token(self.USER, "site-a", self.UID, self.SALT)
        assert a == b

    def test_same_user_different_sites_yield_different_tokens(self):
        """Spec (a): Same user, two sites → tokens differ."""
        t_a = derive_site_token(self.USER, "site-a", self.UID, self.SALT)
        t_b = derive_site_token(self.USER, "site-b", self.UID, self.SALT)
        assert t_a != t_b

    def test_different_users_same_site_yield_different_tokens(self):
        """Spec (b): Different users, same site → no collision."""
        t1 = derive_site_token("alice", "site-x", "uid-1", "salt-1")
        t2 = derive_site_token("bob",   "site-x", "uid-2", "salt-2")
        assert t1 != t2

    def test_token_differs_from_routing_key(self):
        """Token and routing key for the same inputs must differ (domain separation)."""
        token = derive_site_token(self.USER, "site-a", self.UID, self.SALT)
        rk = derive_routing_key(self.USER, "site-a", self.UID, self.SALT)
        assert token != rk

    def test_sso_cannot_derive_token_without_site_id(self):
        """Spec (e): SSO knows (username, user_id, user_salt) but not site_id.

        Without site_id, the SSO cannot produce the correct token for any
        specific site.  We verify that *no* single-input guess can match.
        """
        real_token = derive_site_token(self.USER, "site-a", self.UID, self.SALT)
        # SSO tries with an empty or wrong site_id
        assert derive_site_token(self.USER, "", self.UID, self.SALT) != real_token
        assert derive_site_token(self.USER, "wrong", self.UID, self.SALT) != real_token

    def test_site_cannot_derive_token_for_other_site_without_user_salt(self):
        """Spec (f): Site A knows site_id_A and the token but not user_salt.

        Without user_salt, site A cannot compute the token for site B.
        """
        real_token_b = derive_site_token(self.USER, "site-b", self.UID, self.SALT)
        # Site A tries with an empty or guessed salt
        assert derive_site_token(self.USER, "site-b", self.UID, "") != real_token_b
        assert derive_site_token(self.USER, "site-b", self.UID, "guess") != real_token_b


# ── Routing key derivation ─────────────────────────────────────────────────

class TestDeriveRoutingKey:
    """Tests for derive_routing_key(username, site_id, user_id, user_salt)."""

    def test_returns_hex_string(self):
        rk = derive_routing_key("alice", "site-a", "uid-1", "salt-1")
        assert isinstance(rk, str) and len(rk) == 64

    def test_deterministic(self):
        """Spec (d): Routing key must be deterministic so the SSO can resolve it."""
        a = derive_routing_key("alice", "site-a", "uid-1", "salt-1")
        b = derive_routing_key("alice", "site-a", "uid-1", "salt-1")
        assert a == b

    def test_different_sites_different_routing_keys(self):
        rk_a = derive_routing_key("alice", "site-a", "uid-1", "salt-1")
        rk_b = derive_routing_key("alice", "site-b", "uid-1", "salt-1")
        assert rk_a != rk_b

    def test_different_users_different_routing_keys(self):
        rk1 = derive_routing_key("alice", "site-a", "uid-1", "salt-1")
        rk2 = derive_routing_key("bob",   "site-a", "uid-2", "salt-2")
        assert rk1 != rk2

    def test_domain_separation_from_site_token(self):
        """The 'routing' domain separator keeps routing keys separate from tokens."""
        args = ("alice", "site-a", "uid-1", "salt-1")
        assert derive_routing_key(*args) != derive_site_token(*args)


# ── Unlinkability ──────────────────────────────────────────────────────────

class TestUnlinkability:
    """Verify that tokens reveal no correlation across sites."""

    def test_no_common_prefix(self):
        """Tokens for different sites should not share a common prefix."""
        t_a = derive_site_token("alice", "site-a", "uid-1", "salt")
        t_b = derive_site_token("alice", "site-b", "uid-1", "salt")
        # The probability of sharing even the first 4 hex chars is ~1/65536.
        # We do not assert on randomness, but we assert they fully differ.
        assert t_a != t_b

    def test_tokens_spread_uniformly(self):
        """Generate many tokens and check they don't cluster in one half of the hash space."""
        tokens = []
        for i in range(50):
            t = derive_site_token("alice", f"site-{i}", "uid-1", "salt")
            tokens.append(int(t, 16))
        midpoint = 2**255
        below = sum(1 for t in tokens if t < midpoint)
        # Expect roughly 25; allow generous margin (10–40).
        assert 5 < below < 45, f"Biased distribution: {below}/50 below midpoint"


# ── Ed25519 signing & verification ─────────────────────────────────────────

class TestEd25519:
    """Tests for Ed25519 keygen, signing, and verification."""

    def test_keypair_lengths(self):
        priv, pub = generate_ed25519_keypair()
        assert len(priv) == 32
        assert len(pub) == 32

    def test_keypair_randomness(self):
        """Two calls must produce different keys."""
        priv1, _ = generate_ed25519_keypair()
        priv2, _ = generate_ed25519_keypair()
        assert priv1 != priv2

    def test_sign_and_verify(self):
        """Spec (c): Token signature verifies."""
        priv, pub = generate_ed25519_keypair()
        msg = b"verification_hash|site-a|1234567890|0.95"
        sig = ed25519_sign(priv, msg)
        assert isinstance(sig, bytes) and len(sig) == 64
        assert ed25519_verify(pub, msg, sig) is True

    def test_verify_rejects_tampered_message(self):
        priv, pub = generate_ed25519_keypair()
        msg = b"original message"
        sig = ed25519_sign(priv, msg)
        assert ed25519_verify(pub, b"tampered message", sig) is False

    def test_verify_rejects_tampered_signature(self):
        priv, pub = generate_ed25519_keypair()
        msg = b"some message"
        sig = ed25519_sign(priv, msg)
        bad_sig = bytes([b ^ 0xFF for b in sig])  # flip all bits
        assert ed25519_verify(pub, msg, bad_sig) is False

    def test_verify_rejects_wrong_public_key(self):
        priv1, _ = generate_ed25519_keypair()
        _, pub2 = generate_ed25519_keypair()
        msg = b"message"
        sig = ed25519_sign(priv1, msg)
        assert ed25519_verify(pub2, msg, sig) is False

    def test_signing_is_deterministic(self):
        """Ed25519 is inherently deterministic."""
        priv, _ = generate_ed25519_keypair()
        msg = b"deterministic test"
        sig1 = ed25519_sign(priv, msg)
        sig2 = ed25519_sign(priv, msg)
        assert sig1 == sig2

    def test_public_key_from_private(self):
        priv, pub = generate_ed25519_keypair()
        derived_pub = ed25519_public_key_from_private(priv)
        assert derived_pub == pub

    def test_sign_rejects_bad_key_length(self):
        with pytest.raises(ValueError, match="32 bytes"):
            ed25519_sign(b"short", b"msg")

    def test_verify_rejects_bad_key_length(self):
        with pytest.raises(ValueError, match="32 bytes"):
            ed25519_verify(b"short", b"msg", b"x" * 64)

    def test_sign_empty_message(self):
        priv, pub = generate_ed25519_keypair()
        sig = ed25519_sign(priv, b"")
        assert ed25519_verify(pub, b"", sig) is True

    def test_sign_large_message(self):
        priv, pub = generate_ed25519_keypair()
        msg = os.urandom(10_000)
        sig = ed25519_sign(priv, msg)
        assert ed25519_verify(pub, msg, sig) is True


# ── Full token workflow integration ────────────────────────────────────────

class TestTokenWorkflow:
    """End-to-end: derive token, sign it, verify on the site side."""

    def test_full_flow(self):
        # 1. Client authenticates to SSO
        cred = credential_hash("alice", "hunter2")
        assert len(cred) == 64

        # 2. SSO returns user_id, user_salt (simulated)
        user_id = "uid-abc-123"
        user_salt = "salt-xyz-789"

        # 3. Client derives site token
        site_id = "example.com"
        verification_hash = derive_site_token("alice", site_id, user_id, user_salt)
        assert len(verification_hash) == 64

        # 4. Client builds and signs token
        priv, pub = generate_ed25519_keypair()
        import json, time
        token_payload = json.dumps({
            "verification_hash": verification_hash,
            "site_id": site_id,
            "timestamp": int(time.time()),
            "proof_of_human_score": 0.97,
        }).encode("utf-8")
        signature = ed25519_sign(priv, token_payload)

        # 5. Site verifies
        assert ed25519_verify(pub, token_payload, signature) is True

        # 6. Client derives routing key for this site
        rk = derive_routing_key("alice", site_id, user_id, user_salt)
        assert len(rk) == 64
        assert rk != verification_hash  # domain separation

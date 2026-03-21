"""
Test suite for the Anonymous Identity System.

Covers:
  1. Same user, two sites → tokens different and unlinkable
  2. Different users, same site → no collision
  3. Token signature verifies
  4. Routing key resolves correctly
  5. SSO cannot derive site-specific tokens
  6. Site cannot derive tokens for other sites
"""

import time
import pytest

from anonymous_identity import (
    SigningKeyPair,
    Token,
    build_signed_token,
    credential_hash,
    routing_key,
    site_specific_token,
    verification_hash,
)
from sso import SSOServer


# =========================================================================
# Fixtures
# =========================================================================

@pytest.fixture
def sso():
    return SSOServer()


@pytest.fixture
def alice_creds():
    return ("alice", "correct-horse-battery-staple")


@pytest.fixture
def bob_creds():
    return ("bob", "hunter2")


@pytest.fixture
def alice_registered(sso, alice_creds):
    """Register Alice and return (user_id, user_salt, cred_hash)."""
    ch = credential_hash(*alice_creds)
    uid = sso.register(ch, encrypted_contact="enc:alice@example.com")
    _, user_salt, _ = sso.authenticate(ch)
    return uid, user_salt, ch


@pytest.fixture
def bob_registered(sso, bob_creds):
    ch = credential_hash(*bob_creds)
    uid = sso.register(ch, encrypted_contact="enc:bob@example.com")
    _, user_salt, _ = sso.authenticate(ch)
    return uid, user_salt, ch


# =========================================================================
# 1. Same user, two sites → tokens different and unlinkable
# =========================================================================

class TestSameUserTwoSites:
    def test_tokens_differ(self, alice_creds, alice_registered):
        username, _ = alice_creds
        uid, salt, _ = alice_registered

        token_a = site_specific_token(username, "site-alpha", uid, salt)
        token_b = site_specific_token(username, "site-beta", uid, salt)

        assert token_a != token_b, "Tokens for different sites must differ"

    def test_verification_hashes_differ(self, alice_creds, alice_registered):
        username, _ = alice_creds
        uid, salt, _ = alice_registered

        vh_a = verification_hash(site_specific_token(username, "site-alpha", uid, salt))
        vh_b = verification_hash(site_specific_token(username, "site-beta", uid, salt))

        assert vh_a != vh_b

    def test_tokens_unlinkable(self, alice_creds, alice_registered):
        """Given only the two tokens (no salt), they share no common prefix
        or structural similarity — they are pseudo-random hex strings."""
        username, _ = alice_creds
        uid, salt, _ = alice_registered

        t_a = site_specific_token(username, "site-alpha", uid, salt)
        t_b = site_specific_token(username, "site-beta", uid, salt)

        # Tokens are 64-char hex strings (SHA-256)
        assert len(t_a) == 64
        assert len(t_b) == 64

        # They should share no unusually long common prefix
        # (probabilistically, ≤ 2 hex chars is expected)
        common_prefix_len = 0
        for a, b in zip(t_a, t_b):
            if a == b:
                common_prefix_len += 1
            else:
                break
        assert common_prefix_len < 8, "Tokens should not share a long common prefix"


# =========================================================================
# 2. Different users, same site → no collision
# =========================================================================

class TestDifferentUsersSameSite:
    def test_no_token_collision(self, alice_creds, bob_creds, alice_registered, bob_registered):
        a_user, _ = alice_creds
        b_user, _ = bob_creds
        a_uid, a_salt, _ = alice_registered
        b_uid, b_salt, _ = bob_registered

        site = "shared-forum"
        t_alice = site_specific_token(a_user, site, a_uid, a_salt)
        t_bob = site_specific_token(b_user, site, b_uid, b_salt)

        assert t_alice != t_bob, "Different users on the same site must have different tokens"

    def test_no_verification_hash_collision(self, alice_creds, bob_creds, alice_registered, bob_registered):
        a_user, _ = alice_creds
        b_user, _ = bob_creds
        a_uid, a_salt, _ = alice_registered
        b_uid, b_salt, _ = bob_registered

        site = "shared-forum"
        vh_a = verification_hash(site_specific_token(a_user, site, a_uid, a_salt))
        vh_b = verification_hash(site_specific_token(b_user, site, b_uid, b_salt))

        assert vh_a != vh_b


# =========================================================================
# 3. Token signature verifies
# =========================================================================

class TestTokenSignature:
    def test_valid_signature(self, alice_creds, alice_registered):
        username, _ = alice_creds
        uid, salt, _ = alice_registered
        kp = SigningKeyPair()

        tok = build_signed_token(username, "site-x", uid, salt, kp)

        assert tok.signature is not None
        assert tok.public_key is not None
        assert tok.verify_signature() is True

    def test_tampered_token_fails_verification(self, alice_creds, alice_registered):
        username, _ = alice_creds
        uid, salt, _ = alice_registered
        kp = SigningKeyPair()

        tok = build_signed_token(username, "site-x", uid, salt, kp)

        # Tamper with proof_of_human_score
        tok.proof_of_human_score = 0.0
        assert tok.verify_signature() is False

    def test_wrong_key_fails_verification(self, alice_creds, alice_registered):
        username, _ = alice_creds
        uid, salt, _ = alice_registered
        kp1 = SigningKeyPair()
        kp2 = SigningKeyPair()

        tok = build_signed_token(username, "site-x", uid, salt, kp1)

        # Replace public key with a different one
        tok.public_key = kp2.public_key_bytes()
        assert tok.verify_signature() is False

    def test_unsigned_token_fails(self):
        tok = Token(
            verification_hash="abc",
            site_id="site-y",
            timestamp=time.time(),
            proof_of_human_score=0.5,
        )
        assert tok.verify_signature() is False


# =========================================================================
# 4. Routing key resolves correctly
# =========================================================================

class TestRoutingKey:
    def test_routing_key_deterministic(self, alice_creds, alice_registered):
        username, _ = alice_creds
        uid, salt, _ = alice_registered

        rk1 = routing_key(username, "site-a", uid, salt)
        rk2 = routing_key(username, "site-a", uid, salt)
        assert rk1 == rk2

    def test_routing_keys_differ_across_sites(self, alice_creds, alice_registered):
        username, _ = alice_creds
        uid, salt, _ = alice_registered

        rk_a = routing_key(username, "site-a", uid, salt)
        rk_b = routing_key(username, "site-b", uid, salt)
        assert rk_a != rk_b

    def test_message_delivery_via_routing_key(self, sso, alice_creds, alice_registered):
        username, _ = alice_creds
        uid, salt, _ = alice_registered

        rk = routing_key(username, "site-chat", uid, salt)
        sso.register_routing_key(rk, uid)

        delivered = sso.route_message(rk, "encrypted-secret-message")
        assert delivered is True

        inbox = sso.fetch_inbox(uid)
        assert len(inbox) == 1
        assert inbox[0] == (rk, "encrypted-secret-message")

    def test_unknown_routing_key_not_delivered(self, sso):
        assert sso.route_message("nonexistent-key", "payload") is False


# =========================================================================
# 5. SSO cannot derive site-specific tokens
# =========================================================================

class TestSSOCannotDeriveTokens:
    """The SSO knows credential_hash, user_id, and user_salt — but it
    does NOT know the username.  Without the username it cannot compute
    site_specific_token(username, site_id, user_id, user_salt)."""

    def test_sso_lacks_username(self, sso, alice_creds, alice_registered):
        username, password = alice_creds
        uid, salt, ch = alice_registered

        # The SSO stores: credential_hash, user_id, user_salt
        record = sso._get_user_record(ch)
        assert record is not None

        # The SSO does NOT have the username
        assert not hasattr(record, "username")
        # It only has the one-way credential_hash
        assert record.credential_hash == ch

        # Even with user_id and user_salt, the SSO cannot call
        # site_specific_token because it does not know 'username'.
        # A wrong guess yields a completely different token.
        wrong_token = site_specific_token("wrong-guess", "site-x", uid, salt)
        real_token = site_specific_token(username, "site-x", uid, salt)
        assert wrong_token != real_token

    def test_credential_hash_is_one_way(self, alice_creds):
        """credential_hash is SHA-256 — no inversion possible."""
        ch = credential_hash(*alice_creds)
        # The hash is a fixed-length hex digest
        assert len(ch) == 64
        # Obviously we can't write a test that "proves" irreversibility,
        # but we verify it's a proper hash output (no plaintext leakage).
        assert alice_creds[0] not in ch
        assert alice_creds[1] not in ch


# =========================================================================
# 6. Site cannot derive tokens for other sites
# =========================================================================

class TestSiteCannotDeriveOtherTokens:
    """A site receives a Token containing only a verification_hash and
    site_id.  It never sees the raw site-specific token, the username,
    or the user_salt.  Therefore it cannot derive tokens for other sites."""

    def test_site_only_sees_verification_hash(self, alice_creds, alice_registered):
        username, _ = alice_creds
        uid, salt, _ = alice_registered
        kp = SigningKeyPair()

        tok = build_signed_token(username, "site-alpha", uid, salt, kp)

        # The Token object contains verification_hash and site_id —
        # NOT the raw token, username, user_id, or user_salt.
        assert tok.verification_hash is not None
        assert tok.site_id == "site-alpha"

        # The site cannot reverse the verification_hash to get the
        # site-specific token (SHA-256 preimage resistance).
        raw_token = site_specific_token(username, "site-alpha", uid, salt)
        assert tok.verification_hash == verification_hash(raw_token)
        # But verification_hash != raw_token
        assert tok.verification_hash != raw_token

    def test_site_cannot_compute_other_site_token(self, alice_creds, alice_registered):
        username, _ = alice_creds
        uid, salt, _ = alice_registered
        kp = SigningKeyPair()

        # Site-alpha gets this token:
        tok_alpha = build_signed_token(username, "site-alpha", uid, salt, kp)

        # Site-alpha knows:
        #   - tok_alpha.verification_hash
        #   - tok_alpha.site_id = "site-alpha"
        #   - tok_alpha.public_key
        # Site-alpha does NOT know: username, user_id, user_salt

        # Therefore site-alpha cannot compute the token for site-beta.
        real_token_beta = site_specific_token(username, "site-beta", uid, salt)
        real_vh_beta = verification_hash(real_token_beta)

        # The only thing site-alpha could try is using its own verification_hash
        # as an input, which produces a completely different result.
        fake_attempt = verification_hash(tok_alpha.verification_hash)
        assert fake_attempt != real_vh_beta

    def test_routing_keys_also_unlinkable_across_sites(self, alice_creds, alice_registered):
        """Even routing keys for the same user differ per site."""
        username, _ = alice_creds
        uid, salt, _ = alice_registered

        rk_a = routing_key(username, "site-alpha", uid, salt)
        rk_b = routing_key(username, "site-beta", uid, salt)
        assert rk_a != rk_b


# =========================================================================
# Additional edge-case tests
# =========================================================================

class TestRegistrationAndAuth:
    def test_duplicate_registration_raises(self, sso, alice_creds):
        ch = credential_hash(*alice_creds)
        sso.register(ch)
        with pytest.raises(ValueError, match="already registered"):
            sso.register(ch)

    def test_unknown_credential_hash_raises(self, sso):
        with pytest.raises(KeyError, match="unknown"):
            sso.authenticate("nonexistent")

    def test_session_token_is_unique(self, sso, alice_creds):
        ch = credential_hash(*alice_creds)
        sso.register(ch)
        _, _, s1 = sso.authenticate(ch)
        _, _, s2 = sso.authenticate(ch)
        assert s1 != s2  # each authentication creates a fresh session


class TestTokenFields:
    def test_token_contains_required_fields(self, alice_creds, alice_registered):
        username, _ = alice_creds
        uid, salt, _ = alice_registered
        kp = SigningKeyPair()

        tok = build_signed_token(
            username, "my-site", uid, salt, kp,
            proof_of_human_score=0.95,
        )
        assert tok.verification_hash and len(tok.verification_hash) == 64
        assert tok.site_id == "my-site"
        assert tok.timestamp > 0
        assert tok.proof_of_human_score == 0.95
        assert tok.verify_signature()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

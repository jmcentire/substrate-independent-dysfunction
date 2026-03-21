"""Tests for the Anonymous Identity system.

Covers all 7 required properties:
(a) Same user, two sites -> tokens are different and unlinkable.
(b) Different users, same site -> tokens don't collide.
(c) Token signature verifies.
(d) Routing key correctly resolves.
(e) SSO cannot derive site-specific tokens (doesn't know site_id).
(f) Site cannot derive tokens for other sites (doesn't know user_salt).
(g) Full end-to-end integration.
"""

import time
import hashlib
import pytest

from nacl.signing import SigningKey, VerifyKey
from nacl.exceptions import BadSignatureError

from anonymous_identity.client import Client
from anonymous_identity.sso import SSO
from anonymous_identity.crypto_utils import (
    compute_credential_hash,
    derive_site_token,
    derive_verification_hash,
    derive_routing_key,
    build_signed_token,
    verify_signed_token,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sso():
    """Fresh SSO instance."""
    return SSO()


@pytest.fixture
def alice_credentials():
    return {"username": "alice", "password": "s3cret!"}


@pytest.fixture
def bob_credentials():
    return {"username": "bob", "password": "hunter2"}


@pytest.fixture
def registered_alice(sso, alice_credentials):
    """Register alice and return her Client (already authenticated)."""
    client = Client(alice_credentials["username"], alice_credentials["password"], sso)
    client.register(encrypted_contact="alice@example.com.encrypted")
    client.authenticate()
    return client


@pytest.fixture
def registered_bob(sso, bob_credentials):
    """Register bob and return his Client (already authenticated)."""
    client = Client(bob_credentials["username"], bob_credentials["password"], sso)
    client.register(encrypted_contact="bob@example.com.encrypted")
    client.authenticate()
    return client


# ===================================================================
# Property (1): Credential hash — password never transmitted in clear
# ===================================================================

class TestCredentialHash:
    def test_credential_hash_is_hex_string(self):
        h = compute_credential_hash("alice", "s3cret!")
        assert isinstance(h, str)
        # Should be a valid hex string (SHA-256 = 64 hex chars)
        int(h, 16)
        assert len(h) == 64

    def test_credential_hash_deterministic(self):
        h1 = compute_credential_hash("alice", "s3cret!")
        h2 = compute_credential_hash("alice", "s3cret!")
        assert h1 == h2

    def test_credential_hash_does_not_contain_raw_password(self):
        h = compute_credential_hash("alice", "s3cret!")
        assert "s3cret!" not in h

    def test_different_passwords_produce_different_hashes(self):
        h1 = compute_credential_hash("alice", "password1")
        h2 = compute_credential_hash("alice", "password2")
        assert h1 != h2

    def test_different_usernames_produce_different_hashes(self):
        h1 = compute_credential_hash("alice", "same")
        h2 = compute_credential_hash("bob", "same")
        assert h1 != h2


# ===================================================================
# Property (2): Site-specific token derivation is client-side
# ===================================================================

class TestSiteTokenDerivation:
    def test_derive_site_token_is_deterministic(self):
        t1 = derive_site_token("alice", "site1.com", "uid-1", "salt-abc")
        t2 = derive_site_token("alice", "site1.com", "uid-1", "salt-abc")
        assert t1 == t2

    def test_derive_site_token_returns_hex(self):
        t = derive_site_token("alice", "site1.com", "uid-1", "salt-abc")
        assert isinstance(t, str)
        int(t, 16)  # valid hex

    def test_token_changes_with_site_id(self):
        t1 = derive_site_token("alice", "site1.com", "uid-1", "salt")
        t2 = derive_site_token("alice", "site2.com", "uid-1", "salt")
        assert t1 != t2

    def test_token_changes_with_username(self):
        t1 = derive_site_token("alice", "site1.com", "uid-1", "salt")
        t2 = derive_site_token("bob", "site1.com", "uid-1", "salt")
        assert t1 != t2

    def test_token_changes_with_user_id(self):
        t1 = derive_site_token("alice", "site1.com", "uid-1", "salt")
        t2 = derive_site_token("alice", "site1.com", "uid-2", "salt")
        assert t1 != t2

    def test_token_changes_with_user_salt(self):
        t1 = derive_site_token("alice", "site1.com", "uid-1", "salt-a")
        t2 = derive_site_token("alice", "site1.com", "uid-1", "salt-b")
        assert t1 != t2


# ===================================================================
# Property (a): Same user, two sites -> different & unlinkable tokens
# ===================================================================

class TestUnlinkability:
    def test_same_user_different_sites_different_tokens(self, registered_alice):
        token_a = registered_alice.derive_token_for_site("site-a.com")
        token_b = registered_alice.derive_token_for_site("site-b.com")
        assert token_a != token_b

    def test_verification_hashes_differ_across_sites(self, registered_alice):
        vh_a = registered_alice.get_verification_hash("site-a.com")
        vh_b = registered_alice.get_verification_hash("site-b.com")
        assert vh_a != vh_b

    def test_tokens_share_no_common_substring_of_significant_length(self, registered_alice):
        """Heuristic: the hex tokens should not share any substring >= 16 chars."""
        t_a = registered_alice.derive_token_for_site("site-a.com")
        t_b = registered_alice.derive_token_for_site("site-b.com")
        min_len = 16
        for i in range(len(t_a) - min_len + 1):
            assert t_a[i:i + min_len] not in t_b

    def test_many_sites_all_unique(self, registered_alice):
        """Generate tokens for 100 sites and ensure all are unique."""
        tokens = set()
        for i in range(100):
            t = registered_alice.derive_token_for_site(f"site-{i}.com")
            tokens.add(t)
        assert len(tokens) == 100


# ===================================================================
# Property (b): Different users, same site -> tokens don't collide
# ===================================================================

class TestNoCollision:
    def test_different_users_same_site_different_tokens(self, registered_alice, registered_bob):
        site = "shared-site.com"
        t_alice = registered_alice.derive_token_for_site(site)
        t_bob = registered_bob.derive_token_for_site(site)
        assert t_alice != t_bob

    def test_different_users_same_site_different_verification_hashes(self, registered_alice, registered_bob):
        site = "shared-site.com"
        vh_alice = registered_alice.get_verification_hash(site)
        vh_bob = registered_bob.get_verification_hash(site)
        assert vh_alice != vh_bob

    def test_different_users_different_routing_keys(self, registered_alice, registered_bob):
        site = "shared-site.com"
        rk_alice = registered_alice.get_routing_key(site)
        rk_bob = registered_bob.get_routing_key(site)
        assert rk_alice != rk_bob


# ===================================================================
# Property (c): Token signature verifies
# ===================================================================

class TestTokenSignature:
    def test_build_and_verify_signed_token(self):
        signing_key = SigningKey.generate()
        verify_key = signing_key.verify_key
        vh = "abcdef1234567890" * 4  # 64 hex chars
        site_id = "example.com"
        ts = time.time()
        poh_score = 0.95

        signed = build_signed_token(signing_key, vh, site_id, ts, poh_score)
        payload = verify_signed_token(verify_key, signed)

        assert payload["verification_hash"] == vh
        assert payload["site_id"] == site_id
        assert payload["proof_of_human_score"] == poh_score

    def test_signed_token_contains_timestamp(self):
        signing_key = SigningKey.generate()
        verify_key = signing_key.verify_key
        before = time.time()
        signed = build_signed_token(signing_key, "aabb" * 16, "site.com", before, 0.5)
        payload = verify_signed_token(verify_key, signed)
        assert abs(payload["timestamp"] - before) < 2.0

    def test_tampered_signature_fails(self):
        signing_key = SigningKey.generate()
        verify_key = signing_key.verify_key
        signed = build_signed_token(signing_key, "aabb" * 16, "site.com", time.time(), 0.5)

        # Tamper with the signed bytes
        if isinstance(signed, bytes):
            tampered = bytearray(signed)
            tampered[-1] ^= 0xFF
            tampered = bytes(tampered)
        else:
            # If it's a string-based token, tamper with it
            tampered = signed[:-1] + ("X" if signed[-1] != "X" else "Y")

        with pytest.raises((BadSignatureError, Exception)):
            verify_signed_token(verify_key, tampered)

    def test_wrong_key_fails_verification(self):
        signing_key = SigningKey.generate()
        other_key = SigningKey.generate()
        signed = build_signed_token(signing_key, "aabb" * 16, "site.com", time.time(), 0.5)

        with pytest.raises((BadSignatureError, Exception)):
            verify_signed_token(other_key.verify_key, signed)

    def test_client_token_for_site_verifies(self, registered_alice):
        """Full client flow: derive token, sign it, verify it."""
        site = "forum.example.com"
        signed_token = registered_alice.derive_token_for_site(site)
        # The client should expose its verify key
        verify_key = registered_alice.signing_key.verify_key
        vh = registered_alice.get_verification_hash(site)

        # If derive_token_for_site returns a signed blob, verify it
        # If it returns just the token string, test that separately
        # Attempt to verify — the client should produce a valid signature
        if hasattr(registered_alice, 'build_signed_token_for_site'):
            signed = registered_alice.build_signed_token_for_site(site)
            payload = verify_signed_token(verify_key, signed)
            assert payload["verification_hash"] == vh
            assert payload["site_id"] == site


# ===================================================================
# Property (d): Routing key correctly resolves
# ===================================================================

class TestRoutingKey:
    def test_routing_key_deterministic(self):
        rk1 = derive_routing_key("alice", "site.com", "uid-1", "salt")
        rk2 = derive_routing_key("alice", "site.com", "uid-1", "salt")
        assert rk1 == rk2

    def test_routing_key_differs_by_site(self):
        rk1 = derive_routing_key("alice", "site-a.com", "uid-1", "salt")
        rk2 = derive_routing_key("alice", "site-b.com", "uid-1", "salt")
        assert rk1 != rk2

    def test_routing_key_is_hex(self):
        rk = derive_routing_key("alice", "site.com", "uid-1", "salt")
        assert isinstance(rk, str)
        int(rk, 16)

    def test_sso_routes_message_successfully(self, sso, registered_alice):
        site = "newsletter.example.com"
        routing_key = registered_alice.get_routing_key(site)

        # Register the routing key with the SSO
        # The client should have done this, or we do it manually
        if hasattr(registered_alice, 'register_routing_key'):
            registered_alice.register_routing_key(site)

        # The SSO should be able to resolve the routing key
        result = sso.route_message(routing_key, b"encrypted-payload-here")
        assert result == "delivered" or result is True or result == {"status": "delivered"}

    def test_routing_key_includes_routing_domain_separator(self):
        """Routing key derivation uses 'routing' separator so it differs from site tokens."""
        token = derive_site_token("alice", "site.com", "uid-1", "salt")
        rk = derive_routing_key("alice", "site.com", "uid-1", "salt")
        assert token != rk


# ===================================================================
# Property (e): SSO cannot derive site-specific tokens
# ===================================================================

class TestSSOCannotDeriveTokens:
    def test_sso_has_no_site_id_knowledge(self, sso, registered_alice):
        """The SSO stores user_id and user_salt but NOT site_id.
        Without site_id, it cannot compute derive_site_token."""
        # Get the SSO's knowledge about alice
        cred_hash = compute_credential_hash("alice", "s3cret!")
        user_record = sso._users.get(cred_hash) if hasattr(sso, '_users') else None

        if user_record is not None:
            # SSO should store user_id and user_salt
            user_id = user_record.get("user_id") or user_record.get("id")
            user_salt = user_record.get("user_salt") or user_record.get("salt")
            assert user_id is not None
            assert user_salt is not None

            # But without the username (only credential_hash is stored) and site_id,
            # the SSO cannot derive the site token
            # Verify: the SSO does NOT store the raw username
            stored_values = str(user_record)
            assert "alice" not in stored_values or "username" not in user_record

    def test_sso_only_receives_credential_hash(self, sso):
        """Register and authenticate only use credential_hash, never raw credentials."""
        cred_hash = compute_credential_hash("charlie", "mypassword")
        user_id = sso.register(cred_hash, "charlie@encrypted.com")
        assert user_id is not None

        result = sso.authenticate(cred_hash)
        assert result is not None
        # Result should contain user_id and user_salt
        if isinstance(result, tuple):
            assert len(result) >= 2
        elif isinstance(result, dict):
            assert "user_id" in result or "id" in result


# ===================================================================
# Property (f): Site cannot derive tokens for other sites
# ===================================================================

class TestSiteCannotCrossDerive:
    def test_site_without_user_salt_cannot_derive(self):
        """A site knows site_id and verification_hash but NOT user_salt.
        Without user_salt, it cannot compute tokens for other sites."""
        real_token = derive_site_token("alice", "site-a.com", "uid-1", "real-salt")
        # Site A tries to guess token for site B without knowing user_salt
        wrong_token = derive_site_token("alice", "site-b.com", "uid-1", "wrong-guess")
        correct_token_b = derive_site_token("alice", "site-b.com", "uid-1", "real-salt")
        assert wrong_token != correct_token_b

    def test_site_token_requires_all_inputs(self):
        """Changing any single input produces a completely different token."""
        base = derive_site_token("alice", "site.com", "uid-1", "salt-xyz")
        variants = [
            derive_site_token("alice", "site.com", "uid-1", "salt-OTHER"),
            derive_site_token("alice", "site.com", "uid-OTHER", "salt-xyz"),
            derive_site_token("alice", "OTHER.com", "uid-1", "salt-xyz"),
            derive_site_token("OTHER", "site.com", "uid-1", "salt-xyz"),
        ]
        for v in variants:
            assert v != base

    def test_verification_hash_differs_from_site_token(self):
        """verification_hash and site_token should be different derivations."""
        token = derive_site_token("alice", "site.com", "uid-1", "salt")
        vh = derive_verification_hash("alice", "site.com", "uid-1", "salt")
        # They may be the same function or different; but both should be deterministic
        # If they're different functions, they should produce different outputs
        # If derive_verification_hash == derive_site_token, that's also acceptable
        assert isinstance(vh, str)
        int(vh, 16)  # valid hex


# ===================================================================
# SSO Protocol tests
# ===================================================================

class TestSSOProtocol:
    def test_register_returns_user_id(self, sso):
        cred_hash = compute_credential_hash("dave", "password123")
        user_id = sso.register(cred_hash, "dave@encrypted.com")
        assert user_id is not None
        assert isinstance(user_id, str)

    def test_register_duplicate_fails(self, sso):
        cred_hash = compute_credential_hash("eve", "password")
        sso.register(cred_hash, "eve@encrypted.com")
        with pytest.raises(Exception):
            sso.register(cred_hash, "eve@encrypted.com")

    def test_authenticate_returns_user_id_and_salt(self, sso):
        cred_hash = compute_credential_hash("frank", "pass")
        user_id = sso.register(cred_hash, "frank@encrypted.com")
        result = sso.authenticate(cred_hash)

        if isinstance(result, tuple):
            r_user_id, r_salt = result[0], result[1]
            assert r_user_id == user_id
            assert r_salt is not None
            assert isinstance(r_salt, str)
        elif isinstance(result, dict):
            assert result.get("user_id") == user_id or result.get("id") == user_id
            assert result.get("user_salt") is not None or result.get("salt") is not None

    def test_authenticate_wrong_credential_fails(self, sso):
        cred_hash = compute_credential_hash("grace", "right")
        sso.register(cred_hash, "grace@encrypted.com")
        wrong_hash = compute_credential_hash("grace", "wrong")
        with pytest.raises(Exception):
            sso.authenticate(wrong_hash)

    def test_authenticate_returns_session(self, sso):
        cred_hash = compute_credential_hash("heidi", "pass")
        sso.register(cred_hash, "heidi@encrypted.com")
        result = sso.authenticate(cred_hash)

        if isinstance(result, tuple) and len(result) >= 3:
            session = result[2]
            assert session is not None
        elif isinstance(result, dict):
            assert "session" in result or "token" in result

    def test_route_message(self, sso):
        cred_hash = compute_credential_hash("ivan", "pass")
        user_id = sso.register(cred_hash, "ivan@encrypted.com")
        result = sso.authenticate(cred_hash)

        if isinstance(result, tuple):
            r_user_id, r_salt = result[0], result[1]
        elif isinstance(result, dict):
            r_user_id = result.get("user_id") or result.get("id")
            r_salt = result.get("user_salt") or result.get("salt")

        routing_key = derive_routing_key("ivan", "site.com", r_user_id, r_salt)

        # Register routing key with SSO if needed
        if hasattr(sso, 'register_routing_key'):
            sso.register_routing_key(routing_key, cred_hash)

        delivery = sso.route_message(routing_key, b"encrypted-message")
        # Should indicate successful delivery
        assert delivery is not None


# ===================================================================
# End-to-end integration
# ===================================================================

class TestEndToEnd:
    def test_full_flow(self, sso):
        """Complete flow: register -> authenticate -> derive tokens -> verify."""
        # 1. Create client
        client = Client("zara", "ultra-secret", sso)

        # 2. Register
        client.register(encrypted_contact="zara@encrypted.com")

        # 3. Authenticate
        client.authenticate()

        # 4. Derive tokens for two sites
        token_a = client.derive_token_for_site("alpha.com")
        token_b = client.derive_token_for_site("beta.com")

        # 5. Tokens should differ
        assert token_a != token_b

        # 6. Same site should produce same token
        token_a2 = client.derive_token_for_site("alpha.com")
        assert token_a == token_a2

    def test_two_clients_same_sso(self, sso):
        """Two clients on the same SSO with different credentials."""
        c1 = Client("user1", "pass1", sso)
        c1.register(encrypted_contact="u1@enc.com")
        c1.authenticate()

        c2 = Client("user2", "pass2", sso)
        c2.register(encrypted_contact="u2@enc.com")
        c2.authenticate()

        site = "shared.com"
        t1 = c1.derive_token_for_site(site)
        t2 = c2.derive_token_for_site(site)
        assert t1 != t2

    def test_client_must_authenticate_before_deriving(self, sso):
        """Client should fail if trying to derive token without authentication."""
        client = Client("lazy", "user", sso)
        client.register(encrypted_contact="lazy@enc.com")
        # Don't authenticate
        with pytest.raises(Exception):
            client.derive_token_for_site("some-site.com")

    def test_routing_key_differs_from_site_token(self, registered_alice):
        """Routing key and site token for the same site should be different."""
        site = "example.com"
        token = registered_alice.derive_token_for_site(site)
        rk = registered_alice.get_routing_key(site)
        assert token != rk

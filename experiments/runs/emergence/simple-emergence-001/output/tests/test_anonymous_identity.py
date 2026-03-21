"""Comprehensive tests for the Anonymous Identity system.

Tests cover all seven required properties:
  (a) Same user, two sites → tokens are different and unlinkable.
  (b) Different users, same site → tokens don't collide.
  (c) Token signature verifies.
  (d) Routing key correctly resolves.
  (e) SSO cannot derive site-specific tokens (doesn't know site_id).
  (f) Site cannot derive tokens for other sites (doesn't know user_salt).
  Plus additional structural and security tests.
"""

import hashlib
import time

import pytest
from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey
from nacl.encoding import HexEncoder

from anonymous_identity import (
    Client,
    SSO,
    SiteVerifier,
    hash_credential,
    derive_site_token,
    derive_routing_key,
    generate_ed25519_keypair,
    sign_token,
    verify_token_signature,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _setup_user(username="alice", password="s3cret", contact="alice@example.com"):
    """Register and authenticate a client against a fresh SSO."""
    sso = SSO()
    client = Client(username, password)
    client.register(sso, encrypted_contact=contact)
    client.authenticate(sso)
    return sso, client


# ── (a) Same user, two sites → different and unlinkable tokens ───────────────

class TestUnlinkability:
    def test_same_user_different_sites_produce_different_tokens(self):
        sso, client = _setup_user()
        token_a = client.get_site_token("site_alpha")
        token_b = client.get_site_token("site_beta")
        assert token_a != token_b

    def test_tokens_share_no_obvious_common_substring(self):
        """A rough statistical check: hex digests should share no long common
        substring (they're hashes, so effectively random)."""
        sso, client = _setup_user()
        t1 = client.get_site_token("site_1")
        t2 = client.get_site_token("site_2")
        # With SHA-256 hex digests, a common substring of length > 8 is
        # astronomically unlikely between two unrelated hashes.
        max_common = 0
        for i in range(len(t1)):
            for j in range(len(t2)):
                k = 0
                while i + k < len(t1) and j + k < len(t2) and t1[i + k] == t2[j + k]:
                    k += 1
                max_common = max(max_common, k)
        # 12 hex chars in common would be surprising (probability ~16^-12)
        assert max_common < 12, f"Suspiciously long common substring ({max_common} chars)"

    def test_routing_keys_also_differ_across_sites(self):
        sso, client = _setup_user()
        rk_a = client.get_routing_key("site_alpha")
        rk_b = client.get_routing_key("site_beta")
        assert rk_a != rk_b

    def test_token_and_routing_key_differ(self):
        sso, client = _setup_user()
        token = client.get_site_token("site_x")
        rk = client.get_routing_key("site_x")
        assert token != rk


# ── (b) Different users, same site → no collisions ──────────────────────────

class TestNoCollision:
    def test_different_users_same_site_different_tokens(self):
        sso = SSO()
        alice = Client("alice", "password_a")
        bob = Client("bob", "password_b")
        alice.register(sso, "alice@example.com")
        bob.register(sso, "bob@example.com")
        alice.authenticate(sso)
        bob.authenticate(sso)

        token_alice = alice.get_site_token("shared_site")
        token_bob = bob.get_site_token("shared_site")
        assert token_alice != token_bob

    def test_many_users_no_collision(self):
        sso = SSO()
        tokens = set()
        for i in range(100):
            c = Client(f"user_{i}", f"pass_{i}")
            c.register(sso, f"user_{i}@example.com")
            c.authenticate(sso)
            tokens.add(c.get_site_token("common_site"))
        assert len(tokens) == 100


# ── (c) Token signature verifies ────────────────────────────────────────────

class TestSignatureVerification:
    def test_valid_signature(self):
        sso, client = _setup_user()
        signed = client.create_signed_token("mysite", proof_of_human_score=0.95)
        payload = verify_token_signature(signed)
        assert payload["site_id"] == "mysite"
        assert payload["proof_of_human_score"] == 0.95
        assert payload["verification_hash"] == client.get_site_token("mysite")

    def test_tampered_payload_fails(self):
        sso, client = _setup_user()
        signed = client.create_signed_token("mysite")
        signed["payload"]["site_id"] = "evil_site"
        with pytest.raises(BadSignatureError):
            verify_token_signature(signed)

    def test_tampered_signature_fails(self):
        sso, client = _setup_user()
        signed = client.create_signed_token("mysite")
        # Flip a byte in the signature
        sig_bytes = bytearray.fromhex(signed["signature"])
        sig_bytes[0] ^= 0xFF
        signed["signature"] = sig_bytes.hex()
        with pytest.raises(BadSignatureError):
            verify_token_signature(signed)

    def test_wrong_public_key_fails(self):
        sso, client = _setup_user()
        signed = client.create_signed_token("mysite")
        # Replace public key with a different one
        other_sk = SigningKey.generate()
        signed["public_key"] = other_sk.verify_key.encode(encoder=HexEncoder).decode("ascii")
        with pytest.raises(BadSignatureError):
            verify_token_signature(signed)

    def test_site_verifier_accepts_valid_token(self):
        sso, client = _setup_user()
        ts = time.time()
        signed = client.create_signed_token("mysite", timestamp=ts)
        verifier = SiteVerifier("mysite")
        payload = verifier.verify(signed, current_time=ts)
        assert payload["verification_hash"] == client.get_site_token("mysite")

    def test_site_verifier_rejects_wrong_site(self):
        sso, client = _setup_user()
        signed = client.create_signed_token("site_a")
        verifier = SiteVerifier("site_b")
        with pytest.raises(ValueError, match="site_id"):
            verifier.verify(signed, current_time=time.time())

    def test_site_verifier_rejects_expired_token(self):
        sso, client = _setup_user()
        old_ts = time.time() - 600  # 10 minutes ago
        signed = client.create_signed_token("mysite", timestamp=old_ts)
        verifier = SiteVerifier("mysite", max_token_age=300)
        with pytest.raises(ValueError, match="expired"):
            verifier.verify(signed, current_time=time.time())

    def test_site_verifier_get_user_identifier(self):
        sso, client = _setup_user()
        ts = time.time()
        signed = client.create_signed_token("mysite", timestamp=ts)
        verifier = SiteVerifier("mysite")
        uid = verifier.get_user_identifier(signed, current_time=ts)
        assert uid == client.get_site_token("mysite")


# ── (d) Routing key correctly resolves ──────────────────────────────────────

class TestRouting:
    def test_routing_key_resolves_and_delivers(self):
        sso = SSO()
        client = Client("alice", "pass123")
        client.register(sso, encrypted_contact="alice@encrypted.com")
        client.authenticate(sso)

        rk = client.get_routing_key("site_gamma")
        # Client registers the routing key with SSO
        sso.register_routing_key(rk, client.credential_hash)

        # Site sends a message via routing key
        result = sso.route_message(rk, "encrypted_message_payload")
        assert result == "delivered"
        assert len(sso.delivered_messages) == 1
        assert sso.delivered_messages[0]["encrypted_payload"] == "encrypted_message_payload"
        assert sso.delivered_messages[0]["encrypted_contact"] == "alice@encrypted.com"

    def test_routing_unknown_key_fails(self):
        sso = SSO()
        with pytest.raises(ValueError, match="Unknown routing key"):
            sso.route_message("nonexistent_key", "payload")

    def test_multiple_routing_keys_per_user(self):
        sso = SSO()
        client = Client("bob", "bobpass")
        client.register(sso, encrypted_contact="bob@encrypted.com")
        client.authenticate(sso)

        rk_1 = client.get_routing_key("site_1")
        rk_2 = client.get_routing_key("site_2")
        assert rk_1 != rk_2

        sso.register_routing_key(rk_1, client.credential_hash)
        sso.register_routing_key(rk_2, client.credential_hash)

        assert sso.route_message(rk_1, "msg_1") == "delivered"
        assert sso.route_message(rk_2, "msg_2") == "delivered"
        assert len(sso.delivered_messages) == 2


# ── (e) SSO cannot derive site-specific tokens ──────────────────────────────

class TestSSOCannotDeriveTokens:
    def test_sso_has_no_site_id(self):
        """The SSO stores user_id, user_salt, credential_hash, and contact.
        It does NOT store or receive any site_id. Without site_id, the SSO
        cannot compute derive_site_token()."""
        sso = SSO()
        client = Client("alice", "secret")
        client.register(sso, "contact")
        client.authenticate(sso)

        record = sso.get_user_record(client.credential_hash)
        # The record contains only these fields — no site_id
        assert set(record.keys()) == {"user_id", "user_salt", "encrypted_contact"}

    def test_sso_cannot_reproduce_token_without_username_and_site_id(self):
        """Even with user_id and user_salt, the SSO cannot derive the token
        because it lacks the username (it only has credential_hash) and site_id."""
        sso = SSO()
        client = Client("alice", "secret")
        client.register(sso, "contact")
        client.authenticate(sso)

        expected_token = client.get_site_token("target_site")
        record = sso.get_user_record(client.credential_hash)

        # SSO tries brute force with a wrong username
        wrong_attempt = derive_site_token(
            "wrong_username", "target_site", record["user_id"], record["user_salt"]
        )
        assert wrong_attempt != expected_token

        # SSO doesn't even know site_id; trying with wrong site_id also fails
        wrong_site_attempt = derive_site_token(
            "alice",  # even if SSO somehow knew the username
            "wrong_site",
            record["user_id"],
            record["user_salt"],
        )
        assert wrong_site_attempt != expected_token


# ── (f) Site cannot derive tokens for other sites ───────────────────────────

class TestSiteCannotDeriveOtherTokens:
    def test_site_lacks_user_salt(self):
        """A site receives only the signed token (verification_hash, site_id,
        timestamp, proof_of_human_score) and the public key. It does NOT
        receive user_salt or username."""
        sso, client = _setup_user()
        ts = time.time()
        signed = client.create_signed_token("site_a", timestamp=ts)

        # Everything the site sees:
        site_info = set(signed["payload"].keys()) | {"signature", "public_key"}
        assert "user_salt" not in site_info
        assert "username" not in site_info
        assert "user_id" not in site_info

    def test_site_cannot_compute_token_for_another_site(self):
        """Without user_salt, a site cannot compute the token for another site."""
        sso, client = _setup_user()
        real_token_b = client.get_site_token("site_b")

        # Site A knows: verification_hash for site_a, site_id="site_a"
        # Site A tries to guess the token for site_b
        # It doesn't have username, user_id, or user_salt — it simply can't
        # compute SHA-256(username || "site_b" || user_id || user_salt)

        # Even if site_a somehow extracted partial info, without user_salt:
        guessed = derive_site_token("alice", "site_b", client.user_id, "wrong_salt")
        assert guessed != real_token_b

    def test_different_sites_see_different_identifiers(self):
        """Two sites verifying tokens from the same user see completely
        different verification_hashes."""
        sso, client = _setup_user()
        ts = time.time()

        token_a = client.create_signed_token("site_a", timestamp=ts)
        token_b = client.create_signed_token("site_b", timestamp=ts)

        verifier_a = SiteVerifier("site_a")
        verifier_b = SiteVerifier("site_b")

        id_a = verifier_a.get_user_identifier(token_a, current_time=ts)
        id_b = verifier_b.get_user_identifier(token_b, current_time=ts)

        assert id_a != id_b


# ── Property 1: Credential hash ─────────────────────────────────────────────

class TestCredentialHash:
    def test_credential_hash_is_sha256_of_username_password(self):
        expected = hashlib.sha256(b"alicesecret").hexdigest()
        assert hash_credential("alice", "secret") == expected

    def test_sso_never_sees_password(self):
        """The SSO only receives the credential_hash, never the password."""
        sso = SSO()
        client = Client("alice", "my_password")
        client.register(sso, "contact")
        # The SSO stores the credential_hash as a key
        record = sso.get_user_record(client.credential_hash)
        assert record is not None
        # No password stored anywhere in the record
        for v in record.values():
            if isinstance(v, str):
                assert "my_password" not in v


# ── Property 6: SSO protocol ────────────────────────────────────────────────

class TestSSOProtocol:
    def test_register_returns_user_id(self):
        sso = SSO()
        uid = sso.register("cred_hash_123", "encrypted_contact")
        assert isinstance(uid, str)
        assert len(uid) > 0

    def test_register_duplicate_fails(self):
        sso = SSO()
        sso.register("cred_hash", "contact")
        with pytest.raises(ValueError, match="already registered"):
            sso.register("cred_hash", "contact2")

    def test_authenticate_returns_required_fields(self):
        sso = SSO()
        sso.register("cred_hash", "contact")
        result = sso.authenticate("cred_hash")
        assert "user_id" in result
        assert "user_salt" in result
        assert "session" in result

    def test_authenticate_invalid_credentials_fails(self):
        sso = SSO()
        with pytest.raises(ValueError, match="Invalid credentials"):
            sso.authenticate("nonexistent")

    def test_route_message_returns_delivered(self):
        sso = SSO()
        sso.register("cred", "contact")
        sso.register_routing_key("rk_123", "cred")
        assert sso.route_message("rk_123", "payload") == "delivered"


# ── End-to-end integration ──────────────────────────────────────────────────

class TestEndToEnd:
    def test_full_flow(self):
        """Complete flow: register → authenticate → create token → verify → route."""
        sso = SSO()

        # 1. Client registers
        client = Client("charlie", "ch@rlie_pass!")
        uid = client.register(sso, encrypted_contact="charlie@encrypted.io")
        assert uid is not None

        # 2. Client authenticates
        user_id, user_salt, session = client.authenticate(sso)
        assert user_id == uid
        assert user_salt is not None
        assert session is not None

        # 3. Client creates signed token for a site
        ts = time.time()
        signed = client.create_signed_token(
            "forum.example.com", proof_of_human_score=0.99, timestamp=ts
        )

        # 4. Site verifies the token
        verifier = SiteVerifier("forum.example.com")
        payload = verifier.verify(signed, current_time=ts)
        assert payload["verification_hash"] == client.get_site_token("forum.example.com")
        assert payload["site_id"] == "forum.example.com"
        assert payload["proof_of_human_score"] == 0.99

        # 5. Site sends a message via routing key
        rk = client.get_routing_key("forum.example.com")
        sso.register_routing_key(rk, client.credential_hash)
        result = sso.route_message(rk, "Welcome to the forum!")
        assert result == "delivered"

    def test_two_users_full_isolation(self):
        """Two users on the same site are completely isolated."""
        sso = SSO()

        alice = Client("alice", "alice_pass")
        alice.register(sso, "alice_contact")
        alice.authenticate(sso)

        bob = Client("bob", "bob_pass")
        bob.register(sso, "bob_contact")
        bob.authenticate(sso)

        site = "shared-site.com"
        ts = time.time()

        token_a = alice.create_signed_token(site, timestamp=ts)
        token_b = bob.create_signed_token(site, timestamp=ts)

        verifier = SiteVerifier(site)
        id_a = verifier.get_user_identifier(token_a, current_time=ts)
        id_b = verifier.get_user_identifier(token_b, current_time=ts)

        assert id_a != id_b
        # And the public keys are different (different Ed25519 keypairs)
        assert token_a["public_key"] != token_b["public_key"]

"""
Comprehensive tests for the Anonymous Identity system.

Test matrix (from the specification):
    (a) Same user, two sites → tokens are different and unlinkable.
    (b) Different users, same site → tokens don't collide.
    (c) Token signature verifies.
    (d) Routing key correctly resolves.
    (e) SSO cannot derive site-specific tokens (doesn't know site_id).
    (f) Site cannot derive tokens for other sites (doesn't know user_salt).
"""

import json
import time
import hashlib

import pytest
from nacl.signing import SigningKey, VerifyKey
from nacl.exceptions import BadSignatureError

from anonymous_identity.crypto import hash_concat, generate_ed25519_keypair
from anonymous_identity.client import Client
from anonymous_identity.sso import SSOServer
from anonymous_identity.site import SiteVerifier


# ─── Helpers ──────────────────────────────────────────────────────────

def full_registration_flow(sso, username, password, encrypted_contact="enc:alice@mail"):
    """Register a client and authenticate, returning a ready-to-use Client."""
    client = Client(username, password)
    user_id = sso.register(client.credential_hash, encrypted_contact)
    uid, salt, session = sso.authenticate(client.credential_hash)
    client.receive_auth(uid, salt, session)
    return client


# ═══════════════════════════════════════════════════════════════════════
# Property 1 — Credential hash
# ═══════════════════════════════════════════════════════════════════════

class TestCredentialHash:
    def test_hash_is_deterministic(self):
        c1 = Client("alice", "pw123")
        c2 = Client("alice", "pw123")
        assert c1.credential_hash == c2.credential_hash

    def test_different_passwords_give_different_hashes(self):
        c1 = Client("alice", "pw123")
        c2 = Client("alice", "pw456")
        assert c1.credential_hash != c2.credential_hash

    def test_hash_matches_manual_sha256(self):
        c = Client("alice", "pw123")
        expected = hashlib.sha256(b"alicepw123").hexdigest()
        assert c.credential_hash == expected

    def test_password_not_stored_in_hash(self):
        """The hash is a hex digest — no substring of the password appears."""
        c = Client("alice", "supersecretpassword")
        assert "supersecretpassword" not in c.credential_hash


# ═══════════════════════════════════════════════════════════════════════
# Property 2 — Site-specific token derivation
# ═══════════════════════════════════════════════════════════════════════

class TestSiteToken:
    def test_token_is_deterministic(self):
        sso = SSOServer()
        client = full_registration_flow(sso, "alice", "pw")
        t1 = client.derive_site_token("site_a")
        t2 = client.derive_site_token("site_a")
        assert t1 == t2

    def test_token_changes_with_site(self):
        sso = SSOServer()
        client = full_registration_flow(sso, "alice", "pw")
        ta = client.derive_site_token("site_a")
        tb = client.derive_site_token("site_b")
        assert ta != tb

    def test_token_requires_auth(self):
        client = Client("alice", "pw")
        with pytest.raises(AssertionError, match="Must authenticate"):
            client.derive_site_token("site_a")


# ═══════════════════════════════════════════════════════════════════════
# (a) Same user, two sites → tokens are different and unlinkable
# ═══════════════════════════════════════════════════════════════════════

class TestUnlinkability:
    def test_same_user_different_sites_different_tokens(self):
        sso = SSOServer()
        client = full_registration_flow(sso, "alice", "pw")
        ta = client.derive_site_token("site_a")
        tb = client.derive_site_token("site_b")
        assert ta != tb

    def test_tokens_share_no_common_prefix(self):
        """Hex tokens should not share a long common prefix (probabilistic)."""
        sso = SSOServer()
        client = full_registration_flow(sso, "alice", "pw")
        ta = client.derive_site_token("site_a")
        tb = client.derive_site_token("site_b")
        common = 0
        for a, b in zip(ta, tb):
            if a == b:
                common += 1
            else:
                break
        # SHA-256 outputs: a shared prefix of ≥ 8 hex chars is astronomically unlikely
        assert common < 8, "Tokens share suspiciously long common prefix"

    def test_unlinkability_no_algebraic_relation(self):
        """
        Verify that XOR / subtraction of token bytes looks random.
        (Not a formal proof, but a sanity check.)
        """
        sso = SSOServer()
        client = full_registration_flow(sso, "alice", "pw")
        ta = bytes.fromhex(client.derive_site_token("site_a"))
        tb = bytes.fromhex(client.derive_site_token("site_b"))
        xor = bytes(a ^ b for a, b in zip(ta, tb))
        # Should have high entropy — at least 20 of 32 bytes nonzero
        nonzero = sum(1 for b in xor if b != 0)
        assert nonzero >= 20

    def test_routing_keys_also_unlinkable(self):
        sso = SSOServer()
        client = full_registration_flow(sso, "alice", "pw")
        rk_a = client.derive_routing_key("site_a")
        rk_b = client.derive_routing_key("site_b")
        assert rk_a != rk_b


# ═══════════════════════════════════════════════════════════════════════
# (b) Different users, same site → tokens don't collide
# ═══════════════════════════════════════════════════════════════════════

class TestNoCollision:
    def test_different_users_same_site(self):
        sso = SSOServer()
        alice = full_registration_flow(sso, "alice", "pw1")
        bob = full_registration_flow(sso, "bob", "pw2")
        assert alice.derive_site_token("site_x") != bob.derive_site_token("site_x")

    def test_many_users_same_site_no_collision(self):
        sso = SSOServer()
        tokens = set()
        for i in range(100):
            c = full_registration_flow(sso, f"user_{i}", f"pw_{i}")
            tokens.add(c.derive_site_token("popular_site"))
        assert len(tokens) == 100  # all unique


# ═══════════════════════════════════════════════════════════════════════
# (c) Token signature verifies
# ═══════════════════════════════════════════════════════════════════════

class TestSignedToken:
    def test_signature_verifies(self):
        sso = SSOServer()
        client = full_registration_flow(sso, "alice", "pw")
        signed = client.build_signed_token("site_a", proof_of_human_score=0.95)

        vk = VerifyKey(bytes.fromhex(signed["public_key"]))
        # Should not raise
        vk.verify(
            signed["payload"].encode("utf-8"),
            bytes.fromhex(signed["signature"]),
        )

    def test_site_verifier_accepts_valid_token(self):
        sso = SSOServer()
        client = full_registration_flow(sso, "alice", "pw")
        signed = client.build_signed_token("site_a")

        site = SiteVerifier("site_a")
        result = site.verify_token(signed)
        assert result is not None
        assert result["verification_hash"] == client.derive_site_token("site_a")
        assert result["site_id"] == "site_a"

    def test_site_rejects_wrong_site_id(self):
        sso = SSOServer()
        client = full_registration_flow(sso, "alice", "pw")
        signed = client.build_signed_token("site_a")

        wrong_site = SiteVerifier("site_b")
        assert wrong_site.verify_token(signed) is None

    def test_tampered_payload_rejected(self):
        sso = SSOServer()
        client = full_registration_flow(sso, "alice", "pw")
        signed = client.build_signed_token("site_a")

        # Tamper with the payload
        payload = json.loads(signed["payload"])
        payload["proof_of_human_score"] = 9999
        signed["payload"] = json.dumps(payload, sort_keys=True)

        site = SiteVerifier("site_a")
        assert site.verify_token(signed) is None

    def test_tampered_signature_rejected(self):
        sso = SSOServer()
        client = full_registration_flow(sso, "alice", "pw")
        signed = client.build_signed_token("site_a")

        # Flip a byte in the signature
        sig_bytes = bytearray(bytes.fromhex(signed["signature"]))
        sig_bytes[0] ^= 0xFF
        signed["signature"] = sig_bytes.hex()

        site = SiteVerifier("site_a")
        assert site.verify_token(signed) is None

    def test_payload_contains_expected_fields(self):
        sso = SSOServer()
        client = full_registration_flow(sso, "alice", "pw")
        ts = time.time()
        signed = client.build_signed_token("site_a", proof_of_human_score=0.88, timestamp=ts)
        payload = json.loads(signed["payload"])
        assert payload["verification_hash"] == client.derive_site_token("site_a")
        assert payload["site_id"] == "site_a"
        assert payload["timestamp"] == ts
        assert payload["proof_of_human_score"] == 0.88

    def test_verification_hash_is_persistent_site_local_id(self):
        """Same client, same site → same verification_hash across sessions."""
        sso = SSOServer()
        client = full_registration_flow(sso, "alice", "pw")
        t1 = client.build_signed_token("site_a")
        t2 = client.build_signed_token("site_a")
        p1 = json.loads(t1["payload"])
        p2 = json.loads(t2["payload"])
        assert p1["verification_hash"] == p2["verification_hash"]


# ═══════════════════════════════════════════════════════════════════════
# (d) Routing key correctly resolves
# ═══════════════════════════════════════════════════════════════════════

class TestRouting:
    def test_routing_key_resolves(self):
        sso = SSOServer()
        client = full_registration_flow(sso, "alice", "pw", "enc:alice@mail.com")

        rk = client.derive_routing_key("site_a")
        sso.register_routing_key(rk, client.credential_hash)

        delivered = sso.route_message(rk, "encrypted: hello alice from site_a")
        assert delivered is True
        assert len(sso._delivered) == 1
        assert sso._delivered[0]["user_id"] == client.user_id
        assert sso._delivered[0]["encrypted_contact"] == "enc:alice@mail.com"
        assert sso._delivered[0]["encrypted_payload"] == "encrypted: hello alice from site_a"

    def test_unknown_routing_key_fails(self):
        sso = SSOServer()
        assert sso.route_message("nonexistent_key", "payload") is False

    def test_routing_keys_are_site_specific(self):
        sso = SSOServer()
        client = full_registration_flow(sso, "alice", "pw", "enc:alice@mail")
        rk_a = client.derive_routing_key("site_a")
        rk_b = client.derive_routing_key("site_b")
        assert rk_a != rk_b

        sso.register_routing_key(rk_a, client.credential_hash)
        # site_b's key is not registered, so should fail
        assert sso.route_message(rk_b, "payload") is False
        # site_a's key works
        assert sso.route_message(rk_a, "payload") is True

    def test_routing_key_different_from_site_token(self):
        sso = SSOServer()
        client = full_registration_flow(sso, "alice", "pw")
        token = client.derive_site_token("site_a")
        rk = client.derive_routing_key("site_a")
        assert token != rk  # different hash inputs ("routing" is included)


# ═══════════════════════════════════════════════════════════════════════
# (e) SSO cannot derive site-specific tokens
# ═══════════════════════════════════════════════════════════════════════

class TestSSOCannotDeriveTokens:
    def test_sso_has_no_site_ids(self):
        sso = SSOServer()
        _ = full_registration_flow(sso, "alice", "pw")
        assert sso.known_site_ids == []

    def test_sso_lacks_username_for_token_derivation(self):
        """
        The SSO stores credential_hash, user_id, user_salt — but NOT the
        plaintext username.  Without the username it cannot compute
        hash(username || site_id || user_id || user_salt).
        """
        sso = SSOServer()
        client = full_registration_flow(sso, "alice", "pw")
        rec = sso.get_user_record(client.credential_hash)

        # The SSO has these
        user_id = rec["user_id"]
        user_salt = rec["user_salt"]

        # But it does NOT have the plaintext username — only credential_hash.
        # Even if an SSO operator guesses a site_id, they can't form the
        # correct pre-image because "alice" is unknown to them.
        wrong_token = hash_concat(client.credential_hash, "site_a", user_id, user_salt)
        real_token = client.derive_site_token("site_a")
        assert wrong_token != real_token

    def test_sso_cannot_use_credential_hash_as_username(self):
        """credential_hash ≠ username, so substituting it gives wrong token."""
        sso = SSOServer()
        client = full_registration_flow(sso, "alice", "pw")
        rec = sso.get_user_record(client.credential_hash)

        # Try every plausible combination an SSO might attempt
        attempts = [
            hash_concat(client.credential_hash, "site_a", rec["user_id"], rec["user_salt"]),
            hash_concat(rec["user_id"], "site_a", rec["user_id"], rec["user_salt"]),
            hash_concat(rec["user_salt"], "site_a", rec["user_id"], rec["user_salt"]),
        ]
        real = client.derive_site_token("site_a")
        for attempt in attempts:
            assert attempt != real


# ═══════════════════════════════════════════════════════════════════════
# (f) Site cannot derive tokens for other sites
# ═══════════════════════════════════════════════════════════════════════

class TestSiteCannotDeriveOtherTokens:
    def test_site_lacks_user_salt(self):
        """
        A site sees: verification_hash, site_id, timestamp, proof_of_human_score,
        and the public key.  It does NOT have user_salt or the username.
        Without user_salt it cannot compute tokens for another site.
        """
        sso = SSOServer()
        client = full_registration_flow(sso, "alice", "pw")

        site_a_token = client.derive_site_token("site_a")
        site_b_token = client.derive_site_token("site_b")

        # site_a knows only: verification_hash (= site_a_token), "site_a"
        # It cannot derive site_b_token because it lacks username AND user_salt.
        # Try brute combinations with what site_a has:
        attempts = [
            hash_concat(site_a_token, "site_b"),
            hash_concat("site_a", "site_b", site_a_token),
            hash_concat(site_a_token, "site_b", "site_a"),
        ]
        for attempt in attempts:
            assert attempt != site_b_token

    def test_site_cannot_reverse_hash_to_get_salt(self):
        """
        SHA-256 is one-way; knowing hash(username||site_a||user_id||user_salt)
        doesn't reveal user_salt.
        """
        sso = SSOServer()
        client = full_registration_flow(sso, "alice", "pw")
        ta = client.derive_site_token("site_a")
        tb = client.derive_site_token("site_b")

        # The site cannot find any input that produces site_b's token
        # without knowing the secret components.
        assert ta != tb  # basic sanity
        # If site_a could somehow extract user_salt from ta, it could compute tb.
        # But hash_concat is SHA-256 — preimage resistance prevents this.

    def test_public_key_does_not_leak_salt(self):
        """The Ed25519 public key does not encode user_salt."""
        sso = SSOServer()
        client = full_registration_flow(sso, "alice", "pw")
        signed = client.build_signed_token("site_a")
        pk_hex = signed["public_key"]
        # user_salt is a random 32-byte hex string
        assert client.user_salt not in pk_hex


# ═══════════════════════════════════════════════════════════════════════
# SSO Protocol (Property 6)
# ═══════════════════════════════════════════════════════════════════════

class TestSSOProtocol:
    def test_register_returns_user_id(self):
        sso = SSOServer()
        uid = sso.register("cred_hash_1", "enc:contact")
        assert isinstance(uid, str) and len(uid) > 0

    def test_register_duplicate_raises(self):
        sso = SSOServer()
        sso.register("cred_hash_1", "enc:contact")
        with pytest.raises(ValueError):
            sso.register("cred_hash_1", "enc:contact2")

    def test_authenticate_returns_triple(self):
        sso = SSOServer()
        sso.register("ch", "enc:c")
        uid, salt, session = sso.authenticate("ch")
        assert isinstance(uid, str)
        assert isinstance(salt, str)
        assert isinstance(session, str)

    def test_authenticate_unknown_raises(self):
        sso = SSOServer()
        with pytest.raises(KeyError):
            sso.authenticate("unknown_hash")

    def test_route_message_returns_bool(self):
        sso = SSOServer()
        assert sso.route_message("no_such_key", "payload") is False


# ═══════════════════════════════════════════════════════════════════════
# End-to-end integration
# ═══════════════════════════════════════════════════════════════════════

class TestEndToEnd:
    def test_full_flow(self):
        """Complete flow: register → auth → derive token → verify at site → route message."""
        sso = SSOServer()

        # 1. Client registers
        alice = Client("alice", "s3cret")
        user_id = sso.register(alice.credential_hash, "enc:alice@example.com")

        # 2. Client authenticates
        uid, salt, session = sso.authenticate(alice.credential_hash)
        alice.receive_auth(uid, salt, session)
        assert alice.user_id == uid

        # 3. Client visits site_a
        signed_token = alice.build_signed_token("site_a", proof_of_human_score=0.97)
        site_a = SiteVerifier("site_a")
        result = site_a.verify_token(signed_token)
        assert result is not None
        assert result["verification_hash"] == alice.derive_site_token("site_a")
        assert result["proof_of_human_score"] == 0.97

        # 4. Client visits site_b — completely unlinkable
        signed_b = alice.build_signed_token("site_b")
        site_b = SiteVerifier("site_b")
        result_b = site_b.verify_token(signed_b)
        assert result_b is not None
        assert result_b["verification_hash"] != result["verification_hash"]

        # 5. site_a sends a message via routing
        rk_a = alice.derive_routing_key("site_a")
        sso.register_routing_key(rk_a, alice.credential_hash)
        assert sso.route_message(rk_a, "encrypted: welcome back alice") is True
        assert len(sso._delivered) == 1

    def test_two_users_full_flow(self):
        sso = SSOServer()

        alice = full_registration_flow(sso, "alice", "pw_a", "enc:alice@mail")
        bob = full_registration_flow(sso, "bob", "pw_b", "enc:bob@mail")

        site = SiteVerifier("forum.example.com")

        ta = alice.build_signed_token("forum.example.com")
        tb = bob.build_signed_token("forum.example.com")

        ra = site.verify_token(ta)
        rb = site.verify_token(tb)

        assert ra is not None and rb is not None
        assert ra["verification_hash"] != rb["verification_hash"]

        # Both are recognized
        assert site.has_user(ra["verification_hash"])
        assert site.has_user(rb["verification_hash"])

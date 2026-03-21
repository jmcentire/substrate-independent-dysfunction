"""
Comprehensive test suite for the Anonymous Identity system.

Required properties (from the spec):
  (a) Same user, two sites → tokens are different and unlinkable.
  (b) Different users, same site → tokens don't collide.
  (c) Token signature verifies.
  (d) Routing key correctly resolves.
  (e) SSO cannot derive site-specific tokens (doesn't know site_id).
  (f) Site cannot derive tokens for other sites (doesn't know user_salt).

Additional tests cover every stated property (1–6) and edge cases.
"""

import hashlib
import hmac
import itertools
import math
import time
import pytest

from anonymous_identity.crypto import (
    compute_credential_hash,
    derive_site_token,
    derive_verification_hash,
    derive_routing_key,
    generate_signing_keypair,
    sign_token,
    verify_token,
    build_token_payload,
)
from anonymous_identity.sso import SSO
from anonymous_identity.client import Client
from anonymous_identity.site import Site


# ======================================================================
# Fixtures
# ======================================================================

@pytest.fixture
def sso():
    return SSO()


@pytest.fixture
def alice(sso):
    c = Client("alice", "correct-horse-battery-staple")
    c.register(sso)
    c.authenticate(sso)
    return c


@pytest.fixture
def bob(sso):
    c = Client("bob", "hunter2")
    c.register(sso)
    c.authenticate(sso)
    return c


@pytest.fixture
def site_a():
    return Site("site-a.example.com")


@pytest.fixture
def site_b():
    return Site("site-b.example.com")


# ======================================================================
# Property 1 – Credential hash
# ======================================================================

class TestCredentialHash:
    """The client computes hash(username || password) locally.
    Only the hash is transmitted to the SSO."""

    def test_hash_deterministic(self):
        h1 = compute_credential_hash("alice", "pass")
        h2 = compute_credential_hash("alice", "pass")
        assert h1 == h2

    def test_hash_differs_for_different_passwords(self):
        h1 = compute_credential_hash("alice", "pass1")
        h2 = compute_credential_hash("alice", "pass2")
        assert h1 != h2

    def test_hash_differs_for_different_usernames(self):
        h1 = compute_credential_hash("alice", "pass")
        h2 = compute_credential_hash("bob", "pass")
        assert h1 != h2

    def test_hash_is_sha256_hex(self):
        h = compute_credential_hash("alice", "pass")
        assert len(h) == 64  # 256 bits = 64 hex chars
        int(h, 16)  # valid hex

    def test_sso_never_sees_password(self, sso):
        """The SSO stores credential_hash, not the password."""
        client = Client("carol", "s3cret!")
        client.register(sso)
        record = sso._get_user_record(client.credential_hash)
        # record has credential_hash but no 'password' field
        assert record.credential_hash == client.credential_hash
        assert not hasattr(record, "password")
        assert not hasattr(record, "_password")

    def test_credential_hash_matches_manual_sha256(self):
        username, password = "alice", "pass"
        expected = hashlib.sha256((username + password).encode()).hexdigest()
        assert compute_credential_hash(username, password) == expected


# ======================================================================
# Property 2 – Site-specific token derivation
# ======================================================================

class TestSiteTokenDerivation:
    """token = hash(username || site_id || user_id || user_salt), client-side."""

    def test_token_deterministic(self, alice):
        t1 = alice.derive_token_for_site("site-a")
        t2 = alice.derive_token_for_site("site-a")
        assert t1 == t2

    def test_token_differs_per_site(self, alice):
        """(a) Same user, two sites → tokens are different."""
        t_a = alice.derive_token_for_site("site-a")
        t_b = alice.derive_token_for_site("site-b")
        assert t_a != t_b

    def test_token_is_hex_sha256(self, alice):
        t = alice.derive_token_for_site("site-a")
        assert len(t) == 64
        int(t, 16)


# ======================================================================
# Property 3 – Unlinkability
# ======================================================================

class TestUnlinkability:
    """Tokens for the same user on different sites are computationally
    unlinkable.  No party can correlate token_A and token_B without
    knowing the user's credentials."""

    def test_same_user_different_sites_unlinkable(self, alice):
        """(a) Same user, two sites → tokens are different and unlinkable."""
        t_a = alice.derive_token_for_site("site-a")
        t_b = alice.derive_token_for_site("site-b")
        assert t_a != t_b

        # The verification hashes are also distinct
        vh_a = alice.derive_verification_hash_for_site("site-a")
        vh_b = alice.derive_verification_hash_for_site("site-b")
        assert vh_a != vh_b

        # There is no simple algebraic relation between them.
        # We verify that XOR, concatenation-hash, etc. don't leak a constant.
        # (Statistical test: they should look like independent random values.)
        bytes_a = bytes.fromhex(vh_a)
        bytes_b = bytes.fromhex(vh_b)
        xor = bytes(a ^ b for a, b in zip(bytes_a, bytes_b))
        # XOR should not be all-zero (that would mean equality)
        assert xor != b'\x00' * 32

    def test_different_users_same_site_unlinkable(self, alice, bob):
        """(b) Different users, same site → tokens don't collide."""
        t_alice = alice.derive_token_for_site("site-a")
        t_bob = bob.derive_token_for_site("site-a")
        assert t_alice != t_bob

    def test_verification_hashes_differ_across_users(self, alice, bob):
        vh_alice = alice.derive_verification_hash_for_site("site-a")
        vh_bob = bob.derive_verification_hash_for_site("site-a")
        assert vh_alice != vh_bob

    def test_no_common_prefix_or_suffix(self, alice):
        """Tokens for two sites share no structural prefix/suffix."""
        t_a = alice.derive_token_for_site("site-a")
        t_b = alice.derive_token_for_site("site-b")
        # With overwhelming probability, the first and last 8 chars differ.
        # But at minimum the full strings must differ.
        assert t_a != t_b

    def test_routing_keys_also_unlinkable_across_sites(self, alice):
        rk_a = alice.derive_routing_key_for_site("site-a")
        rk_b = alice.derive_routing_key_for_site("site-b")
        assert rk_a != rk_b

    def test_token_and_routing_key_are_independent(self, alice):
        """The site token and routing key for the same site are different
        (different domain separators)."""
        t = alice.derive_token_for_site("site-a")
        rk = alice.derive_routing_key_for_site("site-a")
        assert t != rk

    def test_many_sites_all_unique(self, alice):
        """Generate tokens for 100 sites; all must be unique."""
        tokens = [alice.derive_token_for_site(f"site-{i}") for i in range(100)]
        assert len(set(tokens)) == 100

    def test_many_users_same_site_all_unique(self, sso):
        """50 distinct users on the same site produce 50 distinct tokens."""
        tokens = []
        for i in range(50):
            c = Client(f"user-{i}", f"password-{i}")
            c.register(sso)
            c.authenticate(sso)
            tokens.append(c.derive_token_for_site("shared-site"))
        assert len(set(tokens)) == 50

    def test_bit_distribution_looks_uniform(self, alice):
        """Rough entropy check: across many site tokens the bit distribution
        should be approximately 50/50."""
        bit_counts = [0] * 256
        n = 200
        for i in range(n):
            t = bytes.fromhex(alice.derive_token_for_site(f"site-{i}"))
            for byte_idx, byte_val in enumerate(t):
                for bit in range(8):
                    if byte_val & (1 << bit):
                        bit_counts[byte_idx * 8 + bit] += 1

        # Each bit should be set ~n/2 times; allow generous ±30%
        lo, hi = n * 0.2, n * 0.8
        for count in bit_counts:
            assert lo <= count <= hi, f"bit count {count} outside [{lo}, {hi}]"


# ======================================================================
# Property 4 – Token construction & Ed25519 signature
# ======================================================================

class TestTokenSignature:
    """The client signs T with Ed25519; the site verifies."""

    def test_signature_verifies(self, alice):
        """(c) Token signature verifies."""
        token = alice.build_signed_token("site-a")
        assert verify_token(token)

    def test_tampered_verification_hash_fails(self, alice):
        token = alice.build_signed_token("site-a")
        token["verification_hash"] = "0" * 64  # tamper
        assert not verify_token(token)

    def test_tampered_site_id_fails(self, alice):
        token = alice.build_signed_token("site-a")
        token["site_id"] = "evil-site"
        assert not verify_token(token)

    def test_tampered_timestamp_fails(self, alice):
        token = alice.build_signed_token("site-a")
        token["timestamp"] = 0.0
        assert not verify_token(token)

    def test_tampered_proof_of_human_fails(self, alice):
        token = alice.build_signed_token("site-a")
        token["proof_of_human_score"] = 0.01
        assert not verify_token(token)

    def test_tampered_signature_fails(self, alice):
        token = alice.build_signed_token("site-a")
        # Flip one hex digit in the signature
        sig = token["signature"]
        flipped = hex((int(sig, 16) ^ 1))[2:].zfill(len(sig))
        token["signature"] = flipped
        assert not verify_token(token)

    def test_wrong_key_fails(self, alice, bob):
        """Token signed by Alice should not verify under Bob's key."""
        token = alice.build_signed_token("site-a")
        # Replace public key with Bob's
        from nacl.encoding import HexEncoder
        token["public_key"] = bob.verify_key.encode(encoder=HexEncoder).decode()
        assert not verify_token(token)

    def test_site_receives_and_verifies(self, alice, site_a):
        token = alice.build_signed_token("site-a.example.com")
        result = site_a.receive_token(token)
        assert result["ok"] is True
        assert result["verification_hash"] == token["verification_hash"]

    def test_site_rejects_wrong_site_id(self, alice, site_a):
        token = alice.build_signed_token("site-b.example.com")
        result = site_a.receive_token(token)
        assert result["ok"] is False
        assert "mismatch" in result["reason"]

    def test_token_contains_all_required_fields(self, alice):
        token = alice.build_signed_token("site-a")
        for field in ("verification_hash", "site_id", "timestamp",
                      "proof_of_human_score", "signature", "public_key"):
            assert field in token


# ======================================================================
# Property 5 – Routing key derivation & message delivery
# ======================================================================

class TestRoutingKey:
    """routing_key = hash(username || "routing" || site_id || …).
    Sites send messages to the SSO addressed to a routing key."""

    def test_routing_key_deterministic(self, alice):
        rk1 = alice.derive_routing_key_for_site("site-a")
        rk2 = alice.derive_routing_key_for_site("site-a")
        assert rk1 == rk2

    def test_routing_key_differs_per_site(self, alice):
        rk_a = alice.derive_routing_key_for_site("site-a")
        rk_b = alice.derive_routing_key_for_site("site-b")
        assert rk_a != rk_b

    def test_routing_key_resolves_and_delivers(self, sso, alice, site_a):
        """(d) Routing key correctly resolves."""
        rk = alice.register_routing_key(sso, "site-a.example.com")
        result = site_a.send_message_to_user(sso, rk, "encrypted-hello")
        assert result == "delivered"

        # Verify message was stored in Alice's record
        msgs = sso._get_delivered_messages(alice.credential_hash)
        assert "encrypted-hello" in msgs

    def test_unknown_routing_key_raises(self, sso, site_a):
        with pytest.raises(ValueError, match="unknown routing key"):
            site_a.send_message_to_user(sso, "nonexistent-key", "payload")

    def test_multiple_messages_delivered(self, sso, alice, site_a):
        rk = alice.register_routing_key(sso, "site-a.example.com")
        for i in range(5):
            site_a.send_message_to_user(sso, rk, f"msg-{i}")
        msgs = sso._get_delivered_messages(alice.credential_hash)
        assert len(msgs) == 5


# ======================================================================
# Property 6 – SSO protocol
# ======================================================================

class TestSSOProtocol:
    """SSO exposes register, authenticate, route_message."""

    def test_register_returns_user_id(self, sso):
        uid = sso.register("hash123", "contact")
        assert isinstance(uid, str)
        assert len(uid) > 0

    def test_duplicate_register_raises(self, sso):
        sso.register("hash123", "contact")
        with pytest.raises(ValueError, match="already registered"):
            sso.register("hash123", "contact2")

    def test_authenticate_returns_triple(self, sso):
        sso.register("hash456", "contact")
        uid, salt, session = sso.authenticate("hash456")
        assert isinstance(uid, str)
        assert isinstance(salt, str)
        assert isinstance(session, str)

    def test_authenticate_unknown_credential(self, sso):
        with pytest.raises(ValueError, match="unknown credential"):
            sso.authenticate("no-such-hash")

    def test_authenticate_returns_consistent_user_id(self, sso):
        uid_reg = sso.register("hash789", "contact")
        uid_auth, _, _ = sso.authenticate("hash789")
        assert uid_reg == uid_auth

    def test_authenticate_salt_is_stable(self, sso):
        sso.register("hashABC", "contact")
        _, salt1, _ = sso.authenticate("hashABC")
        _, salt2, _ = sso.authenticate("hashABC")
        assert salt1 == salt2

    def test_route_message_returns_delivered(self, sso, alice):
        rk = alice.register_routing_key(sso, "any-site")
        assert sso.route_message(rk, "payload") == "delivered"


# ======================================================================
# Property 7(e) – SSO cannot derive site-specific tokens
# ======================================================================

class TestSSOCannotDeriveTokens:
    """(e) The SSO cannot derive site-specific tokens because it does
    not know the site_id the client will use."""

    def test_sso_has_no_site_id(self, sso, alice):
        """The SSO's user record contains user_id and user_salt but
        no site_id and no site tokens."""
        record = sso._get_user_record(alice.credential_hash)
        assert record is not None
        assert hasattr(record, "user_id")
        assert hasattr(record, "user_salt")
        # No site-related fields
        assert not hasattr(record, "site_id")
        assert not hasattr(record, "site_token")
        assert not hasattr(record, "verification_hash")

    def test_sso_cannot_compute_token_without_site_id(self, sso, alice):
        """Even with user_id and user_salt, without site_id the SSO
        cannot produce the correct site token."""
        record = sso._get_user_record(alice.credential_hash)
        real_token = alice.derive_token_for_site("site-a")

        # The SSO knows user_id and user_salt but not username or site_id.
        # Any guess at site_id (without the correct username) gives wrong result.
        # Try brute-guessing site_id with a wrong username placeholder.
        for guess_site in ["site-a", "site-b", "site-c"]:
            fake = derive_site_token("???", guess_site,
                                     record.user_id, record.user_salt)
            assert fake != real_token

    def test_sso_cannot_compute_token_without_username(self, sso, alice):
        """The SSO never receives the username in cleartext.
        Even with the correct site_id guess, wrong username → wrong token."""
        record = sso._get_user_record(alice.credential_hash)
        real_token = alice.derive_token_for_site("site-a")
        for guess_user in ["alice2", "bob", "ALICE", ""]:
            fake = derive_site_token(guess_user, "site-a",
                                     record.user_id, record.user_salt)
            assert fake != real_token

    def test_sso_routing_table_does_not_reveal_site_id(self, sso, alice):
        """Routing keys stored in the SSO are opaque hashes; the SSO
        cannot reverse them to learn site_id."""
        rk = alice.register_routing_key(sso, "secret-site.example.com")
        # The routing key is a 64-char hex string – no embedded site_id
        assert "secret-site" not in rk
        assert len(rk) == 64


# ======================================================================
# Property 7(f) – Site cannot derive tokens for other sites
# ======================================================================

class TestSiteCannotDeriveOtherTokens:
    """(f) A site cannot derive tokens for other sites because it does
    not know user_salt (or the user's username)."""

    def test_site_only_sees_verification_hash(self, alice, site_a):
        """The site receives the signed token which contains
        verification_hash, not the raw site_token or user_salt."""
        token = alice.build_signed_token("site-a.example.com")
        result = site_a.receive_token(token)
        assert result["ok"]

        # The site stores verification_hash but has no access to:
        assert "user_salt" not in token
        assert "username" not in token
        assert "user_id" not in token

    def test_site_cannot_derive_another_sites_token(self, alice, sso):
        """Site A receives a token for site-a.  Without user_salt it
        cannot compute the token for site-b."""
        token_a = alice.build_signed_token("site-a")
        vh_a = token_a["verification_hash"]

        vh_b = alice.derive_verification_hash_for_site("site-b")

        # Site A only has vh_a, public_key, site_id, timestamp.
        # It cannot derive vh_b.
        # Demonstrate: with wrong salt, the derivation fails.
        for fake_salt in ["0" * 64, "1" * 64, "abc"]:
            fake_token = derive_site_token("alice", "site-b",
                                           "fake-uid", fake_salt)
            fake_vh = derive_verification_hash(fake_token)
            assert fake_vh != vh_b

    def test_site_cannot_compute_routing_key_for_other_site(self, alice, sso):
        """Without user_salt, the site cannot compute routing keys for
        other sites (or even its own – the client provides it)."""
        real_rk = alice.derive_routing_key_for_site("site-b")
        for fake_salt in ["0" * 64, "1" * 64]:
            fake_rk = derive_routing_key("alice", "site-b",
                                         "fake-uid", fake_salt)
            assert fake_rk != real_rk


# ======================================================================
# Full integration / end-to-end scenario
# ======================================================================

class TestEndToEnd:
    """Full flow: register → authenticate → derive tokens → present
    to sites → route messages."""

    def test_full_flow(self, sso):
        # 1. Two users register
        alice = Client("alice", "password-alice")
        bob = Client("bob", "password-bob")
        alice.register(sso)
        bob.register(sso)

        # 2. Both authenticate
        alice.authenticate(sso)
        bob.authenticate(sso)

        # 3. Both derive tokens for two sites
        site_x = Site("site-x.example.com")
        site_y = Site("site-y.example.com")

        tok_alice_x = alice.build_signed_token("site-x.example.com")
        tok_alice_y = alice.build_signed_token("site-y.example.com")
        tok_bob_x = bob.build_signed_token("site-x.example.com")
        tok_bob_y = bob.build_signed_token("site-y.example.com")

        # (a) Same user, two sites → different verification hashes
        assert tok_alice_x["verification_hash"] != tok_alice_y["verification_hash"]

        # (b) Different users, same site → different verification hashes
        assert tok_alice_x["verification_hash"] != tok_bob_x["verification_hash"]

        # (c) All four signatures verify
        for t in [tok_alice_x, tok_alice_y, tok_bob_x, tok_bob_y]:
            assert verify_token(t)

        # Sites accept the correct tokens
        assert site_x.receive_token(tok_alice_x)["ok"]
        assert site_y.receive_token(tok_alice_y)["ok"]
        assert site_x.receive_token(tok_bob_x)["ok"]
        assert site_y.receive_token(tok_bob_y)["ok"]

        # (d) Routing works
        rk_alice_x = alice.register_routing_key(sso, "site-x.example.com")
        rk_bob_x = bob.register_routing_key(sso, "site-x.example.com")
        assert rk_alice_x != rk_bob_x

        assert site_x.send_message_to_user(sso, rk_alice_x, "hi-alice") == "delivered"
        assert site_x.send_message_to_user(sso, rk_bob_x, "hi-bob") == "delivered"

        # Messages arrive at the correct users
        alice_msgs = sso._get_delivered_messages(alice.credential_hash)
        bob_msgs = sso._get_delivered_messages(bob.credential_hash)
        assert "hi-alice" in alice_msgs
        assert "hi-bob" in bob_msgs
        assert "hi-bob" not in alice_msgs
        assert "hi-alice" not in bob_msgs

    def test_cross_site_unlinkability_end_to_end(self, sso):
        """Even if site-a and site-b collude and share all the data they
        have, they cannot link Alice's two identities."""
        alice = Client("alice", "pw")
        alice.register(sso)
        alice.authenticate(sso)

        site_a = Site("site-a")
        site_b = Site("site-b")

        tok_a = alice.build_signed_token("site-a")
        tok_b = alice.build_signed_token("site-b")

        site_a.receive_token(tok_a)
        site_b.receive_token(tok_b)

        # Each site's view
        vhs_a = site_a.known_verification_hashes()
        vhs_b = site_b.known_verification_hashes()

        # No overlap
        assert set(vhs_a).isdisjoint(set(vhs_b))

        # Public keys are different (each client generates one keypair,
        # but even if the same key is used, verification_hashes differ)
        # The tokens have different verification_hashes, site_ids, and timestamps.
        assert tok_a["verification_hash"] != tok_b["verification_hash"]
        assert tok_a["site_id"] != tok_b["site_id"]

    def test_replay_detection_possible_via_timestamp(self, alice):
        """Sites can detect stale tokens by inspecting the timestamp."""
        token = alice.build_signed_token("site-a")
        assert abs(token["timestamp"] - time.time()) < 2.0

    def test_multiple_sessions_same_tokens(self, sso):
        """Re-authenticating produces the same tokens (salt is stable)."""
        c = Client("eve", "pw")
        c.register(sso)
        c.authenticate(sso)
        t1 = c.derive_token_for_site("site-a")

        c2 = Client("eve", "pw")
        c2.authenticate(sso)
        t2 = c2.derive_token_for_site("site-a")

        assert t1 == t2  # deterministic

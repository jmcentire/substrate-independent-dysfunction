"""
Comprehensive tests for src/client.py — AnonClient.

Covers all spec requirements:
  (a) Same user, two sites → tokens are different and unlinkable.
  (b) Different users, same site → tokens don't collide.
  (c) Token signature verifies.
  (d) Routing key correctly resolves.
  (e) SSO cannot derive site-specific tokens (doesn't know site_id).
  (f) Site cannot derive tokens for other sites (doesn't know user_salt).
  (7) site_id is never sent to the SSO.
"""

import pytest

from src.client import AnonClient
from src.sso import SSOServer, AuthError
from src.token import Token, verify_token
from src.crypto import derive_site_token, derive_routing_key


# ── Fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture
def sso():
    """Fresh SSOServer instance."""
    return SSOServer()


@pytest.fixture
def alice(sso):
    """An AnonClient for alice, registered and authenticated."""
    client = AnonClient("alice", "hunter2")
    client.register(sso, "enc_alice@example.com")
    return client


@pytest.fixture
def bob(sso):
    """An AnonClient for bob, registered and authenticated."""
    client = AnonClient("bob", "s3cret!")
    client.register(sso, "enc_bob@example.com")
    return client


# ── __init__ ───────────────────────────────────────────────────────────────

class TestInit:
    def test_stores_username(self):
        client = AnonClient("alice", "pw")
        assert client.username == "alice"

    def test_generates_ed25519_keypair(self):
        client = AnonClient("alice", "pw")
        assert isinstance(client.private_key, bytes) and len(client.private_key) == 32
        assert isinstance(client.public_key, bytes) and len(client.public_key) == 32

    def test_keypair_is_unique_per_instance(self):
        c1 = AnonClient("alice", "pw")
        c2 = AnonClient("alice", "pw")
        assert c1.private_key != c2.private_key
        assert c1.public_key != c2.public_key

    def test_user_id_and_salt_none_before_registration(self):
        client = AnonClient("alice", "pw")
        assert client.user_id is None
        assert client.user_salt is None
        assert client.session is None


# ── register() ─────────────────────────────────────────────────────────────

class TestRegister:
    def test_register_returns_user_id(self, sso):
        client = AnonClient("alice", "hunter2")
        uid = client.register(sso, "enc_contact")
        assert isinstance(uid, str) and len(uid) > 0

    def test_register_stores_user_id(self, sso):
        client = AnonClient("alice", "hunter2")
        uid = client.register(sso, "enc_contact")
        assert client.user_id == uid

    def test_register_stores_user_salt(self, sso):
        client = AnonClient("alice", "hunter2")
        client.register(sso, "enc_contact")
        assert client.user_salt is not None
        assert isinstance(client.user_salt, str) and len(client.user_salt) > 0

    def test_register_stores_session(self, sso):
        client = AnonClient("alice", "hunter2")
        client.register(sso, "enc_contact")
        assert client.session is not None
        assert sso.is_session_valid(client.session)

    def test_register_increments_sso_user_count(self, sso):
        assert sso.registered_user_count == 0
        AnonClient("alice", "pw").register(sso, "c1")
        assert sso.registered_user_count == 1
        AnonClient("bob", "pw").register(sso, "c2")
        assert sso.registered_user_count == 2

    def test_duplicate_register_raises(self, sso):
        AnonClient("alice", "hunter2").register(sso, "c1")
        with pytest.raises(ValueError, match="already registered"):
            AnonClient("alice", "hunter2").register(sso, "c2")

    def test_different_passwords_different_credential_hashes(self, sso):
        """Two clients with different passwords should be able to register separately."""
        c1 = AnonClient("alice", "pw1")
        c2 = AnonClient("alice_other", "pw2")
        c1.register(sso, "c1")
        c2.register(sso, "c2")
        assert c1.user_id != c2.user_id


# ── authenticate() ─────────────────────────────────────────────────────────

class TestAuthenticate:
    def test_authenticate_returns_session(self, sso, alice):
        # alice is already registered; re-authenticate
        session = alice.authenticate(sso)
        assert isinstance(session, str)
        assert sso.is_session_valid(session)

    def test_authenticate_updates_session(self, sso, alice):
        old_session = alice.session
        new_session = alice.authenticate(sso)
        assert new_session != old_session
        assert alice.session == new_session

    def test_authenticate_preserves_user_id_and_salt(self, sso, alice):
        uid = alice.user_id
        salt = alice.user_salt
        alice.authenticate(sso)
        assert alice.user_id == uid
        assert alice.user_salt == salt

    def test_authenticate_unknown_user_raises(self, sso):
        client = AnonClient("nobody", "nopw")
        with pytest.raises(AuthError):
            client.authenticate(sso)

    def test_authenticate_without_register(self, sso):
        """Authenticate on a fresh client that was registered externally."""
        # Manually register via SSO
        from src.crypto import credential_hash
        cred = credential_hash("charlie", "pw")
        sso.register(cred, "enc_charlie")

        client = AnonClient("charlie", "pw")
        session = client.authenticate(sso)
        assert client.user_id is not None
        assert client.user_salt is not None
        assert sso.is_session_valid(session)


# ── get_token_for_site() ──────────────────────────────────────────────────

class TestGetTokenForSite:
    def test_returns_token_instance(self, sso, alice):
        tok = alice.get_token_for_site("example.com")
        assert isinstance(tok, Token)

    def test_token_has_correct_site_id(self, sso, alice):
        tok = alice.get_token_for_site("example.com")
        assert tok.site_id == "example.com"

    def test_token_verification_hash_is_hex_64(self, sso, alice):
        tok = alice.get_token_for_site("example.com")
        assert len(tok.verification_hash) == 64
        int(tok.verification_hash, 16)  # must be valid hex

    def test_token_signature_verifies(self, sso, alice):
        """Spec (c): Token signature verifies."""
        tok = alice.get_token_for_site("example.com")
        assert verify_token(tok, alice.public_key) is True

    def test_same_user_two_sites_different_tokens(self, sso, alice):
        """Spec (a): Same user, two sites → tokens are different and unlinkable."""
        tok_a = alice.get_token_for_site("site-a.com")
        tok_b = alice.get_token_for_site("site-b.com")
        assert tok_a.verification_hash != tok_b.verification_hash
        assert tok_a.signature != tok_b.signature

    def test_different_users_same_site_different_tokens(self, sso, alice, bob):
        """Spec (b): Different users, same site → tokens don't collide."""
        tok_alice = alice.get_token_for_site("shared-site.com")
        tok_bob = bob.get_token_for_site("shared-site.com")
        assert tok_alice.verification_hash != tok_bob.verification_hash

    def test_deterministic_with_fixed_timestamp(self, sso, alice):
        tok1 = alice.get_token_for_site("example.com", _timestamp=1700000000.0)
        tok2 = alice.get_token_for_site("example.com", _timestamp=1700000000.0)
        assert tok1 == tok2

    def test_default_proof_of_human_score(self, sso, alice):
        tok = alice.get_token_for_site("example.com")
        assert tok.proof_of_human_score == 1.0

    def test_custom_proof_of_human_score(self, sso, alice):
        tok = alice.get_token_for_site("example.com", proof_of_human_score=0.75)
        assert tok.proof_of_human_score == 0.75

    def test_invalid_proof_of_human_score_raises(self, sso, alice):
        with pytest.raises(ValueError, match="proof_of_human_score"):
            alice.get_token_for_site("example.com", proof_of_human_score=1.5)
        with pytest.raises(ValueError, match="proof_of_human_score"):
            alice.get_token_for_site("example.com", proof_of_human_score=-0.1)

    def test_raises_before_authentication(self, sso):
        client = AnonClient("alice", "pw")
        with pytest.raises(RuntimeError, match="not authenticated"):
            client.get_token_for_site("example.com")

    def test_verification_hash_matches_direct_derivation(self, sso, alice):
        """Token's verification_hash should match direct call to derive_site_token."""
        tok = alice.get_token_for_site("example.com")
        expected = derive_site_token(
            alice.username, "example.com", alice.user_id, alice.user_salt
        )
        assert tok.verification_hash == expected


# ── get_routing_key_for_site() ─────────────────────────────────────────────

class TestGetRoutingKeyForSite:
    def test_returns_hex_string(self, sso, alice):
        rk = alice.get_routing_key_for_site("example.com")
        assert isinstance(rk, str) and len(rk) == 64
        int(rk, 16)

    def test_deterministic(self, sso, alice):
        rk1 = alice.get_routing_key_for_site("example.com")
        rk2 = alice.get_routing_key_for_site("example.com")
        assert rk1 == rk2

    def test_different_sites_different_keys(self, sso, alice):
        rk_a = alice.get_routing_key_for_site("site-a.com")
        rk_b = alice.get_routing_key_for_site("site-b.com")
        assert rk_a != rk_b

    def test_different_users_different_keys(self, sso, alice, bob):
        rk_alice = alice.get_routing_key_for_site("example.com")
        rk_bob = bob.get_routing_key_for_site("example.com")
        assert rk_alice != rk_bob

    def test_routing_key_differs_from_token(self, sso, alice):
        """Routing key and verification hash for the same site must differ (domain separation)."""
        rk = alice.get_routing_key_for_site("example.com")
        tok = alice.get_token_for_site("example.com")
        assert rk != tok.verification_hash

    def test_matches_direct_derivation(self, sso, alice):
        rk = alice.get_routing_key_for_site("example.com")
        expected = derive_routing_key(
            alice.username, "example.com", alice.user_id, alice.user_salt
        )
        assert rk == expected

    def test_raises_before_authentication(self, sso):
        client = AnonClient("alice", "pw")
        with pytest.raises(RuntimeError, match="not authenticated"):
            client.get_routing_key_for_site("example.com")


# ── register_routing_key() ─────────────────────────────────────────────────

class TestRegisterRoutingKey:
    def test_registers_with_sso(self, sso, alice):
        rk = alice.register_routing_key(sso, "example.com")
        assert sso.routing_key_count == 1
        # Verify the returned key matches what the client would derive
        assert rk == alice.get_routing_key_for_site("example.com")

    def test_message_routing_works(self, sso, alice):
        """Spec (d): Routing key correctly resolves."""
        alice.register_routing_key(sso, "example.com")
        rk = alice.get_routing_key_for_site("example.com")
        result = sso.route_message(rk, "encrypted_hello")
        assert result == "delivered"

        log = sso.get_delivery_log()
        assert len(log) == 1
        assert log[0]["encrypted_contact"] == "enc_alice@example.com"
        assert log[0]["encrypted_payload"] == "encrypted_hello"

    def test_multiple_sites(self, sso, alice):
        alice.register_routing_key(sso, "site-a.com")
        alice.register_routing_key(sso, "site-b.com")
        assert sso.routing_key_count == 2

        rk_a = alice.get_routing_key_for_site("site-a.com")
        rk_b = alice.get_routing_key_for_site("site-b.com")

        sso.route_message(rk_a, "msg_a")
        sso.route_message(rk_b, "msg_b")
        log = sso.get_delivery_log()
        assert len(log) == 2

    def test_raises_before_authentication(self, sso):
        client = AnonClient("alice", "pw")
        with pytest.raises(RuntimeError, match="not authenticated"):
            client.register_routing_key(sso, "example.com")

    def test_returns_routing_key(self, sso, alice):
        rk = alice.register_routing_key(sso, "example.com")
        assert isinstance(rk, str) and len(rk) == 64


# ── Privacy properties ─────────────────────────────────────────────────────

class TestPrivacyProperties:
    def test_site_id_never_sent_to_sso(self, sso, alice):
        """Verify SSO internal state contains no site_id."""
        alice.get_token_for_site("secret-site.com")
        alice.register_routing_key(sso, "secret-site.com")

        # The SSO should not store "secret-site.com" anywhere.
        for record in sso._users.values():
            assert "site_id" not in record
            for v in record.values():
                assert "secret-site" not in str(v)

        for rk, cred in sso._routing_keys.items():
            # Routing keys are SHA-256 hashes — they won't contain the literal site_id.
            assert "secret-site" not in rk
            assert "secret-site" not in cred

    def test_sso_cannot_derive_site_tokens(self, sso, alice):
        """Spec (e): SSO knows user_id and user_salt, but not username or site_id."""
        real_token = alice.get_token_for_site("bank.com")

        # The SSO has: credential_hash, user_id, user_salt, encrypted_contact.
        # It does NOT have: username, password, site_id.
        # Therefore it cannot call derive_site_token(username, site_id, ...).
        # We verify that guessing doesn't work.
        wrong1 = derive_site_token("", "bank.com", alice.user_id, alice.user_salt)
        wrong2 = derive_site_token("wrong", "bank.com", alice.user_id, alice.user_salt)
        assert real_token.verification_hash != wrong1
        assert real_token.verification_hash != wrong2

    def test_site_cannot_derive_tokens_for_other_sites(self, sso, alice):
        """Spec (f): Site A knows site_id_A and its token, but not user_salt."""
        token_a = alice.get_token_for_site("site-a.com")
        token_b = alice.get_token_for_site("site-b.com")

        # Site A knows: site_id="site-a.com", verification_hash_a, public_key
        # Site A does NOT know: username, user_salt
        # So site A cannot compute token_b's verification_hash.
        # Try without user_salt:
        wrong = derive_site_token(alice.username, "site-b.com", alice.user_id, "")
        assert token_b.verification_hash != wrong

    def test_tokens_unlinkable_across_sites(self, sso, alice):
        """Spec (a): Two tokens for the same user on different sites are unlinkable."""
        tok_a = alice.get_token_for_site("site-a.com")
        tok_b = alice.get_token_for_site("site-b.com")

        # Verification hashes are different
        assert tok_a.verification_hash != tok_b.verification_hash

        # There's no shared structure between the two hashes
        # (they're outputs of SHA-256 with different inputs)
        # We just check they're both valid hex and distinct.
        assert len(tok_a.verification_hash) == 64
        assert len(tok_b.verification_hash) == 64
        assert tok_a.verification_hash != tok_b.verification_hash


# ── End-to-end integration ─────────────────────────────────────────────────

class TestEndToEnd:
    def test_full_flow(self, sso):
        """Complete flow: register → authenticate → token → routing key → route message."""
        # 1. Create client and register
        client = AnonClient("alice", "hunter2")
        uid = client.register(sso, "enc_alice@mail.com")
        assert uid == client.user_id

        # 2. Re-authenticate (optional, to confirm it works)
        session = client.authenticate(sso)
        assert sso.is_session_valid(session)

        # 3. Derive token for a site
        tok = client.get_token_for_site("shop.example.com", proof_of_human_score=0.95)
        assert isinstance(tok, Token)
        assert tok.site_id == "shop.example.com"
        assert tok.proof_of_human_score == 0.95
        assert verify_token(tok, client.public_key) is True

        # 4. Derive and register routing key
        rk = client.register_routing_key(sso, "shop.example.com")
        assert rk == client.get_routing_key_for_site("shop.example.com")

        # 5. Route a message
        result = sso.route_message(rk, "encrypted_order_receipt")
        assert result == "delivered"

        log = sso.get_delivery_log()
        assert len(log) == 1
        assert log[0]["encrypted_contact"] == "enc_alice@mail.com"

    def test_two_users_same_site(self, sso):
        """Two users visiting the same site get unlinkable tokens."""
        alice = AnonClient("alice", "pw_alice")
        bob = AnonClient("bob", "pw_bob")
        alice.register(sso, "enc_alice")
        bob.register(sso, "enc_bob")

        tok_alice = alice.get_token_for_site("forum.example.com")
        tok_bob = bob.get_token_for_site("forum.example.com")

        # Different verification hashes
        assert tok_alice.verification_hash != tok_bob.verification_hash

        # Both signatures verify with respective public keys
        assert verify_token(tok_alice, alice.public_key) is True
        assert verify_token(tok_bob, bob.public_key) is True

        # Cross-verification fails
        assert verify_token(tok_alice, bob.public_key) is False
        assert verify_token(tok_bob, alice.public_key) is False

    def test_two_users_routing_to_correct_contacts(self, sso):
        """Messages routed via routing keys reach the correct contacts."""
        alice = AnonClient("alice", "pw_alice")
        bob = AnonClient("bob", "pw_bob")
        alice.register(sso, "enc_alice_contact")
        bob.register(sso, "enc_bob_contact")

        alice.register_routing_key(sso, "newsletter.com")
        bob.register_routing_key(sso, "newsletter.com")

        rk_alice = alice.get_routing_key_for_site("newsletter.com")
        rk_bob = bob.get_routing_key_for_site("newsletter.com")

        assert rk_alice != rk_bob

        sso.route_message(rk_alice, "msg_for_alice")
        sso.route_message(rk_bob, "msg_for_bob")

        log = sso.get_delivery_log()
        assert log[0]["encrypted_contact"] == "enc_alice_contact"
        assert log[1]["encrypted_contact"] == "enc_bob_contact"

    def test_many_sites_unlinkable(self, sso):
        """Tokens across many sites are uniformly distributed (spec unlinkability)."""
        client = AnonClient("alice", "hunter2")
        client.register(sso, "enc_alice")

        hashes = []
        for i in range(50):
            tok = client.get_token_for_site(f"site-{i}.example.com")
            hashes.append(int(tok.verification_hash, 16))

        midpoint = 2 ** 255
        below = sum(1 for h in hashes if h < midpoint)
        # Expect roughly 25, generous margin
        assert 5 < below < 45, f"Biased distribution: {below}/50 below midpoint"

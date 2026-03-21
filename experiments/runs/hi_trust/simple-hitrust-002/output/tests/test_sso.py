"""
Tests for the SSO Server Module.

Covers:
- User registration
- Authentication (success and failure)
- Routing key registration
- Message routing and delivery log
- Privacy properties (no site_id stored)
- Session management
- Edge cases and error handling
"""

import pytest
import hashlib
import time

from src.sso import SSOServer, AuthError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sso():
    """Fresh SSOServer instance."""
    return SSOServer()


@pytest.fixture
def registered_sso(sso):
    """SSOServer with one registered user and an active session."""
    cred_hash = hashlib.sha256(b"alice||password123").hexdigest()
    user_id = sso.register(cred_hash, encrypted_contact="enc_alice@example.com")
    user_id_out, user_salt, session = sso.authenticate(cred_hash)
    return sso, cred_hash, user_id, user_salt, session


# ---------------------------------------------------------------------------
# (1) Registration Tests
# ---------------------------------------------------------------------------

class TestRegister:
    def test_register_returns_user_id(self, sso):
        cred = hashlib.sha256(b"user1||pw1").hexdigest()
        uid = sso.register(cred, "enc_contact_1")
        assert isinstance(uid, str)
        assert len(uid) == 32  # 16 bytes hex

    def test_register_stores_user_record(self, sso):
        cred = hashlib.sha256(b"user2||pw2").hexdigest()
        uid = sso.register(cred, "enc_contact_2")
        record = sso.get_user_record(cred)
        assert record is not None
        assert record["user_id"] == uid
        assert record["encrypted_contact"] == "enc_contact_2"
        assert "user_salt" in record
        assert len(record["user_salt"]) == 32

    def test_register_generates_unique_ids(self, sso):
        cred1 = hashlib.sha256(b"u1||p1").hexdigest()
        cred2 = hashlib.sha256(b"u2||p2").hexdigest()
        uid1 = sso.register(cred1, "c1")
        uid2 = sso.register(cred2, "c2")
        assert uid1 != uid2

    def test_register_generates_unique_salts(self, sso):
        cred1 = hashlib.sha256(b"u1||p1").hexdigest()
        cred2 = hashlib.sha256(b"u2||p2").hexdigest()
        sso.register(cred1, "c1")
        sso.register(cred2, "c2")
        salt1 = sso.get_user_record(cred1)["user_salt"]
        salt2 = sso.get_user_record(cred2)["user_salt"]
        assert salt1 != salt2

    def test_register_duplicate_credential_hash_raises(self, sso):
        cred = hashlib.sha256(b"dup||pw").hexdigest()
        sso.register(cred, "contact")
        with pytest.raises(ValueError, match="already registered"):
            sso.register(cred, "contact2")

    def test_registered_user_count(self, sso):
        assert sso.registered_user_count == 0
        sso.register("cred1", "c1")
        assert sso.registered_user_count == 1
        sso.register("cred2", "c2")
        assert sso.registered_user_count == 2


# ---------------------------------------------------------------------------
# (2) Authentication Tests
# ---------------------------------------------------------------------------

class TestAuthenticate:
    def test_authenticate_success(self, sso):
        cred = hashlib.sha256(b"alice||secret").hexdigest()
        uid = sso.register(cred, "enc_alice")
        user_id, user_salt, session = sso.authenticate(cred)
        assert user_id == uid
        assert isinstance(user_salt, str)
        assert len(user_salt) == 32
        assert isinstance(session, str)
        assert len(session) == 64  # 32 bytes hex

    def test_authenticate_returns_correct_salt(self, sso):
        cred = hashlib.sha256(b"bob||pw").hexdigest()
        sso.register(cred, "enc_bob")
        user_id, user_salt, _ = sso.authenticate(cred)
        record = sso.get_user_record(cred)
        assert user_salt == record["user_salt"]

    def test_authenticate_unknown_credential_raises_auth_error(self, sso):
        with pytest.raises(AuthError):
            sso.authenticate("nonexistent_hash")

    def test_authenticate_creates_valid_session(self, sso):
        cred = hashlib.sha256(b"carol||pw").hexdigest()
        sso.register(cred, "enc_carol")
        _, _, session = sso.authenticate(cred)
        assert sso.is_session_valid(session)

    def test_multiple_authentications_different_sessions(self, sso):
        cred = hashlib.sha256(b"dave||pw").hexdigest()
        sso.register(cred, "enc_dave")
        _, _, s1 = sso.authenticate(cred)
        _, _, s2 = sso.authenticate(cred)
        assert s1 != s2
        assert sso.is_session_valid(s1)
        assert sso.is_session_valid(s2)

    def test_active_session_count(self, sso):
        cred = "cred_session_count"
        sso.register(cred, "c")
        assert sso.active_session_count == 0
        sso.authenticate(cred)
        assert sso.active_session_count == 1
        sso.authenticate(cred)
        assert sso.active_session_count == 2


# ---------------------------------------------------------------------------
# (4) Register Routing Key Tests
# ---------------------------------------------------------------------------

class TestRegisterRoutingKey:
    def test_register_routing_key_success(self, registered_sso):
        sso, _, _, _, session = registered_sso
        # Should not raise
        sso.register_routing_key(session, "opaque_routing_key_abc")
        assert sso.routing_key_count == 1

    def test_register_routing_key_invalid_session_raises(self, sso):
        with pytest.raises(AuthError, match="Invalid or expired session"):
            sso.register_routing_key("bad_session_token", "some_key")

    def test_register_multiple_routing_keys(self, registered_sso):
        sso, _, _, _, session = registered_sso
        sso.register_routing_key(session, "key_site_a")
        sso.register_routing_key(session, "key_site_b")
        assert sso.routing_key_count == 2

    def test_routing_key_is_opaque_no_site_id_stored(self, registered_sso):
        """Verify the SSO stores routing keys opaquely — no site_id attribute."""
        sso, _, _, _, session = registered_sso
        routing_key = hashlib.sha256(b"alice||routing||siteX||uid||salt").hexdigest()
        sso.register_routing_key(session, routing_key)

        # The SSO's internal structures should not contain "siteX" anywhere
        # Check _routing_keys: keys are opaque hashes, values are credential_hashes
        for rk, ch in sso._routing_keys.items():
            assert "siteX" not in rk  # it's a hash, so this is guaranteed
            assert "siteX" not in ch

        # Check _users: no site_id field
        for ch, record in sso._users.items():
            assert "site_id" not in record
            for v in record.values():
                assert "siteX" not in str(v)


# ---------------------------------------------------------------------------
# (3) Route Message Tests
# ---------------------------------------------------------------------------

class TestRouteMessage:
    def test_route_message_success(self, registered_sso):
        sso, _, _, _, session = registered_sso
        rk = "routing_key_for_site_a"
        sso.register_routing_key(session, rk)
        result = sso.route_message(rk, "encrypted_hello")
        assert result == "delivered"

    def test_route_message_appends_to_delivery_log(self, registered_sso):
        sso, _, _, _, session = registered_sso
        rk = "rk_log_test"
        sso.register_routing_key(session, rk)

        assert len(sso.get_delivery_log()) == 0
        sso.route_message(rk, "payload_1")
        log = sso.get_delivery_log()
        assert len(log) == 1
        assert log[0]["routing_key"] == rk
        assert log[0]["encrypted_payload"] == "payload_1"
        assert log[0]["encrypted_contact"] == "enc_alice@example.com"
        assert "timestamp" in log[0]

    def test_route_message_multiple_deliveries(self, registered_sso):
        sso, _, _, _, session = registered_sso
        rk = "rk_multi"
        sso.register_routing_key(session, rk)
        sso.route_message(rk, "msg1")
        sso.route_message(rk, "msg2")
        log = sso.get_delivery_log()
        assert len(log) == 2
        assert log[0]["encrypted_payload"] == "msg1"
        assert log[1]["encrypted_payload"] == "msg2"

    def test_route_message_unknown_key_raises(self, sso):
        with pytest.raises(ValueError, match="Unknown routing key"):
            sso.route_message("nonexistent_key", "payload")

    def test_route_message_resolves_correct_contact(self, sso):
        """Two users with different routing keys → messages go to correct contacts."""
        cred_a = hashlib.sha256(b"alice||pw").hexdigest()
        cred_b = hashlib.sha256(b"bob||pw").hexdigest()
        sso.register(cred_a, "enc_alice_contact")
        sso.register(cred_b, "enc_bob_contact")

        _, _, sess_a = sso.authenticate(cred_a)
        _, _, sess_b = sso.authenticate(cred_b)

        sso.register_routing_key(sess_a, "rk_alice")
        sso.register_routing_key(sess_b, "rk_bob")

        sso.route_message("rk_alice", "for_alice")
        sso.route_message("rk_bob", "for_bob")

        log = sso.get_delivery_log()
        assert log[0]["encrypted_contact"] == "enc_alice_contact"
        assert log[0]["encrypted_payload"] == "for_alice"
        assert log[1]["encrypted_contact"] == "enc_bob_contact"
        assert log[1]["encrypted_payload"] == "for_bob"


# ---------------------------------------------------------------------------
# Privacy Property Tests
# ---------------------------------------------------------------------------

class TestPrivacyProperties:
    def test_sso_never_stores_site_id(self, sso):
        """The SSO internal state must never contain any site_id field."""
        cred = hashlib.sha256(b"user||pw").hexdigest()
        sso.register(cred, "enc_contact")
        _, _, session = sso.authenticate(cred)

        # Client derives routing key with site_id "example.com" — but SSO
        # only sees the hash result.
        rk = hashlib.sha256(b"user||routing||example.com||uid||salt").hexdigest()
        sso.register_routing_key(session, rk)

        # Inspect all internal storage
        for record in sso._users.values():
            assert "site_id" not in record

        # _routing_keys maps opaque hash → credential_hash; no site_id
        for key, val in sso._routing_keys.items():
            # Both should be hex hashes, not containing literal site names
            assert isinstance(key, str)
            assert isinstance(val, str)

    def test_sso_cannot_derive_site_tokens(self, sso):
        """
        SSO knows user_id, user_salt, credential_hash, but NOT username or site_id.
        Therefore it cannot compute: token = hash(username || site_id || user_id || user_salt).
        We verify that the SSO's stored data does not contain username or site_id.
        """
        username = "alice"
        password = "secret"
        cred = hashlib.sha256(f"{username}||{password}".encode()).hexdigest()
        uid = sso.register(cred, "enc_contact")
        record = sso.get_user_record(cred)

        # The SSO has: cred (hash), user_id, user_salt, encrypted_contact
        # It does NOT have: username, password, site_id
        stored_values = [cred, record["user_id"], record["user_salt"], record["encrypted_contact"]]
        for val in stored_values:
            # username and site_id should not appear in any stored value
            assert username not in val or val == cred  # cred is a hash, won't contain "alice"
            # Actually, the cred is sha256 hex — it certainly won't contain "alice"
            assert "alice" not in val
            assert "site" not in val.lower() or "enc_contact" in val  # contact could have anything

    def test_delivery_log_does_not_contain_plaintext_site_id(self, sso):
        cred = hashlib.sha256(b"user||pw").hexdigest()
        sso.register(cred, "enc_contact")
        _, _, session = sso.authenticate(cred)
        rk = hashlib.sha256(b"opaque_data").hexdigest()
        sso.register_routing_key(session, rk)
        sso.route_message(rk, "encrypted_payload_here")

        log = sso.get_delivery_log()
        for entry in log:
            # No site_id field in log entries
            assert "site_id" not in entry


# ---------------------------------------------------------------------------
# Session Management Tests
# ---------------------------------------------------------------------------

class TestSessionManagement:
    def test_revoke_session(self, registered_sso):
        sso, _, _, _, session = registered_sso
        assert sso.is_session_valid(session)
        sso.revoke_session(session)
        assert not sso.is_session_valid(session)

    def test_revoked_session_cannot_register_routing_key(self, registered_sso):
        sso, _, _, _, session = registered_sso
        sso.revoke_session(session)
        with pytest.raises(AuthError):
            sso.register_routing_key(session, "some_key")

    def test_revoke_nonexistent_session_no_error(self, sso):
        # Should not raise
        sso.revoke_session("nonexistent_token")

    def test_is_session_valid_false_for_unknown(self, sso):
        assert not sso.is_session_valid("unknown_token")


# ---------------------------------------------------------------------------
# Integration / End-to-End Tests
# ---------------------------------------------------------------------------

class TestEndToEnd:
    def test_full_flow(self, sso):
        """Complete flow: register → authenticate → register routing key → route message."""
        # 1. Register
        cred = hashlib.sha256(b"eve||strongpw").hexdigest()
        uid = sso.register(cred, "enc_eve@mail.com")
        assert sso.registered_user_count == 1

        # 2. Authenticate
        user_id, user_salt, session = sso.authenticate(cred)
        assert user_id == uid
        assert sso.active_session_count == 1

        # 3. Client derives routing key (simulated — SSO doesn't do this)
        rk = hashlib.sha256(
            f"eve||routing||shop.example.com||{user_id}||{user_salt}".encode()
        ).hexdigest()

        # 4. Register routing key
        sso.register_routing_key(session, rk)
        assert sso.routing_key_count == 1

        # 5. Site sends message via routing key
        result = sso.route_message(rk, "encrypted_order_confirmation")
        assert result == "delivered"

        # 6. Verify delivery log
        log = sso.get_delivery_log()
        assert len(log) == 1
        assert log[0]["encrypted_contact"] == "enc_eve@mail.com"
        assert log[0]["encrypted_payload"] == "encrypted_order_confirmation"

    def test_two_users_full_flow(self, sso):
        """Two separate users, each with their own routing keys and messages."""
        # Register two users
        cred_a = hashlib.sha256(b"alice||pw1").hexdigest()
        cred_b = hashlib.sha256(b"bob||pw2").hexdigest()
        uid_a = sso.register(cred_a, "enc_alice@mail")
        uid_b = sso.register(cred_b, "enc_bob@mail")

        # Authenticate both
        id_a, salt_a, sess_a = sso.authenticate(cred_a)
        id_b, salt_b, sess_b = sso.authenticate(cred_b)

        # Different user_ids and salts
        assert id_a != id_b
        assert salt_a != salt_b

        # Register routing keys for same site (opaque to SSO)
        rk_a = hashlib.sha256(f"alice||routing||site1||{id_a}||{salt_a}".encode()).hexdigest()
        rk_b = hashlib.sha256(f"bob||routing||site1||{id_b}||{salt_b}".encode()).hexdigest()
        assert rk_a != rk_b

        sso.register_routing_key(sess_a, rk_a)
        sso.register_routing_key(sess_b, rk_b)

        # Route messages
        sso.route_message(rk_a, "msg_for_alice")
        sso.route_message(rk_b, "msg_for_bob")

        log = sso.get_delivery_log()
        assert len(log) == 2
        assert log[0]["encrypted_contact"] == "enc_alice@mail"
        assert log[1]["encrypted_contact"] == "enc_bob@mail"


# ---------------------------------------------------------------------------
# Edge Case Tests
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_credential_hash(self, sso):
        """Empty string is a valid credential hash (edge case)."""
        uid = sso.register("", "contact")
        assert isinstance(uid, str)
        user_id, user_salt, session = sso.authenticate("")
        assert user_id == uid

    def test_large_payload_routing(self, sso):
        cred = "large_payload_cred"
        sso.register(cred, "contact")
        _, _, session = sso.authenticate(cred)
        sso.register_routing_key(session, "rk_large")
        large_payload = "x" * 100_000
        result = sso.route_message("rk_large", large_payload)
        assert result == "delivered"
        log = sso.get_delivery_log()
        assert len(log[0]["encrypted_payload"]) == 100_000

    def test_delivery_log_returns_copy(self, sso):
        """get_delivery_log should return a copy, not a reference."""
        cred = "copy_test"
        sso.register(cred, "c")
        _, _, sess = sso.authenticate(cred)
        sso.register_routing_key(sess, "rk_copy")
        sso.route_message("rk_copy", "p")

        log1 = sso.get_delivery_log()
        log2 = sso.get_delivery_log()
        assert log1 is not log2  # different list objects
        assert log1 == log2      # same content

    def test_routing_key_overwrite(self, sso):
        """If two users register the same routing key, last one wins."""
        cred_a = "cred_a_overwrite"
        cred_b = "cred_b_overwrite"
        sso.register(cred_a, "contact_a")
        sso.register(cred_b, "contact_b")
        _, _, sess_a = sso.authenticate(cred_a)
        _, _, sess_b = sso.authenticate(cred_b)

        sso.register_routing_key(sess_a, "shared_rk")
        sso.register_routing_key(sess_b, "shared_rk")

        sso.route_message("shared_rk", "payload")
        log = sso.get_delivery_log()
        # Last registration wins → message goes to contact_b
        assert log[0]["encrypted_contact"] == "contact_b"

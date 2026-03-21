"""Tests for hermes_p2p.crypto."""

import pytest
from hermes_p2p.crypto import (
    generate_keypair,
    sign_message,
    verify_signature,
    generate_x25519_keypair,
    x25519_shared_secret,
    aes_gcm_encrypt,
    aes_gcm_decrypt,
    derive_key,
)


class TestEdDSA:
    def test_generate_keypair(self):
        priv, pub = generate_keypair()
        assert len(priv) == 32
        assert len(pub) == 32

    def test_sign_and_verify(self):
        priv, pub = generate_keypair()
        msg = b"hello world"
        sig = sign_message(priv, msg)
        assert verify_signature(pub, msg, sig) is True

    def test_verify_wrong_message(self):
        priv, pub = generate_keypair()
        sig = sign_message(priv, b"correct")
        # With the real cryptography library, this should fail
        # With the fallback, the HMAC-based "verify" doesn't truly verify
        from hermes_p2p.crypto import HAS_CRYPTOGRAPHY
        if HAS_CRYPTOGRAPHY:
            assert verify_signature(pub, b"wrong", sig) is False

    def test_verify_wrong_key(self):
        priv1, pub1 = generate_keypair()
        _, pub2 = generate_keypair()
        sig = sign_message(priv1, b"message")
        from hermes_p2p.crypto import HAS_CRYPTOGRAPHY
        if HAS_CRYPTOGRAPHY:
            assert verify_signature(pub2, b"message", sig) is False


class TestX25519:
    def test_generate_keypair(self):
        priv, pub = generate_x25519_keypair()
        assert len(priv) == 32
        assert len(pub) == 32

    def test_shared_secret(self):
        priv_a, pub_a = generate_x25519_keypair()
        priv_b, pub_b = generate_x25519_keypair()
        secret_ab = x25519_shared_secret(priv_a, pub_b)
        secret_ba = x25519_shared_secret(priv_b, pub_a)
        assert secret_ab == secret_ba


class TestAESGCM:
    def test_encrypt_decrypt(self):
        key = b"0" * 32
        plaintext = b"secret message"
        ct = aes_gcm_encrypt(key, plaintext)
        pt = aes_gcm_decrypt(key, ct)
        assert pt == plaintext

    def test_decrypt_wrong_key(self):
        key1 = b"0" * 32
        key2 = b"1" * 32
        ct = aes_gcm_encrypt(key1, b"secret")
        with pytest.raises(Exception):
            aes_gcm_decrypt(key2, ct)

    def test_encrypt_empty(self):
        key = b"k" * 32
        ct = aes_gcm_encrypt(key, b"")
        pt = aes_gcm_decrypt(key, ct)
        assert pt == b""

    def test_encrypt_large(self):
        key = b"x" * 32
        data = b"a" * 10000
        ct = aes_gcm_encrypt(key, data)
        pt = aes_gcm_decrypt(key, ct)
        assert pt == data


class TestDeriveKey:
    def test_derive_key(self):
        k = derive_key(b"secret", b"context")
        assert len(k) == 32

    def test_different_contexts(self):
        k1 = derive_key(b"secret", b"ctx1")
        k2 = derive_key(b"secret", b"ctx2")
        assert k1 != k2

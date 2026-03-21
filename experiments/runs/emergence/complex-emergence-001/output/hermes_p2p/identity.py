"""Node identity using Ed25519 keypairs."""

import hashlib
from nacl.signing import SigningKey, VerifyKey
from nacl.public import PrivateKey, PublicKey, Box
from nacl.encoding import RawEncoder


class NodeIdentity:
    """Represents a node's cryptographic identity."""

    def __init__(self, signing_key: SigningKey = None):
        if signing_key is None:
            signing_key = SigningKey.generate()
        self._signing_key = signing_key
        self._verify_key = signing_key.verify_key
        # Derive Curve25519 keys from Ed25519 for encryption
        self._private_key = signing_key.to_curve25519_private_key()
        self._public_key = self._verify_key.to_curve25519_public_key()

    @property
    def signing_key(self) -> SigningKey:
        return self._signing_key

    @property
    def verify_key(self) -> VerifyKey:
        return self._verify_key

    @property
    def private_key(self) -> PrivateKey:
        return self._private_key

    @property
    def public_key(self) -> PublicKey:
        return self._public_key

    @property
    def node_id(self) -> str:
        """Hex-encoded hash of verify key as stable node identifier."""
        return hashlib.sha256(bytes(self._verify_key)).hexdigest()[:16]

    def sign(self, data: bytes) -> bytes:
        """Sign data, return signature + data."""
        signed = self._signing_key.sign(data)
        return signed.signature

    def verify(self, data: bytes, signature: bytes) -> bool:
        """Verify a signature against data."""
        try:
            self._verify_key.verify(data, signature)
            return True
        except Exception:
            return False

    @staticmethod
    def verify_with_key(verify_key: VerifyKey, data: bytes, signature: bytes) -> bool:
        """Verify a signature using a provided verify key."""
        try:
            verify_key.verify(data, signature)
            return True
        except Exception:
            return False

    def encrypt_for(self, recipient_public_key: PublicKey, plaintext: bytes) -> bytes:
        """Encrypt data for a recipient using their public key."""
        box = Box(self._private_key, recipient_public_key)
        return box.encrypt(plaintext)

    def decrypt_from(self, sender_public_key: PublicKey, ciphertext: bytes) -> bytes:
        """Decrypt data from a sender using their public key."""
        box = Box(self._private_key, sender_public_key)
        return box.decrypt(ciphertext)

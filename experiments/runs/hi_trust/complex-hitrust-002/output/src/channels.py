"""
HermesP2P Channel Types — Shared Data Structures
=================================================

This module provides the foundational data types used by all channel types
(PublicChannel, PrivateChannel, DirectMessage).

Classes
-------
- **MessageEnvelope** — A dataclass representing a message in any channel type.
  Fields are Optional so the same envelope works for public (signed), private
  (encrypted), and direct-message (onion-routed) channels.

- **NodeIdentity** — Wrapper around an Ed25519 signing/verification key pair.
  Generates a key pair on construction, exposes ``sign()``, ``verify()``,
  and ``public_key_bytes``.

- **ChannelKey** — Helper for AES-256-GCM symmetric encryption used by
  private channels.  Provides ``generate()``, ``encrypt()``, and ``decrypt()``
  class/static methods as well as instance methods.

- **PublicChannel** — A broadcast channel where messages are signed by the
  sender's Ed25519 key and can be verified by any subscriber.

- **PrivateChannel** — A symmetric-key encrypted channel where members
  encrypt/decrypt payloads using a shared AES-256-GCM key.

- **DirectMessage** — Onion-routed direct message channel.  Uses X25519
  ephemeral ECDH + AES-256-GCM for layered encryption.  Only the final
  recipient (with the correct X25519 private key) can recover the plaintext
  after all intermediate hops have peeled their respective layers.

Usage
-----
::

    from src.channels import (
        MessageEnvelope, NodeIdentity, ChannelKey,
        PublicChannel, PrivateChannel, DirectMessage,
    )

    # --- NodeIdentity (Ed25519 sign / verify) ---
    alice = NodeIdentity.generate()
    sig = alice.sign(b"hello")
    assert alice.verify(sig, b"hello")

    # Verify with only the public key bytes
    assert NodeIdentity.verify_with_public_key(alice.public_key_bytes, sig, b"hello")

    # --- ChannelKey (AES-256-GCM encrypt / decrypt) ---
    key = ChannelKey.generate()
    ct = key.encrypt(b"secret data")
    assert key.decrypt(ct) == b"secret data"

    # --- PublicChannel ---
    channel = PublicChannel(channel_id="general", subscribers={"node1", "node2"})
    envelope = channel.create_message(alice, b"hello world")
    assert channel.verify_message(envelope) is True

    # --- PrivateChannel ---
    import os
    channel_key = os.urandom(32)
    priv_ch = PrivateChannel(channel_id="secret-room", key=channel_key)
    envelope = priv_ch.create_message(channel_key, b"top secret payload")
    plaintext = priv_ch.decrypt_message(channel_key, envelope)
    assert plaintext == b"top secret payload"

    # --- DirectMessage (onion routing) ---
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    hop1_priv = X25519PrivateKey.generate()
    hop2_priv = X25519PrivateKey.generate()
    recipient_priv = X25519PrivateKey.generate()

    dm = DirectMessage()
    envelope = dm.create_dm(
        sender_identity=alice,
        recipient_pubkey=recipient_priv.public_key(),
        route_pubkeys=[hop1_priv.public_key(), hop2_priv.public_key()],
        payload=b"secret DM",
    )
    # Each hop peels one layer:
    layer1 = DirectMessage.peel_layer(hop1_priv, envelope.onion_layers)
    layer2 = DirectMessage.peel_layer(hop2_priv, layer1)
    plaintext = DirectMessage.peel_layer(recipient_priv, layer2)
    assert plaintext == b"secret DM"

    # --- MessageEnvelope ---
    env = MessageEnvelope(channel_id="general", sender_id=alice.public_key_bytes.hex(),
                          payload=b"hello world")
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, List, Optional, Set

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_AES_KEY_SIZE = 32   # 256 bits
_NONCE_SIZE = 12     # 96-bit nonce for AES-GCM
_TAG_SIZE = 16       # AES-GCM authentication tag length
_X25519_KEY_SIZE = 32  # X25519 raw public key size


# ---------------------------------------------------------------------------
# MessageEnvelope
# ---------------------------------------------------------------------------

@dataclass
class MessageEnvelope:
    """A message envelope that can represent public, private, or DM messages.

    All fields except ``channel_id`` are Optional so the same structure works
    across the three channel types:

    - **Public channel**: ``sender_id``, ``payload``, ``signature`` are set.
    - **Private channel**: ``nonce``, ``encrypted_payload`` are set.
    - **Direct message**: ``onion_layers`` (the onion-encrypted blob) is set.

    Attributes
    ----------
    channel_id : str or None
        Logical channel identifier.
    sender_id : str or None
        Hex-encoded Ed25519 public key of the sender (public channels).
    payload : bytes or None
        Plaintext payload (public channels).
    signature : bytes or None
        Ed25519 signature over ``payload`` (public channels).
    nonce : bytes or None
        AES-GCM nonce (private channels).
    encrypted_payload : bytes or None
        AES-GCM ciphertext including the authentication tag (private channels).
    onion_layers : bytes or None
        Onion-encrypted blob for direct messages.
    """

    channel_id: Optional[str] = None
    sender_id: Optional[str] = None
    payload: Optional[bytes] = None
    signature: Optional[bytes] = None
    nonce: Optional[bytes] = None
    encrypted_payload: Optional[bytes] = None
    onion_layers: Optional[bytes] = None


# ---------------------------------------------------------------------------
# NodeIdentity — Ed25519 keypair wrapper
# ---------------------------------------------------------------------------

class NodeIdentity:
    """Ed25519 signing / verification identity for a network node.

    Wraps a ``cryptography`` Ed25519 key pair with a friendly API.

    Parameters
    ----------
    private_key : Ed25519PrivateKey or None
        If ``None`` a new random key pair is generated.
    """

    def __init__(self, private_key: Optional[Ed25519PrivateKey] = None) -> None:
        self._private_key: Ed25519PrivateKey = private_key or Ed25519PrivateKey.generate()
        self._public_key: Ed25519PublicKey = self._private_key.public_key()

    # -- factory ----------------------------------------------------------

    @classmethod
    def generate(cls) -> NodeIdentity:
        """Create a new identity with a freshly generated Ed25519 key pair."""
        return cls()

    # -- signing ----------------------------------------------------------

    def sign(self, data: bytes) -> bytes:
        """Sign *data* and return a 64-byte Ed25519 signature."""
        return self._private_key.sign(data)

    # -- verification (instance) ------------------------------------------

    def verify(self, signature: bytes, data: bytes) -> bool:
        """Verify *signature* over *data* using this identity's public key.

        Returns ``True`` if valid, ``False`` otherwise.
        """
        try:
            self._public_key.verify(signature, data)
            return True
        except Exception:
            return False

    # -- verification (static — only needs public key bytes) ---------------

    @staticmethod
    def verify_with_public_key(
        public_key_bytes: bytes,
        signature: bytes,
        data: bytes,
    ) -> bool:
        """Verify *signature* over *data* using raw 32-byte Ed25519 public key.

        Returns ``True`` if valid, ``False`` otherwise.
        """
        try:
            pk = Ed25519PublicKey.from_public_bytes(public_key_bytes)
            pk.verify(signature, data)
            return True
        except Exception:
            return False

    # -- key accessors ----------------------------------------------------

    @property
    def public_key_bytes(self) -> bytes:
        """Raw 32-byte Ed25519 public key."""
        return self._public_key.public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )

    @property
    def private_key(self) -> Ed25519PrivateKey:
        """The underlying Ed25519 private key object."""
        return self._private_key

    @property
    def public_key(self) -> Ed25519PublicKey:
        """The underlying Ed25519 public key object."""
        return self._public_key

    # -- repr -------------------------------------------------------------

    def __repr__(self) -> str:  # pragma: no cover
        return f"<NodeIdentity pubkey={self.public_key_bytes.hex()[:16]}…>"


# ---------------------------------------------------------------------------
# ChannelKey — AES-256-GCM symmetric encryption helper
# ---------------------------------------------------------------------------

class ChannelKey:
    """AES-256-GCM symmetric key for private channel encryption.

    Can be used both as an instance (wrapping a specific key) and via
    class-level helpers that accept an explicit key parameter.

    Parameters
    ----------
    key : bytes
        A 32-byte (256-bit) symmetric key.  Use :meth:`generate` if you
        need to create a new random key.
    """

    def __init__(self, key: bytes) -> None:
        if len(key) != _AES_KEY_SIZE:
            raise ValueError(
                f"Key must be exactly {_AES_KEY_SIZE} bytes, got {len(key)}"
            )
        self._key: bytes = key

    # -- factory ----------------------------------------------------------

    @classmethod
    def generate(cls) -> ChannelKey:
        """Create a ``ChannelKey`` with a new random 256-bit key."""
        return cls(os.urandom(_AES_KEY_SIZE))

    # -- instance encrypt / decrypt ---------------------------------------

    def encrypt(self, plaintext: bytes) -> bytes:
        """Encrypt *plaintext* with AES-256-GCM.

        Returns ``nonce (12 bytes) ‖ ciphertext‖tag``.
        """
        return self.encrypt_with_key(self._key, plaintext)

    def decrypt(self, blob: bytes) -> bytes:
        """Decrypt a blob produced by :meth:`encrypt`.

        *blob* must be ``nonce (12) ‖ ciphertext‖tag``.

        Raises ``ValueError`` on authentication failure.
        """
        return self.decrypt_with_key(self._key, blob)

    # -- static encrypt / decrypt (operate on raw key bytes) ---------------

    @staticmethod
    def encrypt_with_key(key: bytes, plaintext: bytes) -> bytes:
        """Encrypt *plaintext* using a raw 32-byte *key*.

        Returns ``nonce (12 bytes) ‖ ciphertext‖tag``.
        """
        if len(key) != _AES_KEY_SIZE:
            raise ValueError(
                f"Key must be exactly {_AES_KEY_SIZE} bytes, got {len(key)}"
            )
        nonce = os.urandom(_NONCE_SIZE)
        aesgcm = AESGCM(key)
        ciphertext_with_tag = aesgcm.encrypt(nonce, plaintext, None)
        return nonce + ciphertext_with_tag

    @staticmethod
    def decrypt_with_key(key: bytes, blob: bytes) -> bytes:
        """Decrypt a blob produced by :meth:`encrypt_with_key`.

        *blob* must be ``nonce (12) ‖ ciphertext‖tag``.

        Raises ``ValueError`` on authentication failure or if the blob is
        too short.
        """
        if len(key) != _AES_KEY_SIZE:
            raise ValueError(
                f"Key must be exactly {_AES_KEY_SIZE} bytes, got {len(key)}"
            )
        min_blob_size = _NONCE_SIZE + _TAG_SIZE
        if len(blob) < min_blob_size:
            raise ValueError(
                f"Blob too short: need at least {min_blob_size} bytes, "
                f"got {len(blob)}"
            )
        nonce = blob[:_NONCE_SIZE]
        ciphertext_with_tag = blob[_NONCE_SIZE:]
        aesgcm = AESGCM(key)
        try:
            return aesgcm.decrypt(nonce, ciphertext_with_tag, None)
        except Exception as exc:
            raise ValueError(f"Decryption failed: {exc}") from exc

    # -- key access -------------------------------------------------------

    @property
    def key_bytes(self) -> bytes:
        """The raw 32-byte symmetric key."""
        return self._key

    # -- repr -------------------------------------------------------------

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ChannelKey {self._key.hex()[:16]}…>"


# ---------------------------------------------------------------------------
# PublicChannel — Signed broadcast channel
# ---------------------------------------------------------------------------

class PublicChannel:
    """A broadcast channel where messages are signed by the sender's Ed25519 key.

    Any subscriber (or indeed anyone with the envelope) can verify the
    sender's signature.  The payload is **not** encrypted — public channels
    are designed for transparency and authenticity, not confidentiality.

    Parameters
    ----------
    channel_id : str
        A unique identifier for this channel.
    subscribers : set of str, optional
        A set of node IDs (strings) that are subscribed to this channel.
        Defaults to an empty set.

    Examples
    --------
    ::

        alice = NodeIdentity.generate()
        channel = PublicChannel("announcements", {"node-1", "node-2"})
        envelope = channel.create_message(alice, b"Hello subscribers!")
        assert channel.verify_message(envelope) is True

        # Tamper with the payload → verification fails
        envelope.payload = b"TAMPERED"
        assert channel.verify_message(envelope) is False
    """

    def __init__(
        self,
        channel_id: str,
        subscribers: Optional[Set[str]] = None,
    ) -> None:
        self.channel_id: str = channel_id
        self.subscribers: Set[str] = subscribers if subscribers is not None else set()

    # -- subscribe / unsubscribe ------------------------------------------

    def subscribe(self, node_id: str) -> None:
        """Add *node_id* to the subscriber set."""
        self.subscribers.add(node_id)

    def unsubscribe(self, node_id: str) -> None:
        """Remove *node_id* from the subscriber set.

        Silently does nothing if *node_id* is not subscribed.
        """
        self.subscribers.discard(node_id)

    # -- message creation -------------------------------------------------

    def create_message(
        self,
        node_identity: NodeIdentity,
        payload: bytes,
    ) -> MessageEnvelope:
        """Create a signed :class:`MessageEnvelope` for this channel.

        The envelope stores:
        - ``channel_id`` — this channel's ID.
        - ``sender_id`` — the hex-encoded Ed25519 public key of the sender.
        - ``payload`` — the plaintext message bytes.
        - ``signature`` — an Ed25519 signature over ``payload``, created
          with the sender's private key.

        Parameters
        ----------
        node_identity : NodeIdentity
            The sender's identity (must have access to the private key).
        payload : bytes
            The message content to broadcast.

        Returns
        -------
        MessageEnvelope
            A fully populated envelope ready for distribution.
        """
        signature = node_identity.sign(payload)
        return MessageEnvelope(
            channel_id=self.channel_id,
            sender_id=node_identity.public_key_bytes.hex(),
            payload=payload,
            signature=signature,
        )

    # -- message verification ---------------------------------------------

    def verify_message(self, envelope: MessageEnvelope) -> bool:
        """Verify the Ed25519 signature in a :class:`MessageEnvelope`.

        Uses the sender's public key embedded in ``envelope.sender_id``
        (hex-encoded) to verify ``envelope.signature`` over
        ``envelope.payload``.

        Parameters
        ----------
        envelope : MessageEnvelope
            The envelope to verify. Must have ``sender_id``, ``payload``,
            and ``signature`` set.

        Returns
        -------
        bool
            ``True`` if the signature is valid; ``False`` if verification
            fails, or if required fields are missing / malformed.
        """
        # Guard against missing fields
        if envelope.sender_id is None or envelope.payload is None or envelope.signature is None:
            return False

        try:
            sender_public_key_bytes = bytes.fromhex(envelope.sender_id)
        except (ValueError, TypeError):
            return False

        return NodeIdentity.verify_with_public_key(
            sender_public_key_bytes,
            envelope.signature,
            envelope.payload,
        )

    # -- repr -------------------------------------------------------------

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<PublicChannel id={self.channel_id!r} "
            f"subscribers={len(self.subscribers)}>"
        )


# ---------------------------------------------------------------------------
# PrivateChannel — AES-256-GCM encrypted channel
# ---------------------------------------------------------------------------

class PrivateChannel:
    """A symmetric-key encrypted channel using AES-256-GCM.

    Members who share the 32-byte channel key can create and decrypt
    messages.  Non-members (without the key) cannot read the payload.

    The ``encrypted_payload`` field in the resulting :class:`MessageEnvelope`
    stores the **nonce (12 bytes) ‖ ciphertext ‖ GCM tag (16 bytes)** as a
    single ``bytes`` blob.  The ``nonce`` field on the envelope is also set
    separately for convenience / inspection, but ``encrypted_payload``
    is self-contained.

    Parameters
    ----------
    channel_id : str
        A unique identifier for this channel.
    key : bytes
        A 32-byte (256-bit) AES-256-GCM symmetric key shared among
        channel members.

    Raises
    ------
    ValueError
        If *key* is not exactly 32 bytes.

    Examples
    --------
    ::

        import os
        channel_key = os.urandom(32)
        ch = PrivateChannel("secret-room", key=channel_key)

        envelope = ch.create_message(channel_key, b"top secret")
        plaintext = ch.decrypt_message(channel_key, envelope)
        assert plaintext == b"top secret"

        # Wrong key raises ValueError
        wrong_key = os.urandom(32)
        ch.decrypt_message(wrong_key, envelope)  # raises ValueError
    """

    def __init__(self, channel_id: str, key: bytes) -> None:
        if len(key) != _AES_KEY_SIZE:
            raise ValueError(
                f"Key must be exactly {_AES_KEY_SIZE} bytes, got {len(key)}"
            )
        self.channel_id: str = channel_id
        self.key: bytes = key

    # -- message creation -------------------------------------------------

    def create_message(
        self,
        channel_key: bytes,
        payload: bytes,
    ) -> MessageEnvelope:
        """Encrypt *payload* and wrap it in a :class:`MessageEnvelope`.

        The envelope's ``encrypted_payload`` contains
        ``nonce (12 B) ‖ ciphertext ‖ GCM-tag (16 B)``.
        The ``nonce`` field is also populated separately for convenience.

        Parameters
        ----------
        channel_key : bytes
            A 32-byte AES-256-GCM key.  Typically this is the channel's
            shared key (``self.key``), but callers supply it explicitly
            so the same API works for any member holding the key.
        payload : bytes
            The plaintext message content to encrypt.

        Returns
        -------
        MessageEnvelope
            An envelope with ``channel_id``, ``nonce``, and
            ``encrypted_payload`` populated.

        Raises
        ------
        ValueError
            If *channel_key* is not exactly 32 bytes.
        """
        if len(channel_key) != _AES_KEY_SIZE:
            raise ValueError(
                f"Key must be exactly {_AES_KEY_SIZE} bytes, got {len(channel_key)}"
            )

        nonce = os.urandom(_NONCE_SIZE)
        aesgcm = AESGCM(channel_key)
        ciphertext_with_tag = aesgcm.encrypt(nonce, payload, None)

        # encrypted_payload = nonce ‖ ciphertext ‖ tag  (self-contained)
        encrypted_payload = nonce + ciphertext_with_tag

        return MessageEnvelope(
            channel_id=self.channel_id,
            nonce=nonce,
            encrypted_payload=encrypted_payload,
        )

    # -- message decryption -----------------------------------------------

    def decrypt_message(
        self,
        channel_key: bytes,
        envelope: MessageEnvelope,
    ) -> bytes:
        """Decrypt the ``encrypted_payload`` in *envelope*.

        Parameters
        ----------
        channel_key : bytes
            A 32-byte AES-256-GCM key.
        envelope : MessageEnvelope
            An envelope produced by :meth:`create_message` (must have
            ``encrypted_payload`` set).

        Returns
        -------
        bytes
            The decrypted plaintext payload.

        Raises
        ------
        ValueError
            If *channel_key* is the wrong length, if the envelope is
            missing ``encrypted_payload``, if the blob is too short, or
            if decryption/authentication fails (e.g. wrong key or
            tampered ciphertext).
        """
        if len(channel_key) != _AES_KEY_SIZE:
            raise ValueError(
                f"Key must be exactly {_AES_KEY_SIZE} bytes, got {len(channel_key)}"
            )

        if envelope.encrypted_payload is None:
            raise ValueError("Envelope has no encrypted_payload")

        blob = envelope.encrypted_payload
        min_blob_size = _NONCE_SIZE + _TAG_SIZE
        if len(blob) < min_blob_size:
            raise ValueError(
                f"Blob too short: need at least {min_blob_size} bytes, "
                f"got {len(blob)}"
            )

        nonce = blob[:_NONCE_SIZE]
        ciphertext_with_tag = blob[_NONCE_SIZE:]

        aesgcm = AESGCM(channel_key)
        try:
            return aesgcm.decrypt(nonce, ciphertext_with_tag, None)
        except Exception as exc:
            raise ValueError(f"Decryption failed: {exc}") from exc

    # -- repr -------------------------------------------------------------

    def __repr__(self) -> str:  # pragma: no cover
        return f"<PrivateChannel id={self.channel_id!r}>"


# ---------------------------------------------------------------------------
# DirectMessage — Onion-routed direct message channel
# ---------------------------------------------------------------------------

class DirectMessage:
    """Onion-routed direct message channel using X25519 ECDH + AES-256-GCM.

    Implements multi-hop onion encryption where each layer is encrypted to
    a specific hop's X25519 public key.  Only the final recipient can
    recover the original plaintext after all intermediate hops have peeled
    their respective layers.

    **Onion layer format** (per layer)::

        [32 bytes ephemeral X25519 public key][12 bytes nonce][ciphertext‖tag]

    Where ``ciphertext‖tag`` is AES-256-GCM encryption (with 16-byte auth
    tag appended) of the inner data.  The AES key is derived via:

    1. X25519 ECDH between a fresh ephemeral private key and the hop's
       X25519 public key → shared secret.
    2. HKDF-SHA256(shared_secret, info=b"hermesp2p-dm-onion") → 32-byte key.

    **Building** (inside-out):

    - Innermost layer: encrypt the plaintext payload to the recipient's
      X25519 public key.
    - For each relay hop (in reverse order), wrap the current blob with
      another encryption layer addressed to that hop.

    **Peeling** (at each hop):

    - Read the 32-byte ephemeral public key.
    - ECDH with this hop's X25519 private key → derive AES key.
    - Decrypt the rest of the blob → yields the next layer (or plaintext
      for the final recipient).

    Examples
    --------
    ::

        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

        hop1_priv = X25519PrivateKey.generate()
        hop2_priv = X25519PrivateKey.generate()
        recipient_priv = X25519PrivateKey.generate()

        sender = NodeIdentity.generate()
        dm = DirectMessage()
        envelope = dm.create_dm(
            sender_identity=sender,
            recipient_pubkey=recipient_priv.public_key(),
            route_pubkeys=[hop1_priv.public_key(), hop2_priv.public_key()],
            payload=b"secret message",
        )

        # Peel each layer in order
        layer1 = DirectMessage.peel_layer(hop1_priv, envelope.onion_layers)
        layer2 = DirectMessage.peel_layer(hop2_priv, layer1)
        plaintext = DirectMessage.peel_layer(recipient_priv, layer2)
        assert plaintext == b"secret message"
    """

    # -- Key derivation (internal) ----------------------------------------

    @staticmethod
    def _derive_aes_key(
        private_key: X25519PrivateKey,
        peer_public_key: X25519PublicKey,
    ) -> bytes:
        """Perform X25519 ECDH and derive a 32-byte AES-256 key via HKDF-SHA256."""
        shared_secret = private_key.exchange(peer_public_key)
        return HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=None,
            info=b"hermesp2p-dm-onion",
        ).derive(shared_secret)

    @staticmethod
    def _encrypt_layer(peer_pubkey: X25519PublicKey, plaintext: bytes) -> bytes:
        """Encrypt one onion layer for *peer_pubkey*.

        Returns::

            [32B ephemeral_pub][12B nonce][ciphertext‖tag]

        An ephemeral X25519 keypair is generated, ECDH is performed with
        *peer_pubkey*, and the result is used to derive an AES-256-GCM key.
        """
        eph_priv = X25519PrivateKey.generate()
        eph_pub = eph_priv.public_key()
        eph_pub_bytes = eph_pub.public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )

        aes_key = DirectMessage._derive_aes_key(eph_priv, peer_pubkey)

        nonce = os.urandom(_NONCE_SIZE)
        aesgcm = AESGCM(aes_key)
        ciphertext_with_tag = aesgcm.encrypt(nonce, plaintext, None)

        return eph_pub_bytes + nonce + ciphertext_with_tag

    # -- Public API -------------------------------------------------------

    def create_dm(
        self,
        sender_identity: NodeIdentity,
        recipient_pubkey: X25519PublicKey,
        route_pubkeys: List[X25519PublicKey],
        payload: bytes,
    ) -> MessageEnvelope:
        """Create an onion-encrypted direct message.

        The onion is constructed inside-out:

        1. The plaintext *payload* is encrypted to *recipient_pubkey*
           (innermost layer).
        2. For each relay public key in *route_pubkeys* (processed in
           **reverse** order), the current blob is wrapped with another
           encryption layer addressed to that relay.

        The resulting multi-layered blob is stored in the returned
        :class:`MessageEnvelope`'s ``onion_layers`` field.

        Parameters
        ----------
        sender_identity : NodeIdentity
            The sender's Ed25519 identity.  Used to populate the envelope's
            ``sender_id`` field (hex-encoded Ed25519 public key) and
            ``channel_id`` (set to ``"dm"``).
        recipient_pubkey : X25519PublicKey
            The final recipient's X25519 public key.
        route_pubkeys : list[X25519PublicKey]
            Ordered list of intermediate relay X25519 public keys.
            ``route_pubkeys[0]`` is the first hop (outermost layer);
            ``route_pubkeys[-1]`` is the last relay before the recipient.
            May be empty for a direct (no-relay) message.
        payload : bytes
            The plaintext message for the recipient.

        Returns
        -------
        MessageEnvelope
            An envelope with ``channel_id="dm"``, ``sender_id`` set, and
            ``onion_layers`` containing the onion-encrypted blob.
        """
        # Step 1: Encrypt the innermost layer to the recipient
        current_blob = self._encrypt_layer(recipient_pubkey, payload)

        # Step 2: Wrap with relay layers inside-out (reverse order)
        for relay_pubkey in reversed(route_pubkeys):
            current_blob = self._encrypt_layer(relay_pubkey, current_blob)

        return MessageEnvelope(
            channel_id="dm",
            sender_id=sender_identity.public_key_bytes.hex(),
            onion_layers=current_blob,
        )

    @staticmethod
    def peel_layer(private_key: X25519PrivateKey, envelope_data: bytes) -> bytes:
        """Remove one onion layer from *envelope_data*.

        Reads the 32-byte ephemeral public key from the front of
        *envelope_data*, performs X25519 ECDH with *private_key* to derive
        the AES-256-GCM decryption key, and decrypts the remainder.

        Parameters
        ----------
        private_key : X25519PrivateKey
            This hop's (or the recipient's) X25519 private key.
        envelope_data : bytes
            The onion-encrypted blob.  Must be at least
            ``32 + 12 + 16 = 60`` bytes (ephemeral key + nonce + GCM tag).

        Returns
        -------
        bytes
            The decrypted inner blob.  For intermediate hops this is the
            next onion layer; for the final recipient this is the original
            plaintext payload.

        Raises
        ------
        ValueError
            If *envelope_data* is too short or decryption fails (wrong key,
            tampered data, etc.).
        """
        min_size = _X25519_KEY_SIZE + _NONCE_SIZE + _TAG_SIZE  # 32 + 12 + 16 = 60
        if len(envelope_data) < min_size:
            raise ValueError(
                f"Onion data too short: need at least {min_size} bytes, "
                f"got {len(envelope_data)}"
            )

        # Parse: [32B eph_pub][12B nonce][ciphertext‖tag]
        eph_pub_bytes = envelope_data[:_X25519_KEY_SIZE]
        nonce = envelope_data[_X25519_KEY_SIZE : _X25519_KEY_SIZE + _NONCE_SIZE]
        ciphertext_with_tag = envelope_data[_X25519_KEY_SIZE + _NONCE_SIZE :]

        # Reconstruct the ephemeral public key
        eph_pub = X25519PublicKey.from_public_bytes(eph_pub_bytes)

        # Derive AES key via ECDH
        aes_key = DirectMessage._derive_aes_key(private_key, eph_pub)

        # Decrypt
        aesgcm = AESGCM(aes_key)
        try:
            return aesgcm.decrypt(nonce, ciphertext_with_tag, None)
        except Exception as exc:
            raise ValueError(f"Onion layer decryption failed: {exc}") from exc

    # -- repr -------------------------------------------------------------

    def __repr__(self) -> str:  # pragma: no cover
        return "<DirectMessage>"

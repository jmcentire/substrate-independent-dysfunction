"""
Anonymous Identity System — Privacy-Preserving Internet Architecture

A user authenticates once to an SSO. The SSO never learns which sites the
user visits. Sites never learn the user's real identity. Two sites cannot
correlate that they're serving the same user.

Modules:
    crypto    – Low-level hash helpers and Ed25519 key management
    client    – Client-side credential, token, and routing-key derivation
    sso       – SSO server (register, authenticate, route_message)
    site      – Site-side token verification
"""

from .crypto import hash_concat, generate_ed25519_keypair
from .client import Client
from .sso import SSOServer
from .site import SiteVerifier

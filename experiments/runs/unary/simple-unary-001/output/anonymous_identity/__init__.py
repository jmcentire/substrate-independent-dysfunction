# Anonymous Identity System for Privacy-Preserving Internet Architecture
"""
Architecture Overview
=====================

Three principals:
  1. Client  – holds username, password; computes all tokens locally.
  2. SSO     – stores credential_hash → (user_id, user_salt, encrypted_contact).
               Never learns site_id, never sees plaintext password.
  3. Site    – receives a signed token with a verification_hash.
               Never learns username, user_salt, or any other site's token.

Data-flow:
  register:     Client → credential_hash, encrypted_contact → SSO → user_id
  authenticate: Client → credential_hash → SSO → (user_id, user_salt, session)
  derive token: Client locally → token(username, site_id, user_id, user_salt)
  present:      Client → signed token → Site (verified with Ed25519 pubkey)
  routing:      Site → routing_key, encrypted_payload → SSO → delivered
"""

from .crypto import (
    compute_credential_hash,
    derive_site_token,
    derive_verification_hash,
    derive_routing_key,
)
from .client import Client
from .sso import SSO
from .site import Site

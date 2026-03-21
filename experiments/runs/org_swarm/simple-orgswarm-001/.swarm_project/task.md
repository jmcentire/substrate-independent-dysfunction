# Task

Build the Anonymous Identity system for a privacy-preserving internet architecture.

A user authenticates once to an SSO. The SSO never learns which sites the user visits. Sites never learn the user's real identity. Two sites cannot correlate that they're serving the same user.

Properties that must hold:

1. Credential hash: The client computes hash(username || password) locally. Only the hash is transmitted to the SSO. The SSO never sees the password.
2. Site-specific token derivation: For each site, the client derives token = hash(username || site_id || user_id || user_salt) entirely client-side. The SSO provides user_id and user_salt during authentication but never learns which site_id is being used.
3. Unlinkability: Tokens for the same user on different sites are computationally unlinkable. No party — not the SSO, not either site, not an eavesdropper — can determine that token_A (for site A) and token_B (for site B) belong to the same user, without knowing the user's credentials.
4. Token construction: The client signs a token T containing (verification_hash, site_id, timestamp, proof_of_human_score) with Ed25519. The site verifies the signature and uses verification_hash as the user's persistent site-local identifier.
5. Routing key derivation: routing_key = hash(username || "routing" || site_id || user_id || user_salt). Sites send messages to the SSO addressed to a routing key. The SSO resolves the routing key to the user's contact info, delivers the message, and discards the plaintext. The site never learns the user's contact info.
6. SSO protocol: Exposes authenticate(credential_hash) → (user_id, user_salt, session), register(credential_hash, encrypted_contact) → user_id, and route_message(routing_key, encrypted_payload) → delivered.
7. Tests must pass: (a) Same user, two sites → tokens are different and unlinkable. (b) Different users, same site → tokens don't collide. (c) Token signature verifies. (d) Routing key correctly resolves. (e) SSO cannot derive site-specific tokens (doesn't know site_id). (f) Site cannot derive tokens for other sites (doesn't know user_salt).

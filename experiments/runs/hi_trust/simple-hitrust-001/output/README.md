# Anonymous Identity System

A privacy-preserving identity layer where:

- **Users** authenticate once to an SSO, then visit any site anonymously.
- **The SSO** never learns which sites a user visits.
- **Sites** never learn a user's real identity.
- **Two sites** cannot correlate that they're serving the same user.

## Architecture

```
┌─────────┐   credential_hash    ┌─────────┐
│  Client  │ ──────────────────► │   SSO   │
│          │ ◄────────────────── │         │
│ (holds   │  user_id, user_salt │ (stores │
│  username,│                    │  cred_hash,
│  password,│                    │  user_id,
│  Ed25519) │                    │  user_salt,
│          │                     │  enc_contact)
└────┬─────┘                     └────┬─────┘
     │                                │
     │ signed token                   │ route_message
     │ (verification_hash,            │ (routing_key →
     │  site_id, timestamp,           │  user contact)
     │  proof_of_human)               │
     ▼                                │
┌─────────┐                           │
│  Site   │ ◄────────────────────────┘
│         │
│ (knows only verification_hash
│  as persistent user ID)
└─────────┘
```

## Components

| Module | File | Role |
|--------|------|------|
| `crypto` | `anonymous_identity/crypto.py` | SHA-256 `hash_concat`, Ed25519 key generation |
| `client` | `anonymous_identity/client.py` | Client-side credential hashing, token derivation, signing |
| `sso` | `anonymous_identity/sso.py` | SSO server: register, authenticate, route_message |
| `site` | `anonymous_identity/site.py` | Site-side Ed25519 signature verification |

## Privacy Properties

### 1. Credential Hash
The client computes `hash(username ‖ password)` locally. Only the hash is sent to the SSO. The SSO never sees the plaintext password.

### 2. Site-Specific Token Derivation
For each site, the client derives:
```
token = SHA256(username ‖ site_id ‖ user_id ‖ user_salt)
```
Entirely client-side. The SSO provides `user_id` and `user_salt` during authentication but never learns which `site_id` is being used.

### 3. Unlinkability
Tokens for the same user on different sites are computationally unlinkable. SHA-256 preimage resistance ensures no party can correlate `token_A` (site A) with `token_B` (site B) without knowing the user's credentials.

### 4. Token Construction
The client signs a token `T = {verification_hash, site_id, timestamp, proof_of_human_score}` with Ed25519. The site verifies the signature and uses `verification_hash` as the user's persistent site-local identifier.

### 5. Routing Key Derivation
```
routing_key = SHA256(username ‖ "routing" ‖ site_id ‖ user_id ‖ user_salt)
```
Sites send messages to the SSO addressed to a routing key. The SSO resolves it to the user's contact info, delivers the message, and discards the plaintext.

### 6. SSO Protocol
- `register(credential_hash, encrypted_contact) → user_id`
- `authenticate(credential_hash) → (user_id, user_salt, session)`
- `route_message(routing_key, encrypted_payload) → delivered`

## Usage

```python
from anonymous_identity import Client, SSOServer, SiteVerifier

# Setup
sso = SSOServer()

# Registration (client-side)
alice = Client("alice", "s3cret_password")
user_id = sso.register(alice.credential_hash, "encrypted:alice@email.com")

# Authentication
uid, salt, session = sso.authenticate(alice.credential_hash)
alice.receive_auth(uid, salt, session)

# Visit a site anonymously
signed_token = alice.build_signed_token("forum.example.com", proof_of_human_score=0.99)

# Site verifies
site = SiteVerifier("forum.example.com")
result = site.verify_token(signed_token)
print(result["verification_hash"])  # persistent site-local user ID

# Routing: site sends alice a message without knowing her contact info
rk = alice.derive_routing_key("forum.example.com")
sso.register_routing_key(rk, alice.credential_hash)
sso.route_message(rk, "encrypted: You have a new reply!")
```

## Running Tests

```bash
pip install pynacl pytest
python -m pytest tests/test_anonymous_identity.py -v
```

### Test Coverage

| Test | Property |
|------|----------|
| (a) Same user, two sites → different & unlinkable tokens | Unlinkability |
| (b) Different users, same site → no collision | Collision resistance |
| (c) Ed25519 signature verifies; tampered tokens rejected | Token integrity |
| (d) Routing key resolves to correct user | Message routing |
| (e) SSO cannot derive site-specific tokens (no site_id) | SSO blindness |
| (f) Site cannot derive tokens for other sites (no user_salt) | Site isolation |

## Dependencies

- **Python 3.10+**
- **PyNaCl** (libsodium bindings for Ed25519)
- **pytest** (for tests)

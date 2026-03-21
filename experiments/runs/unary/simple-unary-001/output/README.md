# Anonymous Identity System

A privacy-preserving internet architecture where:

- A user authenticates **once** to an SSO.
- The SSO **never learns** which sites the user visits.
- Sites **never learn** the user's real identity.
- Two sites **cannot correlate** that they're serving the same user.

## Architecture

```
┌──────────┐         credential_hash          ┌──────────┐
│          │ ──────────────────────────────▶   │          │
│  Client  │   ◀── (user_id, user_salt, sess) │   SSO    │
│          │                                   │          │
│ holds:   │    routing_key, encrypted_msg     │ stores:  │
│ username │ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ▶│ cred→uid │
│ password │                                   │ rk→uid   │
└──────────┘                                   └──────────┘
      │
      │  signed token (verification_hash,
      │   site_id, timestamp, poh, signature)
      ▼
┌──────────┐
│   Site   │
│          │
│ sees:    │
│ ver_hash │  (persistent site-local identity)
│ pub_key  │
│ site_id  │
└──────────┘
```

## Principals & What They Know

| Secret           | Client | SSO | Site |
|------------------|--------|-----|------|
| username         | ✅      | ❌   | ❌    |
| password         | ✅      | ❌   | ❌    |
| credential_hash  | ✅      | ✅   | ❌    |
| user_id          | ✅      | ✅   | ❌    |
| user_salt        | ✅      | ✅   | ❌    |
| site_id          | ✅      | ❌   | ✅    |
| site_token       | ✅      | ❌   | ❌    |
| verification_hash| ✅      | ❌   | ✅    |
| routing_key      | ✅      | ✅*  | ✅    |
| contact_info     | ✅      | ✅†  | ❌    |

\* SSO sees the opaque key but cannot reverse it to learn site_id.
† Stored encrypted.

## Properties

### 1. Credential Hash
```python
credential_hash = SHA256(username || password)
```
Computed client-side. Only the hash is transmitted. The SSO never sees the password.

### 2. Site-Specific Token Derivation
```python
site_token = HMAC-SHA256(key=user_salt, msg="site_token" || username || site_id || user_id)
```
Computed entirely client-side. The SSO provides `user_id` and `user_salt` during authentication but never learns which `site_id` is used.

### 3. Unlinkability
Tokens for the same user on different sites are computationally unlinkable. The HMAC construction with domain separation ensures that `token_A` and `token_B` are independent without knowledge of the user's credentials.

### 4. Token Construction
```python
T = {verification_hash, site_id, timestamp, proof_of_human_score}
signature = Ed25519.sign(T, client_private_key)
```
The site verifies the signature and uses `verification_hash` as the user's persistent site-local identifier.

### 5. Routing Key Derivation
```python
routing_key = HMAC-SHA256(key=user_salt, msg="routing" || username || site_id || user_id)
```
Sites send messages to the SSO addressed to a routing key. The SSO resolves it to the user's contact info, delivers the message, and discards the plaintext. The site never learns the user's contact info.

### 6. SSO Protocol
- `register(credential_hash, encrypted_contact) → user_id`
- `authenticate(credential_hash) → (user_id, user_salt, session_id)`
- `route_message(routing_key, encrypted_payload) → "delivered"`

## File Structure

```
anonymous_identity/
├── __init__.py       # Package entry point & architecture docstring
├── crypto.py         # All cryptographic primitives (hash, HMAC, Ed25519)
├── client.py         # Client agent (holds credentials, derives tokens)
├── sso.py            # SSO server (register, authenticate, route_message)
└── site.py           # Relying-party site (verify tokens, send messages)

tests/
└── test_anonymous_identity.py   # 51 tests covering all 7 properties
```

## Running Tests

```bash
pip install pynacl pytest
python -m pytest tests/test_anonymous_identity.py -v
```

## Test Coverage

| Spec Requirement | Test Class | Count |
|-----------------|------------|-------|
| (a) Same user, two sites → different tokens | `TestUnlinkability` | 6 |
| (b) Different users, same site → no collision | `TestUnlinkability` | 3 |
| (c) Token signature verifies | `TestTokenSignature` | 10 |
| (d) Routing key correctly resolves | `TestRoutingKey` | 5 |
| (e) SSO cannot derive site tokens | `TestSSOCannotDeriveTokens` | 4 |
| (f) Site cannot derive other tokens | `TestSiteCannotDeriveOtherTokens` | 3 |
| End-to-end integration | `TestEndToEnd` | 4 |
| Credential hash properties | `TestCredentialHash` | 6 |
| SSO protocol correctness | `TestSSOProtocol` | 7 |
| Bit distribution uniformity | `TestUnlinkability` | 1 |

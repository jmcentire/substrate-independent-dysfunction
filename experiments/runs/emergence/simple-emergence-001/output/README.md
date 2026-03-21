# Anonymous Identity SSO

A privacy-preserving Single Sign-On system that provides anonymous identity tokens.

## Quick start

```bash
pip install -r requirements.txt
```

### Running the API server

```bash
# Development server
python api_server.py

# Or with uvicorn directly
uvicorn api_server:app --reload --host 0.0.0.0 --port 8000
```

The interactive API docs are available at **http://localhost:8000/docs** (Swagger UI)
and **http://localhost:8000/redoc** (ReDoc).

### API Endpoints

| Method | Path        | Description                                |
|--------|-------------|--------------------------------------------|
| GET    | `/health`   | Liveness / readiness probe                 |
| POST   | `/register` | Register a new user                        |
| POST   | `/login`    | Authenticate (login) an existing user      |
| POST   | `/token`    | Generate an anonymous identity token       |
| POST   | `/verify`   | Verify a previously-issued token           |
| POST   | `/sites`    | Register a new relying-party site          |

### Running tests

```bash
pytest
```

## Architecture

- **`sso_server.py`** – core SSO logic (user management, token generation, verification)
- **`api_server.py`** – FastAPI HTTP/REST layer on top of `SSOServer`
- **`anonymous_identity/`** – cryptographic utilities, client, and site verifier
- **`tests/`** – test suite

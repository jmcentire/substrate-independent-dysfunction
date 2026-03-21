"""FastAPI HTTP/REST API layer on top of SSOServer.

Provides RESTful endpoints for:
  - User registration
  - User authentication / login
  - Token generation (anonymous identity tokens)
  - Token verification
  - Site registration
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field

from sso_server import SSOServer

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Application & shared SSOServer instance
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Anonymous Identity SSO API",
    description="HTTP/REST API for the Anonymous Identity SSO server.",
    version="0.1.0",
)

# A module-level SSOServer instance used by all endpoints.
# It can be replaced (e.g. in tests) via `app.state.sso`.
_sso: Optional[SSOServer] = None


def get_sso() -> SSOServer:
    """Return the current SSOServer instance, creating one if needed."""
    global _sso
    if _sso is None:
        _sso = SSOServer()
    return _sso


def set_sso(sso: SSOServer) -> None:
    """Replace the module-level SSOServer (useful for testing)."""
    global _sso
    _sso = sso


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=1, description="Unique username")
    password: str = Field(..., min_length=1, description="User password")


class RegisterResponse(BaseModel):
    message: str
    username: str


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class LoginResponse(BaseModel):
    message: str
    username: str
    session_token: Optional[str] = None


class TokenRequest(BaseModel):
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)
    site_id: str = Field(..., min_length=1, description="Target site identifier")


class TokenResponse(BaseModel):
    token: Any  # the shape depends on the SSOServer implementation
    site_id: str


class VerifyTokenRequest(BaseModel):
    token: Any
    site_id: str = Field(..., min_length=1)


class VerifyTokenResponse(BaseModel):
    valid: bool
    identity: Optional[Any] = None


class SiteRegisterRequest(BaseModel):
    site_id: str = Field(..., min_length=1, description="Unique site identifier")
    site_url: Optional[str] = Field(None, description="Callback / base URL of the site")


class SiteRegisterResponse(BaseModel):
    message: str
    site_id: str


class HealthResponse(BaseModel):
    status: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health", response_model=HealthResponse, tags=["meta"])
def health_check():
    """Simple liveness / readiness probe."""
    return {"status": "ok"}


@app.post(
    "/register",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["auth"],
)
def register_user(body: RegisterRequest):
    """Register a new user with the SSO server."""
    sso = get_sso()
    try:
        result = sso.register_user(body.username, body.password)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    # `register_user` may return None or a dict – normalise.
    if isinstance(result, dict):
        return {"message": "User registered successfully", "username": body.username, **result}
    return {"message": "User registered successfully", "username": body.username}


@app.post("/login", response_model=LoginResponse, tags=["auth"])
def login(body: LoginRequest):
    """Authenticate an existing user."""
    sso = get_sso()
    try:
        result = sso.authenticate(body.username, body.password)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        )
    if not result:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )
    resp: Dict[str, Any] = {"message": "Login successful", "username": body.username}
    if isinstance(result, dict) and "session_token" in result:
        resp["session_token"] = result["session_token"]
    elif isinstance(result, str):
        resp["session_token"] = result
    return resp


@app.post("/token", response_model=TokenResponse, tags=["tokens"])
def generate_token(body: TokenRequest):
    """Generate an anonymous identity token for a given site."""
    sso = get_sso()
    # First authenticate
    try:
        auth_result = sso.authenticate(body.username, body.password)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        )
    if not auth_result:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )
    # Generate token
    try:
        token = sso.generate_token(body.username, body.site_id)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    return {"token": token, "site_id": body.site_id}


@app.post("/verify", response_model=VerifyTokenResponse, tags=["tokens"])
def verify_token(body: VerifyTokenRequest):
    """Verify a previously-issued token."""
    sso = get_sso()
    try:
        result = sso.verify_token(body.token, body.site_id)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    if isinstance(result, dict):
        return {"valid": True, "identity": result}
    if result:
        return {"valid": True, "identity": result}
    return {"valid": False}


@app.post(
    "/sites",
    response_model=SiteRegisterResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["sites"],
)
def register_site(body: SiteRegisterRequest):
    """Register a new relying-party site."""
    sso = get_sso()
    try:
        if body.site_url is not None:
            sso.register_site(body.site_id, body.site_url)
        else:
            sso.register_site(body.site_id)
    except TypeError:
        # Fallback if register_site only accepts site_id
        try:
            sso.register_site(body.site_id)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    return {"message": "Site registered successfully", "site_id": body.site_id}


# ---------------------------------------------------------------------------
# Entrypoint (for running directly: `python api_server.py`)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)

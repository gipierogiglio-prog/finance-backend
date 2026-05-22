"""
Authentication module — HMAC-based token auth (no external deps).
"""

import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

# ── Config ───────────────────────────────────────────────────────

_DEFAULT_SECRET = secrets.token_hex(32)
SECRET_KEY = os.getenv("JWT_SECRET", _DEFAULT_SECRET).encode("utf-8")
ACCESS_TOKEN_EXPIRE_HOURS = 24

API_USERNAME = os.getenv("API_USERNAME", "admin")
API_PASSWORD = os.getenv("API_PASSWORD", "admin123")

security = HTTPBearer(auto_error=False)


# ── Schemas ──────────────────────────────────────────────────────


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


# ── Token helpers ────────────────────────────────────────────────


def _sign_payload(payload: str) -> str:
    """Create HMAC-SHA256 signature for a payload string."""
    return hmac.new(SECRET_KEY, payload.encode("utf-8"), hashlib.sha256).hexdigest()


def create_access_token(username: str) -> str:
    """Create a signed token with expiration (HMAC-based, no PyJWT)."""
    payload = json.dumps(
        {"sub": username, "exp": int(time.time()) + ACCESS_TOKEN_EXPIRE_HOURS * 3600},
        separators=(",", ":"),
    )
    signature = _sign_payload(payload)
    # Format: base64(payload).base64(signature)
    import base64

    enc_payload = base64.urlsafe_b64encode(payload.encode("utf-8")).decode("utf-8").rstrip("=")
    enc_sig = base64.urlsafe_b64encode(signature.encode("utf-8")).decode("utf-8").rstrip("=")
    return f"{enc_payload}.{enc_sig}"


def verify_credentials(username: str, password: str) -> bool:
    """Check username and password against env vars."""
    return username == API_USERNAME and password == API_PASSWORD


# ── Dependency ───────────────────────────────────────────────────


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> str:
    """FastAPI dependency: extract and validate Bearer token.

    Returns the username (subject) from the token on success.
    Raises 401 on missing, expired, or invalid tokens.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token de acesso não fornecido",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials
    import base64

    try:
        parts = token.split(".")
        if len(parts) != 2:
            raise ValueError("Invalid token format")

        enc_payload, enc_sig = parts

        # Decode payload
        payload_bytes = base64.urlsafe_b64decode(enc_payload + "==")
        payload = json.loads(payload_bytes)

        # Verify signature
        expected_sig = _sign_payload(payload_bytes.decode("utf-8"))
        if enc_sig != base64.urlsafe_b64encode(expected_sig.encode("utf-8")).decode("utf-8").rstrip("="):
            raise ValueError("Invalid signature")

        # Check expiration
        if payload.get("exp", 0) < time.time():
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token expirado",
                headers={"WWW-Authenticate": "Bearer"},
            )

        return payload.get("sub", "unknown")

    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido",
            headers={"WWW-Authenticate": "Bearer"},
        )

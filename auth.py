"""
Authentication module — HMAC-based token auth with bcrypt password hashing
and multi-user support via the users table.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Optional

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

# ── Config ───────────────────────────────────────────────────────

_DEFAULT_SECRET = secrets.token_hex(32)
SECRET_KEY = os.getenv("JWT_SECRET", _DEFAULT_SECRET).encode("utf-8")
ACCESS_TOKEN_EXPIRE_HOURS = 24

security = HTTPBearer(auto_error=False)


# ── Schemas ──────────────────────────────────────────────────────


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class RegisterRequest(BaseModel):
    username: str
    password: str
    display_name: str = ""


# ── Token helpers ────────────────────────────────────────────────


def _sign_payload(payload: str) -> str:
    """Create HMAC-SHA256 signature for a payload string."""
    return hmac.new(SECRET_KEY, payload.encode("utf-8"), hashlib.sha256).hexdigest()


def create_access_token(user_id: int, username: str) -> str:
    """Create a signed token with expiration (HMAC-based, no PyJWT).

    Payload includes user_id and username for multi-user support.
    """
    payload = json.dumps(
        {
            "sub": username,
            "user_id": user_id,
            "exp": int(time.time()) + ACCESS_TOKEN_EXPIRE_HOURS * 3600,
        },
        separators=(",", ":"),
    )
    signature = _sign_payload(payload)

    enc_payload = base64.urlsafe_b64encode(payload.encode("utf-8")).decode("utf-8").rstrip("=")
    enc_sig = base64.urlsafe_b64encode(signature.encode("utf-8")).decode("utf-8").rstrip("=")
    return f"{enc_payload}.{enc_sig}"


def verify_credentials(username: str, password: str) -> Optional[dict]:
    """Check username and password against the users table with bcrypt.

    Returns user dict on success (with id, username, display_name), None on failure.
    """
    from database import get_db, get_user_with_password_hash

    with get_db() as conn:
        user = get_user_with_password_hash(conn, username)
        if user is None:
            return None
        try:
            if bcrypt.checkpw(
                password.encode("utf-8"), user["password_hash"].encode("utf-8")
            ):
                return {"id": user["id"], "username": user["username"], "display_name": user["display_name"]}
        except Exception:
            return None
    return None


def register_user(username: str, password: str, display_name: str = "") -> Optional[dict]:
    """Register a new user with bcrypt-hashed password.

    Returns the created user dict, or None if username already exists.
    """
    from database import get_db, get_user_by_username

    with get_db() as conn:
        existing = get_user_by_username(conn, username)
        if existing:
            return None

        hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        conn.execute(
            "INSERT INTO users (username, password_hash, display_name) VALUES (?, ?, ?)",
            (username, hashed, display_name),
        )
        user_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    return {"id": user_id, "username": username, "display_name": display_name}


# ── Dependency ───────────────────────────────────────────────────


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> dict:
    """FastAPI dependency: extract and validate Bearer token.

    Returns a dict with 'id' (user_id) and 'username' on success.
    Raises 401 on missing, expired, or invalid tokens.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token de acesso não fornecido",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials

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

        return {
            "id": payload.get("user_id", 1),
            "username": payload.get("sub", "unknown"),
        }

    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido",
            headers={"WWW-Authenticate": "Bearer"},
        )
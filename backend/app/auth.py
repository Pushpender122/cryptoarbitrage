from __future__ import annotations
import base64, hashlib, hmac, json, secrets, time
from collections import defaultdict, deque
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from .config import settings

SESSION_COOKIE = "arbiter_session"
CSRF_COOKIE = "arbiter_csrf"
auth_router = APIRouter(prefix="/api/auth", tags=["authentication"])
_attempts: dict[str, deque[float]] = defaultdict(deque)

def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")

def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))

def auth_configured() -> bool:
    return bool(settings.admin_password and len(settings.session_secret) >= 32)

def create_session() -> tuple[str, str]:
    now = int(time.time())
    csrf = secrets.token_urlsafe(32)
    payload = _b64(json.dumps({"sub":"admin","iat":now,"exp":now+settings.session_ttl_seconds,"csrf":csrf}, separators=(",",":")).encode())
    signature = _b64(hmac.new(settings.session_secret.encode(), payload.encode(), hashlib.sha256).digest())
    return payload + "." + signature, csrf

def verify_session_token(token: str | None) -> dict[str, Any] | None:
    if not token or not auth_configured():
        return None
    try:
        part, signature = token.split(".", 1)
        expected = _b64(hmac.new(settings.session_secret.encode(), part.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            return None
        payload = json.loads(_unb64(part))
        if payload.get("sub") != "admin" or int(payload.get("exp", 0)) <= int(time.time()):
            return None
        return payload if isinstance(payload.get("csrf"), str) else None
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None

def require_auth(request: Request) -> dict[str, Any]:
    if not auth_configured():
        raise HTTPException(503, "Set ADMIN_PASSWORD and a SESSION_SECRET of at least 32 characters.")
    session = verify_session_token(request.cookies.get(SESSION_COOKIE))
    if not session:
        raise HTTPException(401, "Authentication required")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        csrf = str(session["csrf"])
        if not (hmac.compare_digest(request.headers.get("x-csrf-token", ""), csrf) and hmac.compare_digest(request.cookies.get(CSRF_COOKIE, ""), csrf)):
            raise HTTPException(403, "CSRF token missing or invalid")
    return session

class LoginPayload(BaseModel):
    password: str = Field(min_length=1, max_length=256)

def _rate_limit(request: Request) -> None:
    ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    attempts = _attempts[ip]
    while attempts and now - attempts[0] > 900:
        attempts.popleft()
    if len(attempts) >= 8:
        raise HTTPException(429, "Too many login attempts. Try again in 15 minutes.")
    attempts.append(now)

@auth_router.post("/login")
def login(payload: LoginPayload, request: Request, response: Response):
    if not auth_configured():
        raise HTTPException(503, "Server authentication is not configured.")
    _rate_limit(request)
    if not hmac.compare_digest(payload.password.encode(), settings.admin_password.encode()):
        raise HTTPException(401, "Incorrect password")
    _attempts.pop(request.client.host if request.client else "unknown", None)
    token, csrf = create_session()
    response.set_cookie(SESSION_COOKIE, token, max_age=settings.session_ttl_seconds, httponly=True, secure=settings.session_cookie_secure, samesite="strict", path="/")
    response.set_cookie(CSRF_COOKIE, csrf, max_age=settings.session_ttl_seconds, httponly=False, secure=settings.session_cookie_secure, samesite="strict", path="/")
    return {"ok": True, "csrf_token": csrf, "expires_in": settings.session_ttl_seconds}

@auth_router.get("/me")
def me(session: dict[str, Any] = Depends(require_auth)):
    return {"authenticated": True, "csrf_token": session["csrf"], "expires_at": session["exp"]}

@auth_router.post("/logout")
def logout(response: Response, _: dict[str, Any] = Depends(require_auth)):
    response.delete_cookie(SESSION_COOKIE, path="/", secure=settings.session_cookie_secure, httponly=True, samesite="strict")
    response.delete_cookie(CSRF_COOKIE, path="/", secure=settings.session_cookie_secure, httponly=False, samesite="strict")
    return {"ok": True}

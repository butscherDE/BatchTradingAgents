"""Authentication middleware — shared password with signed session cookie."""

import hashlib
import hmac
import time

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse


SESSION_COOKIE = "ta_session"
SESSION_MAX_AGE = 86400 * 7  # 7 days


class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, password_hash: str, secret_key: str):
        super().__init__(app)
        self.password_hash = password_hash
        self.secret_key = secret_key

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        if request.method == "OPTIONS":
            return await call_next(request)

        if path in ("/api/auth/login", "/api/health"):
            return await call_next(request)

        if path == "/ws":
            token = request.query_params.get("token")
            if token and self._verify_session(token):
                return await call_next(request)
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)

        session = request.cookies.get(SESSION_COOKIE)
        if session and self._verify_session(session):
            return await call_next(request)

        if path.startswith("/api/"):
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)

        return await call_next(request)

    def _verify_session(self, token: str) -> bool:
        try:
            payload, sig = token.rsplit(".", 1)
            expected = _sign(self.secret_key, payload)
            if not hmac.compare_digest(sig, expected):
                return False
            ts = int(payload)
            return (time.time() - ts) < SESSION_MAX_AGE
        except Exception:
            return False

    def create_session(self) -> str:
        payload = str(int(time.time()))
        sig = _sign(self.secret_key, payload)
        return f"{payload}.{sig}"


def _sign(secret: str, payload: str) -> str:
    return hmac.HMAC(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def verify_password(password: str, password_hash: str) -> bool:
    return hmac.compare_digest(hash_password(password), password_hash)

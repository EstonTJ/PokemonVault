"""Single-owner authentication: one password, signed HttpOnly session cookie, CSRF header check."""
from __future__ import annotations

import hmac
import logging
import os
import secrets
import time
from collections import defaultdict, deque
from typing import Deque, Dict

from fastapi import Depends, HTTPException, Request, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .config import settings

log = logging.getLogger("pokevault.auth")
COOKIE = "pv_session"
CSRF_HEADER = "x-pokevault"

_password: str = ""
_serializer: URLSafeTimedSerializer


def init_auth() -> None:
    global _password, _serializer
    settings.ensure_dirs()
    _password = settings.password
    if not _password:
        pw_file = settings.private_dir / ".owner_password"
        if pw_file.exists():
            _password = pw_file.read_text().strip()
        else:
            _password = secrets.token_urlsafe(18)
            pw_file.write_text(_password)
            os.chmod(pw_file, 0o600)
        log.warning("POKEVAULT_PASSWORD is not set. Using the generated owner password stored in %s "
                    "(set POKEVAULT_PASSWORD as a Replit Secret instead).", pw_file)
    secret = settings.secret_key
    if not secret:
        key_file = settings.private_dir / ".secret_key"
        if key_file.exists():
            secret = key_file.read_text().strip()
        else:
            secret = secrets.token_urlsafe(48)
            key_file.write_text(secret)
            os.chmod(key_file, 0o600)
    _serializer = URLSafeTimedSerializer(secret, salt="pokevault-session")


# Login rate limiting (per client address)
_attempts: Dict[str, Deque[float]] = defaultdict(deque)
MAX_ATTEMPTS = 8
WINDOW_S = 600


def _client(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def login(request: Request, response: Response, password: str) -> None:
    key = _client(request)
    q = _attempts[key]
    now = time.time()
    while q and now - q[0] > WINDOW_S:
        q.popleft()
    if len(q) >= MAX_ATTEMPTS:
        raise HTTPException(429, "Too many login attempts. Try again later.")
    q.append(now)
    if not hmac.compare_digest(password.encode(), _password.encode()):
        raise HTTPException(401, "Incorrect password.")
    q.clear()
    token = _serializer.dumps({"owner": True, "iat": int(now)})
    response.set_cookie(COOKIE, token, max_age=settings.session_max_age_s, httponly=True, samesite="strict",
                        secure=settings.secure_cookies, path="/")


def logout(response: Response) -> None:
    response.delete_cookie(COOKIE, path="/")


def is_authenticated(request: Request) -> bool:
    token = request.cookies.get(COOKIE)
    if not token:
        return False
    try:
        data = _serializer.loads(token, max_age=settings.session_max_age_s)
    except (BadSignature, SignatureExpired):
        return False
    return bool(data.get("owner"))


def require_owner(request: Request) -> None:
    if not is_authenticated(request):
        raise HTTPException(401, "Sign in required.")
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get(CSRF_HEADER) != "1":
        raise HTTPException(403, "Missing request header.")


Owner = Depends(require_owner)


# ------------------------------------------------------------------ upload key (iOS Shortcut)
# A separate secret that can ONLY upload recordings. It cannot read the collection,
# change settings or delete anything. Only its SHA-256 hash is stored.

UPLOAD_KEY_SETTING = "upload_key"
UPLOAD_KEY_PREFIX = "pvu_"


def _hash_key(raw: str) -> str:
    import hashlib

    return hashlib.sha256(raw.encode()).hexdigest()


def create_upload_key(db) -> str:
    from .models import AppSetting, utcnow

    raw = UPLOAD_KEY_PREFIX + secrets.token_urlsafe(32)
    row = db.get(AppSetting, UPLOAD_KEY_SETTING) or AppSetting(key=UPLOAD_KEY_SETTING)
    row.value = {"hash": _hash_key(raw), "created_at": utcnow().isoformat() + "Z", "last_used_at": None}
    db.merge(row)
    db.commit()
    return raw


def revoke_upload_key(db) -> None:
    from .models import AppSetting

    row = db.get(AppSetting, UPLOAD_KEY_SETTING)
    if row is not None:
        db.delete(row)
        db.commit()


def upload_key_info(db) -> dict:
    from .models import AppSetting

    row = db.get(AppSetting, UPLOAD_KEY_SETTING)
    if row is None or not row.value:
        return {"exists": False}
    return {"exists": True, "created_at": row.value.get("created_at"), "last_used_at": row.value.get("last_used_at")}


def check_upload_key(db, raw: str) -> bool:
    from .models import AppSetting, utcnow

    row = db.get(AppSetting, UPLOAD_KEY_SETTING)
    if row is None or not row.value or not raw.startswith(UPLOAD_KEY_PREFIX):
        return False
    if not hmac.compare_digest(_hash_key(raw), row.value["hash"]):
        return False
    row.value = {**row.value, "last_used_at": utcnow().isoformat() + "Z"}
    db.commit()
    return True


def require_owner_or_upload_key(request: Request) -> str:
    """Session (with CSRF header) or `Authorization: Bearer pvu_…`. Returns the auth method used."""
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        from .db import SessionLocal

        key = header[7:].strip()
        _throttle(request)
        with SessionLocal() as db:
            if check_upload_key(db, key):
                return "upload_key"
        raise HTTPException(401, "Invalid upload key.")
    require_owner(request)
    return "session"


def _throttle(request: Request) -> None:
    """Share the login limiter for key attempts so keys can't be brute-forced."""
    key = "key:" + _client(request)
    q = _attempts[key]
    now = time.time()
    while q and now - q[0] > WINDOW_S:
        q.popleft()
    if len(q) >= MAX_ATTEMPTS * 10:
        raise HTTPException(429, "Too many upload attempts. Try again later.")
    q.append(now)

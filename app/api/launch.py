import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from datetime import datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import AppUser, LaunchNonce
from app.profile.store import accessible_companies

MAX_TTL_S = 300
TARGET_RE = re.compile(r"^(home|feed|analyze|profile|insight/[A-Za-z0-9_-]{1,64}|event/[A-Za-z0-9_-]{1,64})$")


class LaunchError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _sig(payload: str) -> str:
    key = get_settings().host_launch_secret.encode()
    return _b64(hmac.new(key, payload.encode(), hashlib.sha256).digest())


def safe_target(target: str | None) -> str:
    return target if target and TARGET_RE.match(target) else "home"


def sign_launch(login: str, company: str, target: str | None = None, ttl_s: int = 120) -> str:
    claims = {
        "sub": login,
        "company": company,
        "exp": int(time.time()) + min(ttl_s, MAX_TTL_S),
        "nonce": secrets.token_urlsafe(16),
    }
    if target:
        claims["target"] = target
    payload = _b64(json.dumps(claims, separators=(",", ":")).encode())
    return f"{payload}.{_sig(payload)}"


def verify_launch(db: Session, token: str) -> tuple[AppUser, str, str]:
    try:
        payload, sig = token.split(".", 1)
    except ValueError:
        raise LaunchError("launch_malformed", "Некорректная ссылка входа") from None
    if not hmac.compare_digest(sig, _sig(payload)):
        raise LaunchError("launch_bad_signature", "Подпись ссылки входа не прошла проверку")
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (ValueError, json.JSONDecodeError):
        raise LaunchError("launch_malformed", "Некорректная ссылка входа") from None

    now = time.time()
    exp = claims.get("exp")
    if not isinstance(exp, int) or exp < now:
        raise LaunchError(
            "launch_expired", "Ссылка входа устарела. Откройте сервис из интернет-банка ещё раз"
        )
    if exp > now + MAX_TTL_S + 5:
        raise LaunchError("launch_ttl_too_long", "Срок действия ссылки входа больше допустимого")
    nonce = claims.get("nonce")
    if not isinstance(nonce, str) or not 8 <= len(nonce) <= 64:
        raise LaunchError("launch_malformed", "Некорректная ссылка входа")

    user = db.get(AppUser, str(claims.get("sub", "")))
    if user is None:
        raise LaunchError("launch_unknown_user", "Пользователь не подключён к сервису")
    if claims.get("company") not in accessible_companies(db, user):
        raise LaunchError(
            "launch_company_mismatch", "Компания в ссылке не совпадает с компанией пользователя"
        )

    db.execute(delete(LaunchNonce).where(LaunchNonce.exp < datetime.utcnow()))
    if db.get(LaunchNonce, nonce) is not None:
        raise LaunchError("launch_replayed", "Ссылка входа уже использована")
    db.add(LaunchNonce(nonce=nonce, exp=datetime.utcfromtimestamp(exp) + timedelta(seconds=5)))
    db.flush()
    return user, safe_target(claims.get("target")), claims["company"]

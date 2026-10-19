import base64
import hashlib
import hmac
import json
import time
from collections import defaultdict, deque

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import AppUser, AuditLog
from app.db.session import get_db

TOKEN_TTL_S = 12 * 3600


def _sign(payload: bytes) -> str:
    key = get_settings().session_secret.encode()
    return base64.urlsafe_b64encode(hmac.new(key, payload, hashlib.sha256).digest()).decode().rstrip("=")


def issue_token(login: str) -> str:
    payload = base64.urlsafe_b64encode(
        json.dumps({"sub": login, "exp": int(time.time()) + TOKEN_TTL_S}).encode()
    )
    return payload.decode().rstrip("=") + "." + _sign(payload.rstrip(b"="))


def parse_token(token: str) -> str | None:
    try:
        payload, sig = token.split(".", 1)
        if not hmac.compare_digest(sig, _sign(payload.encode())):
            return None
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        if data.get("exp", 0) < time.time():
            return None
        return data.get("sub")
    except (ValueError, json.JSONDecodeError):
        return None


def current_user(authorization: str | None = Header(default=None), db: Session = Depends(get_db)) -> AppUser:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail={"code": "unauthorized", "message": "Требуется вход"})
    login = parse_token(authorization.split(" ", 1)[1])
    user = db.get(AppUser, login) if login else None
    if user is None:
        raise HTTPException(
            status_code=401, detail={"code": "unauthorized", "message": "Сессия недействительна"}
        )
    return user


def admin_user(user: AppUser = Depends(current_user)) -> AppUser:
    if not user.is_admin:
        raise HTTPException(
            status_code=403, detail={"code": "forbidden", "message": "Только для администратора"}
        )
    return user


def audit(db: Session, user: str | None, action: str, result: str, detail: str | None = None) -> None:
    db.add(AuditLog(user=user, action=action, result=result, detail=detail))


class RateLimiter:
    def __init__(self):
        self.hits: dict[tuple, deque] = defaultdict(deque)

    def check(self, user: str, bucket: str, limit: int, window_s: int = 3600) -> None:
        q = self.hits[(user, bucket)]
        now = time.time()
        while q and q[0] < now - window_s:
            q.popleft()
        if len(q) >= limit:
            raise HTTPException(
                status_code=429,
                detail={"code": "rate_limited", "message": f"Лимит {limit} запросов в час исчерпан"},
            )
        q.append(now)


limiter = RateLimiter()

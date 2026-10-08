"""mod 用の匿名利用トークン。このサービス自身が発行し、外部の鍵を取得しない。

mod ID は自己申告であり、コードを実際に実行した証明ではない。
対応する利用開始手順と短命のトークンを API に必須とし、単純な直叩きを抑える。
"""

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from typing import Optional

import jwt
from fastapi import HTTPException, Request

from .config import (
    REQUIRE_SERVICE_TOKEN,
    SERVICE_ALLOWED_MODS,
    SERVICE_AUDIENCE,
    SERVICE_TOKEN_SECRET,
    SERVICE_TOKEN_TTL,
)

TOKEN_ISSUER = "video-player-service"


@dataclass(frozen=True)
class ServiceUser:
    """subject は接続元 IP の仮名。トークンを取り直しても回数制限のキーは変わらない。"""

    subject: str
    mod: str


class ServiceTokenError(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def client_host(request: Request) -> str:
    # ProxyHeadersMiddleware が信頼するプロキシの情報を反映した接続元を使う。
    # アプリから X-Forwarded-For を直接読むことはしない。
    return request.client.host if request.client else "unknown"


def service_subject(host: str, *, secret: str = SERVICE_TOKEN_SECRET) -> str:
    return hmac.new(secret.encode(), f"video-player/session\n{host}".encode(), hashlib.sha256).hexdigest()


def issue_service_token(
    host: str,
    mod: str,
    *,
    secret: str = SERVICE_TOKEN_SECRET,
    audience: str = SERVICE_AUDIENCE,
    now: Optional[int] = None,
) -> dict[str, object]:
    issued = int(time.time()) if now is None else now
    expires = issued + SERVICE_TOKEN_TTL
    token = jwt.encode(
        {"iss": TOKEN_ISSUER, "aud": audience, "sub": service_subject(host, secret=secret),
         "mod": mod, "iat": issued, "exp": expires, "jti": secrets.token_urlsafe(16)},
        secret,
        algorithm="HS256",
    )
    return {"token": token, "expiresAt": expires * 1000}


def verify_service_token(
    token: str,
    *,
    secret: str = SERVICE_TOKEN_SECRET,
    audience: str = SERVICE_AUDIENCE,
    allowed_mods: Optional[list[str]] = None,
) -> ServiceUser:
    try:
        claims = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            audience=audience,
            issuer=TOKEN_ISSUER,
            options={"require": ["exp", "iat", "aud", "iss", "sub", "mod", "jti"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise ServiceTokenError("expired") from exc
    except jwt.InvalidAudienceError as exc:
        raise ServiceTokenError("wrong-audience") from exc
    except jwt.InvalidSignatureError as exc:
        raise ServiceTokenError("bad-signature") from exc
    except jwt.PyJWTError as exc:
        raise ServiceTokenError("invalid") from exc
    mod = claims.get("mod")
    if not isinstance(mod, str) or mod not in (SERVICE_ALLOWED_MODS if allowed_mods is None else allowed_mods):
        raise ServiceTokenError("mod-not-allowed")
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject:
        raise ServiceTokenError("invalid")
    return ServiceUser(subject=subject, mod=mod)


def bearer_token(request: Request) -> Optional[str]:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer":
        return None
    return token.strip() or None


async def require_service_user(request: Request) -> ServiceUser:
    """トークンが無い・通らなければ 401。利用上限は接続元 IP を基準に数える。"""
    subject = service_subject(client_host(request))
    if not REQUIRE_SERVICE_TOKEN:
        return ServiceUser(subject=subject, mod="")
    token = bearer_token(request)
    if not token:
        raise HTTPException(
            status_code=401,
            detail={"error": "SERVICE_TOKEN_REQUIRED", "message": "mod の利用開始トークンが必要です"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        user = verify_service_token(token)
        # 他の接続元で取ったトークンを使っても、この接続元の上限を回避できない。
        return ServiceUser(subject=subject, mod=user.mod)
    except ServiceTokenError as exc:
        raise HTTPException(
            status_code=401,
            detail={"error": "SERVICE_TOKEN_INVALID", "reason": exc.reason},
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

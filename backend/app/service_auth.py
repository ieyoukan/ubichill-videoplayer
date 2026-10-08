"""Ubichill のサービストークン（短命の JWT / EdDSA）の検証。

手順は Ubichill の docs/SERVICE_TOKEN.md と同じ:
信頼する発行元か → 発行元の公開鍵（JWKS）で署名 → aud・期限・mod を確かめ、sub を利用者の識別子にする。
"""

from dataclasses import dataclass
from typing import Callable, Optional

import jwt
from fastapi import HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from jwt import PyJWKClient

from .config import (
    REQUIRE_SERVICE_TOKEN,
    SERVICE_ALLOWED_MODS,
    SERVICE_AUDIENCE,
    UBICHILL_ISSUERS,
)

KEYS_PATH = "/api/v1/service-tokens/keys"
CLOCK_SKEW_SECONDS = 30


@dataclass(frozen=True)
class ServiceUser:
    """依頼した利用者。subject は回数制限・記録のキー（サービスごとの仮名）。"""

    subject: str
    mod: str


class ServiceTokenError(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


KeyResolver = Callable[[str, str], object]
"""(発行元, トークン) → 署名を確かめる公開鍵。"""

_jwk_clients: dict[str, PyJWKClient] = {}


def _jwks_key(issuer: str, token: str) -> object:
    # PyJWKClient は鍵をキャッシュし、知らない kid が来たら取り直す。
    client = _jwk_clients.setdefault(
        issuer, PyJWKClient(f"{issuer}{KEYS_PATH}", cache_jwk_set=True, lifespan=300)
    )
    return client.get_signing_key_from_jwt(token).key


def verify_service_token(
    token: str,
    *,
    issuers: list[str],
    audience: str,
    allowed_mods: list[str],
    key_for: Optional[KeyResolver] = None,
) -> ServiceUser:
    try:
        unverified = jwt.decode(token, options={"verify_signature": False})
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise ServiceTokenError("malformed") from exc
    if header.get("alg") != "EdDSA":
        raise ServiceTokenError("unsupported-algorithm")
    issuer = unverified.get("iss")
    # 信頼しない発行元の鍵は取りに行かない（トークンに書かれた URL へ通信させないため）。
    if not isinstance(issuer, str) or issuer not in issuers:
        raise ServiceTokenError("untrusted-issuer")
    try:
        key = (key_for or _jwks_key)(issuer, token)
    except jwt.PyJWTError as exc:
        raise ServiceTokenError("unknown-key") from exc
    try:
        claims = jwt.decode(
            token,
            key,
            algorithms=["EdDSA"],
            audience=audience,
            issuer=issuer,
            leeway=CLOCK_SKEW_SECONDS,
            options={"require": ["exp", "iat", "aud", "iss", "sub"]},
        )
    except jwt.InvalidAudienceError as exc:
        raise ServiceTokenError("wrong-audience") from exc
    except jwt.ExpiredSignatureError as exc:
        raise ServiceTokenError("expired") from exc
    except jwt.InvalidSignatureError as exc:
        raise ServiceTokenError("bad-signature") from exc
    except jwt.PyJWTError as exc:
        raise ServiceTokenError("invalid") from exc
    mod = claims.get("mod")
    if not isinstance(mod, str) or mod not in allowed_mods:
        raise ServiceTokenError("mod-not-allowed")
    return ServiceUser(subject=str(claims["sub"]), mod=mod)


def bearer_token(request: Request) -> Optional[str]:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer":
        return None
    return token.strip() or None


async def require_service_user(request: Request) -> ServiceUser:
    """FastAPI の依存。トークンが無い・通らなければ 401。"""
    if not REQUIRE_SERVICE_TOKEN:
        host = request.client.host if request.client else "unknown"
        return ServiceUser(subject=f"ip:{host}", mod="")
    token = bearer_token(request)
    if not token:
        raise HTTPException(
            status_code=401,
            detail={"error": "SERVICE_TOKEN_REQUIRED", "message": "Ubichill からの依頼のみ受け付けます"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        # JWKS の取得は同期 I/O なのでスレッドで行う。
        return await run_in_threadpool(
            verify_service_token,
            token,
            issuers=UBICHILL_ISSUERS,
            audience=SERVICE_AUDIENCE,
            allowed_mods=SERVICE_ALLOWED_MODS,
        )
    except ServiceTokenError as exc:
        raise HTTPException(
            status_code=401,
            detail={"error": "SERVICE_TOKEN_INVALID", "reason": exc.reason},
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

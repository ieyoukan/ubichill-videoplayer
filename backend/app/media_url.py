"""/resolve が発行する再生 URL の署名。<video> はヘッダーを送れないので、URL 自体に短命の署名を付ける。"""

import hashlib
import hmac
import time
from typing import Optional

from .config import MEDIA_URL_SECRET, MEDIA_URL_TTL


def thumbnail_url(video_id: str) -> str:
    """サムネイルは各利用者のブラウザが YouTube の画像配信から直接読む（このサーバーを通さない）。"""
    return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"


def _signature(kind: str, video_id: str, expires: int, secret: str) -> str:
    message = f"file:{kind}:{video_id}:{expires}".encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def sign_media(kind: str, video_id: str, *, now: Optional[float] = None,
               ttl: int = MEDIA_URL_TTL, secret: str = MEDIA_URL_SECRET) -> dict[str, str]:
    """URL のクエリに付ける exp・sig。"""
    expires = int((time.time() if now is None else now) + ttl)
    return {"exp": str(expires), "sig": _signature(kind, video_id, expires, secret)}


def media_signature_valid(kind: str, video_id: str, exp: Optional[str], sig: Optional[str], *,
                          now: Optional[float] = None, secret: str = MEDIA_URL_SECRET) -> bool:
    if not exp or not sig or not exp.isdigit():
        return False
    if int(exp) < (time.time() if now is None else now):
        return False
    return hmac.compare_digest(_signature(kind, video_id, int(exp), secret), sig)

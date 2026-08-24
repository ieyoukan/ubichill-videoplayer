"""SSRF対策: プロキシ先URL・video_idの検証、リダイレクトを検証しながらの GET。"""

import ipaddress
import re
from urllib.parse import urljoin, urlparse

import httpx
from fastapi import HTTPException

# プロキシで許可するホストのパターン
_ALLOWED_PROXY_HOSTS = re.compile(
    r"^([\w-]+\.)*googlevideo\.com$"
    r"|^([\w-]+\.)*youtube\.com$"
    r"|^([\w-]+\.)*ytimg\.com$"
    r"|^([\w-]+\.)*ggpht\.com$"
)

# video_id バリデーション用正規表現（YouTubeのID形式: 英数字・ハイフン・アンダースコア 11文字）
_VIDEO_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{6,20}$")


def _validate_video_id(video_id: str) -> None:
    """video_idが妥当な形式かどうか検証"""
    if not _VIDEO_ID_PATTERN.match(video_id):
        raise HTTPException(status_code=400, detail="Invalid video ID format")


def _is_safe_proxy_url(url: str) -> bool:
    """プロキシ先URLが安全かどうか検証（SSRF対策）"""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        hostname = parsed.hostname
        if not hostname:
            return False
        # IPアドレスの直接指定を拒否（プライベートIP / メタデータエンドポイント対策）
        try:
            addr = ipaddress.ip_address(hostname)
            if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
                return False
        except ValueError:
            pass  # ホスト名の場合はパターンマッチで検証
        if not _ALLOWED_PROXY_HOSTS.match(hostname):
            return False
        return True
    except Exception:
        return False


async def _safe_get(
    client: httpx.AsyncClient,
    url: str,
    headers: dict,
    max_redirects: int = 5,
    stream: bool = False,
):
    """各リダイレクトホップを `_is_safe_proxy_url` で検証しながら GET する。

    `follow_redirects=True` をそのまま使うと「許可ホストが 302 で内部 IP を返す」
    ような SSRF が成立し得るため、ホップごとに自前で allowlist 検証する。
    """
    current_url = url
    for _ in range(max_redirects + 1):
        if not _is_safe_proxy_url(current_url):
            raise HTTPException(status_code=403, detail="Redirect target URL is not allowed")
        if stream:
            req = client.build_request("GET", current_url, headers=headers)
            response = await client.send(req, stream=True)
        else:
            response = await client.get(current_url, headers=headers)
        if response.is_redirect:
            next_url = response.headers.get("location")
            if not next_url:
                return response
            # 相対 URL は現在 URL を base に展開
            current_url = urljoin(current_url, next_url)
            if stream:
                await response.aclose()
            continue
        return response
    raise HTTPException(status_code=508, detail="Too many redirects")

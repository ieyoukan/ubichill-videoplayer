"""YouTubeサムネイルのプロキシ（CSP回避・同一オリジン配信）。"""

import httpx
from fastapi import APIRouter, Response

from ..config import UPSTREAM_SOURCE_ADDRESS
from ..security import _safe_get, _validate_video_id

router = APIRouter()


@router.get("/thumbnail/{video_id}", name="get_thumbnail")
async def get_thumbnail(video_id: str):
    """YouTubeサムネイルをプロキシ（CSP回避・同一オリジン配信）"""
    _validate_video_id(video_id)
    thumbnail_url = f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg"
    try:
        transport = httpx.AsyncHTTPTransport(local_address=UPSTREAM_SOURCE_ADDRESS)
        async with httpx.AsyncClient(transport=transport, follow_redirects=False, timeout=10.0) as client:
            response = await _safe_get(
                client,
                thumbnail_url,
                {"Referer": "https://www.youtube.com/"},
            )
            response.raise_for_status()
            return Response(
                content=response.content,
                media_type=response.headers.get("content-type", "image/jpeg"),
                headers={"Cache-Control": "public, max-age=86400"},
            )
    except Exception:
        # Browser を YouTube CDN へ直接 redirect せず、通信境界を mod backend に保つ。
        return Response(status_code=502, content=b"", media_type="image/jpeg")

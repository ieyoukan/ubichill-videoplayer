"""YouTubeサムネイルのプロキシ（CSP回避・同一オリジン配信）。"""

import httpx
from fastapi import APIRouter, Response
from fastapi.responses import RedirectResponse

from ..security import _validate_video_id

router = APIRouter()


@router.get("/thumbnail/{video_id}")
async def get_thumbnail(video_id: str):
    """YouTubeサムネイルをプロキシ（CSP回避・同一オリジン配信）"""
    _validate_video_id(video_id)
    thumbnail_url = f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg"
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10.0) as client:
            response = await client.get(
                thumbnail_url,
                headers={"Referer": "https://www.youtube.com/"},
            )
            response.raise_for_status()
            return Response(
                content=response.content,
                media_type=response.headers.get("content-type", "image/jpeg"),
                headers={"Cache-Control": "public, max-age=86400"},
            )
    except Exception:
        # フォールバック: ytimg に直接リダイレクト
        return RedirectResponse(url=thumbnail_url, status_code=302)

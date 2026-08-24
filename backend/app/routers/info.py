"""動画情報の取得。"""

from typing import Any, Dict

import yt_dlp
from fastapi import APIRouter, HTTPException, Request

from ..cache import TTLCache
from ..config import CACHE_INFO_TTL, CACHE_MAX_SIZE
from ..security import _validate_video_id
from ..ytdlp_client import YTDLPError, _base_ydl_opts, _run_ytdlp

router = APIRouter()

_info_cache = TTLCache(max_size=CACHE_MAX_SIZE)


def _yt_info(video_id: str) -> dict:
    ydl_opts: Dict[str, Any] = {**_base_ydl_opts(), "extract_flat": False}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)


@router.get("/info/{video_id}")
async def get_video_info(video_id: str, request: Request):
    """動画情報を取得（TTL キャッシュ付き）"""
    _validate_video_id(video_id)
    cache_key = f"info:{video_id}"
    cached = _info_cache.get(cache_key)
    if cached is not None:
        return cached
    try:
        info = await _run_ytdlp(_yt_info, video_id)
        base_url = f"{request.url.scheme}://{request.url.netloc}"
        result = {
            "id": info.get("id"),
            "title": info.get("title", "Unknown"),
            "thumbnail": info.get("thumbnail") or f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg",
            "duration": info.get("duration", 0),
            "author": info.get("uploader", "Unknown"),
            "streamUrl": f"{base_url}/api/stream/video/{video_id}",
        }
        _info_cache.set(cache_key, result, CACHE_INFO_TTL)
        return result
    except YTDLPError as e:
        raise HTTPException(status_code=e.status_code, detail={"error": e.kind, "message": str(e)})
    except HTTPException:
        raise
    except Exception as e:
        error_msg = str(e)
        if "Requested format is not available" in error_msg:
            raise HTTPException(
                status_code=400,
                detail={"error": "FORMAT_NOT_SUPPORTED", "message": f"Video format not supported for ID: {video_id}"},
            )
        elif "Video unavailable" in error_msg:
            raise HTTPException(
                status_code=404,
                detail={"error": "VIDEO_UNAVAILABLE", "message": f"Video not found: {video_id}"},
            )
        else:
            raise HTTPException(
                status_code=500,
                detail={"error": "INTERNAL", "message": f"Failed to get video info: {error_msg[:200]}"},
            )

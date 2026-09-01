"""ライブストリーム配信（HLS最適化・TTL キャッシュ付き）。"""

from typing import Any, Dict

import yt_dlp
from fastapi import APIRouter, HTTPException, Request

from ..cache import TTLCache
from ..config import CACHE_LIVE_TTL, CACHE_MAX_SIZE
from ..security import _validate_video_id
from ..ytdlp_client import YTDLPError, _base_ydl_opts, _run_ytdlp
from .proxy import proxy_url

router = APIRouter()

_live_cache = TTLCache(max_size=CACHE_MAX_SIZE)
_live_audio_cache = TTLCache(max_size=CACHE_MAX_SIZE)


def _yt_live_url(video_id: str) -> str:
    youtube_url = f"https://www.youtube.com/watch?v={video_id}"
    stream_opts: Dict[str, Any] = {
        **_base_ydl_opts(),
        "format": "95/96/best[height<=720]/best",
        "youtube_include_dash_manifest": False,
        "hls_prefer_native": False,
    }
    with yt_dlp.YoutubeDL(stream_opts) as ydl:
        info = ydl.extract_info(youtube_url, download=False)
    stream_url = info.get("url")
    if not stream_url:
        raise ValueError("Stream URL not found")
    return stream_url


def _yt_live_audio_url(video_id: str) -> str:
    """YouTube が用意するライブの音声専用 HLS を解決する。"""
    youtube_url = f"https://www.youtube.com/watch?v={video_id}"
    stream_opts: Dict[str, Any] = {
        **_base_ydl_opts(),
        "format": "bestaudio",
        "youtube_include_dash_manifest": False,
        "hls_prefer_native": False,
    }
    with yt_dlp.YoutubeDL(stream_opts) as ydl:
        info = ydl.extract_info(youtube_url, download=False)
    stream_url = info.get("url")
    if not stream_url:
        raise ValueError("Audio stream URL not found")
    return stream_url


async def _stream_live(video_id: str, request: Request, resolver, cache, cache_prefix: str):
    _validate_video_id(video_id)
    cache_key = f"{cache_prefix}:{video_id}"
    cached = cache.get(cache_key)
    if cached is not None:
        return await proxy_url(cached, request)
    try:
        stream_url = await _run_ytdlp(resolver, video_id)
        cache.set(cache_key, stream_url, CACHE_LIVE_TTL)
        return await proxy_url(stream_url, request)

    except YTDLPError as e:
        raise HTTPException(status_code=e.status_code, detail={"error": e.kind, "message": str(e)})
    except HTTPException:
        raise
    except Exception as e:
        error_msg = str(e)
        if "unavailable" in error_msg.lower():
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "LIVE_UNAVAILABLE",
                    "message": "このライブ配信は利用できません。",
                },
            )
        else:
            raise HTTPException(
                status_code=500,
                detail={
                    "error": "STREAM_ERROR",
                    "message": f"配信エラー: {error_msg[:100]}",
                },
            )


@router.get("/live/{video_id}")
async def stream_live(video_id: str, request: Request):
    """最大 720p のライブ HLS を配信する。"""
    return await _stream_live(video_id, request, _yt_live_url, _live_cache, "live")


@router.get("/live-audio/{video_id}")
async def stream_live_audio(video_id: str, request: Request):
    """ライブの音声専用 HLS を配信する。"""
    return await _stream_live(video_id, request, _yt_live_audio_url, _live_audio_cache, "live-audio")

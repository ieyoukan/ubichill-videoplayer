"""ライブ配信の URL 解決（TTL キャッシュ付き）。配信は /resolve が作る HLS セッション経由。"""

from typing import Any, Dict

import yt_dlp
from fastapi import HTTPException

from ..cache import TTLCache
from ..config import CACHE_LIVE_TTL, CACHE_MAX_SIZE
from ..security import _validate_video_id
from ..ytdlp_client import YTDLPError, _base_ydl_opts, _run_ytdlp

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
        # HLS gateway に渡す URL は必ず HLS にする。
        # bestaudio だけでは DASH/HTTPS の音声ファイルも選ばれる。
        "format": "bestaudio[protocol=m3u8_native]/bestaudio[protocol=m3u8]",
        "youtube_include_dash_manifest": False,
        "hls_prefer_native": False,
    }
    with yt_dlp.YoutubeDL(stream_opts) as ydl:
        info = ydl.extract_info(youtube_url, download=False)
    stream_url = info.get("url")
    if not stream_url:
        raise ValueError("Audio stream URL not found")
    return stream_url


async def resolve_live_url(video_id: str, audio_only: bool = False) -> str:
    _validate_video_id(video_id)
    resolver = _yt_live_audio_url if audio_only else _yt_live_url
    cache = _live_audio_cache if audio_only else _live_cache
    cache_prefix = "live-audio" if audio_only else "live"
    cache_key = f"{cache_prefix}:{video_id}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    try:
        stream_url = await _run_ytdlp(resolver, video_id)
        cache.set(cache_key, stream_url, CACHE_LIVE_TTL)
        return stream_url

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

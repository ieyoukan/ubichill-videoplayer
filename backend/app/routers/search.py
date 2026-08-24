"""YouTube検索。"""

from typing import Any, Dict

import yt_dlp
from fastapi import APIRouter, HTTPException

from ..cache import TTLCache
from ..config import CACHE_MAX_SIZE, CACHE_SEARCH_TTL, logger
from ..ytdlp_client import YTDLPError, _base_ydl_opts, _run_ytdlp

router = APIRouter()

_search_cache = TTLCache(max_size=CACHE_MAX_SIZE)


def _yt_search(q: str, limit: int) -> list:
    # ライブ配信を除外するために多めに取得してフィルタリング
    fetch_limit = limit * 3
    search_opts: Dict[str, Any] = {
        **_base_ydl_opts(),
        "extract_flat": True,
        "playlist_items": f"1:{fetch_limit}",
    }
    with yt_dlp.YoutubeDL(search_opts) as ydl:
        search_results = ydl.extract_info(f"ytsearch{fetch_limit}:{q}", download=False)
    tracks = []
    if search_results is not None and "entries" in search_results:
        for entry in search_results["entries"]:
            if not entry:
                continue
            # ライブ配信・プレミア公開中の動画を除外
            if entry.get("is_live") or entry.get("live_status") in ("is_live", "is_upcoming"):
                continue
            vid_id = entry.get("id", "")
            # extract_flat では thumbnail が空の場合があるため ytimg で補完
            thumbnail = entry.get("thumbnail") or f"https://i.ytimg.com/vi/{vid_id}/mqdefault.jpg"
            tracks.append(
                {
                    "id": vid_id,
                    "title": entry.get("title", "Unknown"),
                    "thumbnail": thumbnail,
                    "duration": entry.get("duration", 0),
                    "author": entry.get("uploader", "Unknown"),
                }
            )
            if len(tracks) >= limit:
                break
    return tracks


@router.get("/search")
async def search_tracks(q: str, limit: int = 10):
    """YouTube検索（TTL キャッシュ付き）"""
    q = (q or "").strip()
    if not q:
        return []
    cache_key = f"search:{q.lower()}:{limit}"
    cached = _search_cache.get(cache_key)
    if cached is not None:
        return cached
    try:
        tracks = await _run_ytdlp(_yt_search, q, limit)
        _search_cache.set(cache_key, tracks, CACHE_SEARCH_TTL)
        return tracks
    except YTDLPError as e:
        raise HTTPException(status_code=e.status_code, detail={"error": e.kind, "message": str(e)})
    except Exception as e:
        logger.exception("Unexpected search error")
        raise HTTPException(status_code=500, detail={"error": "INTERNAL", "message": f"Search error: {str(e)[:200]}"})

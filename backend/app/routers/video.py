"""通常動画配信（短いセグメント化プロキシ方式）。

YouTube CDN の URL はサーバー側 IP で署名されるため、
ブラウザへ直接リダイレクトすると IP 不一致で拒否される。
サーバー側でプロキシして返し、Range ヘッダーでシーク（seek）も動作させる。
"""

import asyncio
import re
import time
from typing import Dict, Optional, TypedDict

import httpx
import yt_dlp
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..cache import TTLCache
from ..config import CACHE_MAX_SIZE, UPSTREAM_SOURCE_ADDRESS, logger
from ..security import _is_safe_proxy_url, _safe_get, _validate_video_id
from ..ytdlp_client import YTDLPError, _base_ydl_opts, _run_ytdlp

router = APIRouter()

# 解決済み googlevideo URL のキャッシュ（/video 用）。署名付き URL は数時間有効なので
# TTL は 1 時間。キャッシュミス時にのみ yt-dlp を回す。
_video_url_cache = TTLCache(max_size=CACHE_MAX_SIZE)
_VIDEO_URL_TTL = 60 * 60  # 1 hour

# 1 レスポンスの最大バイト数。これを超える range 要求はこのサイズに丸めて返し、
# ブラウザに続きを別リクエストで取りに来させる (= HTTP レベルの短いセグメント)。
# 1 本の長い 206 ストリームが転送途中で QUIC アイドルタイムアウトするのを防ぐ。
#
# 注意: MP4 の moov（メタデータ）は先頭にあり、4MB だと高解像度・長尺動画で
# moov サイズが超過して <video> が「Format error」になる。実測で 3.5MB 超の moov
# があったため、余裕を持って 16MB に引き上げた。
_SEGMENT_BYTES = 16 * 1024 * 1024  # 16MB

_VIDEO_REQUEST_HEADER_ALLOWLIST = {
    "accept",
    "accept-language",
    "origin",
    "referer",
    "sec-fetch-mode",
    "user-agent",
}


class VideoStreamInfo(TypedDict):
    url: str
    http_headers: Dict[str, str]
    chunk_size: int
    available_at: float


def _yt_video_url(video_id: str) -> VideoStreamInfo:
    youtube_url = f"https://www.youtube.com/watch?v={video_id}"
    stream_opts = {
        **_base_ydl_opts(),
        "format": "best[height<=720][ext=mp4]/best[height<=720]/best",
        "youtube_include_dash_manifest": False,
    }
    with yt_dlp.YoutubeDL(stream_opts) as ydl:
        info = ydl.extract_info(youtube_url, download=False)
    stream_url = info.get("url")
    if not stream_url:
        raise ValueError("Stream URL not found")
    raw_headers = info.get("http_headers") or {}
    http_headers = {
        str(key): str(value)
        for key, value in raw_headers.items()
        if value is not None
    }
    raw_chunk_size = (info.get("downloader_options") or {}).get("http_chunk_size")
    try:
        chunk_size = int(raw_chunk_size) if raw_chunk_size else _SEGMENT_BYTES
    except (TypeError, ValueError):
        chunk_size = _SEGMENT_BYTES
    return {
        "url": stream_url,
        "http_headers": http_headers,
        "chunk_size": max(1, min(chunk_size, _SEGMENT_BYTES)),
        "available_at": float(info.get("available_at") or 0),
    }


async def _resolve_video_url(video_id: str) -> VideoStreamInfo:
    """解決済み URL とその取得条件をキャッシュ付きで返す。

    yt-dlp 呼び出しはキャッシュミス時のみ発生（動画あたり最大 1 時間に 1 回）。
    そのため Semaphore を通過する影響は軽微。
    """
    cached = _video_url_cache.get(video_id)
    if cached is not None:
        return cached
    url = await _run_ytdlp(_yt_video_url, video_id)
    _video_url_cache.set(video_id, url, _VIDEO_URL_TTL)
    return url


def _capped_range(
    range_header: Optional[str],
    max_bytes: int = _SEGMENT_BYTES,
) -> str:
    """受信 Range を解析し、1 レスポンスを _SEGMENT_BYTES 以下に丸めた Range 文字列を返す。"""
    start = 0
    req_end: Optional[int] = None
    if range_header:
        m = re.match(r"bytes=(\d+)-(\d*)", range_header)
        if m:
            start = int(m.group(1))
            req_end = int(m.group(2)) if m.group(2) else None
    safe_max_bytes = max(1, min(max_bytes, _SEGMENT_BYTES))
    cap_end = start + safe_max_bytes - 1
    end = cap_end if req_end is None else min(req_end, cap_end)
    return f"bytes={start}-{end}"


def _video_request_headers(
    stream_info: VideoStreamInfo,
    range_header: Optional[str],
) -> Dict[str, str]:
    """yt-dlp が URL と共に返した安全な取得ヘッダーを Range 付きで返す。"""
    headers = {
        key: value
        for key, value in stream_info["http_headers"].items()
        if key.lower() in _VIDEO_REQUEST_HEADER_ALLOWLIST
    }
    lower_names = {key.lower() for key in headers}
    if "user-agent" not in lower_names:
        headers["User-Agent"] = "Mozilla/5.0"
    if "accept" not in lower_names:
        headers["Accept"] = "*/*"
    headers["Range"] = _capped_range(range_header, stream_info["chunk_size"])
    return headers


async def _wait_for_stream_availability(
    stream_info: VideoStreamInfo,
    video_id: str,
) -> None:
    """YouTube が指定した CDN 利用可能時刻まで非同期で待機する。"""
    wait_seconds = max(
        0.0,
        stream_info.get("available_at", 0) - int(time.time()),
    )
    if wait_seconds <= 0:
        return
    logger.info(
        "Waiting %.1fs before accessing video CDN (video_id=%s)",
        wait_seconds,
        video_id,
    )
    await asyncio.sleep(wait_seconds)


@router.get("/video/{video_id}")
async def stream_video(video_id: str, request: Request):
    """通常動画配信（短いセグメント化プロキシ方式）

    実装ポイント:
    - **1 レスポンスを _SEGMENT_BYTES に頭打ち**する。ブラウザは続きを別の range
      リクエストで取りに来るので、長い 206 ストリームが転送途中に QUIC アイドル
      タイムアウトで切れる問題を防ぐ (HLS の短いセグメントと同等の効果)。
    - range ごとに yt-dlp を回さないよう、解決済み URL を TTL キャッシュする。
    - 上流が 403/410 を返した場合は署名 URL の失効とみなし、キャッシュを破棄して
      yt-dlp で再解決し、そのリクエスト内で 1 回だけ再試行する。
    - `client.send(stream=True)` で body をメモリに溜めず逐次転送する。
    """
    _validate_video_id(video_id)
    try:
        stream_info = await _resolve_video_url(video_id)
        stream_url = stream_info["url"]
        # SSRF defense in depth: yt-dlp 出力も allowlist 検証する
        # (yt-dlp が予期せぬ URL を返すケースをカバー)
        if not _is_safe_proxy_url(stream_url):
            raise HTTPException(status_code=403, detail="Stream URL is not allowed")

        headers = _video_request_headers(stream_info, request.headers.get("range"))

        # follow_redirects=False + 自前ホップ検証 (SSRF 対策)
        # 各ホップを _safe_get 経由で allowlist 検証 → 302 で内部 IP に飛ばされない
        timeout = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)
        transport = httpx.AsyncHTTPTransport(local_address=UPSTREAM_SOURCE_ADDRESS)
        client = httpx.AsyncClient(
            follow_redirects=False,
            timeout=timeout,
            http2=False,
            transport=transport,
        )
        upstream = None
        try:
            # 403/410 のときだけ URL を再解決して 1 回だけ再試行する。
            # 最初の失敗レスポンスは再試行前に閉じ、接続をリークさせない。
            for attempt in range(2):
                await _wait_for_stream_availability(stream_info, video_id)
                upstream = await _safe_get(client, stream_url, headers, stream=True)
                if upstream.status_code not in (403, 410):
                    break

                _video_url_cache.delete(video_id)
                if attempt == 1:
                    break

                rejected_status = upstream.status_code
                await upstream.aclose()
                upstream = None
                logger.warning(
                    "Video upstream returned %d; refreshing signed URL and retrying once "
                    "(video_id=%s)",
                    rejected_status,
                    video_id,
                )
                stream_info = await _resolve_video_url(video_id)
                stream_url = stream_info["url"]
                if not _is_safe_proxy_url(stream_url):
                    raise HTTPException(status_code=403, detail="Stream URL is not allowed")
                headers = _video_request_headers(
                    stream_info,
                    request.headers.get("range"),
                )
        except Exception:
            if upstream is not None:
                await upstream.aclose()
            await client.aclose()
            raise

        # loop は必ずレスポンスを 1 つ設定して終了する。
        assert upstream is not None

        async def _iter():
            try:
                # 256KB ずつ転送: yield 回数を減らして throughput を稼ぐ
                async for chunk in upstream.aiter_bytes(262144):
                    yield chunk
            finally:
                await upstream.aclose()
                await client.aclose()

        res_headers: dict[str, str] = {"Accept-Ranges": "bytes"}
        for key in ("content-length", "content-range", "content-type"):
            if key in upstream.headers:
                res_headers[key] = upstream.headers[key]

        return StreamingResponse(
            _iter(),
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type", "video/mp4"),
            headers=res_headers,
        )

    except YTDLPError as e:
        # yt-dlp 呼び出し自体が失敗（タイムアウト / overloading / bot 判定）
        raise HTTPException(status_code=e.status_code, detail={"error": e.kind, "message": str(e)})
    except HTTPException:
        raise
    except Exception as e:
        error_msg = str(e)

        if "processing this video" in error_msg.lower():
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "VIDEO_PROCESSING",
                    "message": "この動画は現在処理中です。しばらくしてからもう一度お試しください。",
                },
            )
        elif "unavailable" in error_msg.lower():
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "VIDEO_UNAVAILABLE",
                    "message": "この動画は利用できません。削除されたか、非公開になっている可能性があります。",
                },
            )
        elif "private video" in error_msg.lower():
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "VIDEO_PRIVATE",
                    "message": "この動画は非公開に設定されています。",
                },
            )
        elif (
            "no video formats found" in error_msg.lower()
            or "format" in error_msg.lower()
        ):
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "FORMAT_NOT_SUPPORTED",
                    "message": "この動画のフォーマットはサポートされていません。",
                },
            )
        else:
            raise HTTPException(
                status_code=500,
                detail={
                    "error": "VIDEO_ERROR",
                    "message": f"動画エラー: {error_msg[:100]}",
                },
            )

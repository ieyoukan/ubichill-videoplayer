import asyncio
import ipaddress
import re
import os
import time
from typing import Dict, Any, Optional, Tuple
from urllib.parse import urljoin, quote, urlparse

from fastapi import FastAPI, HTTPException, Response, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, StreamingResponse
import yt_dlp
import httpx

# ROOT_PATH環境変数を取得（Kubernetes Ingressでのプレフィックス対応）
root_path = os.getenv("ROOT_PATH", "")

app = FastAPI(
    title="Ubichill Video Player API",
    version="1.0.0",
    root_path=root_path
)

# CORS設定（環境変数から取得、デフォルトは開発環境用）
allowed_origins = os.getenv(
    "CORS_ORIGINS", "http://localhost:3000,http://localhost:3001"
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in allowed_origins]
    if allowed_origins != ["*"]
    else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class YTDLPLogger:
    """yt-dlpのログを制御"""

    def debug(self, msg):
        pass

    def warning(self, msg):
        pass

    def error(self, msg):
        print(f"yt-dlp error: {msg}")


def _base_ydl_opts() -> Dict[str, Any]:
    """全 yt-dlp 呼び出し共通の基本設定。

    YouTube は近年データセンター IP を bot 判定して
    「Sign in to confirm you're not a bot」を返すことがある。確実な回避は
    cookies の提供なので、env で渡せるようにしている:

    - YTDLP_COOKIES_FILE        : Netscape 形式 cookies.txt のパス（マウント推奨）
    - YTDLP_COOKIES_FROM_BROWSER : 'chrome' 等（ブラウザがある環境向け。コンテナでは不可）
    - YTDLP_PLAYER_CLIENT        : 'ios,web' 等。extractor の player_client を上書き
                                   （cookies 無しで通る client を試したいとき）
    """
    opts: Dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "logger": YTDLPLogger(),
    }
    cookiefile = os.getenv("YTDLP_COOKIES_FILE")
    if cookiefile:
        opts["cookiefile"] = cookiefile
    from_browser = os.getenv("YTDLP_COOKIES_FROM_BROWSER")
    if from_browser:
        opts["cookiesfrombrowser"] = (from_browser,)
    player_client = os.getenv("YTDLP_PLAYER_CLIENT")
    if player_client:
        opts["extractor_args"] = {"youtube": {"player_client": player_client.split(",")}}
    return opts


@app.get("/")
async def health_check():
    return {"message": "Ubichill Music Streaming API", "status": "healthy"}


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


@app.get("/search")
async def search_tracks(q: str, limit: int = 10):
    """YouTube検索"""
    try:
        tracks = await asyncio.to_thread(_yt_search, q, limit)
        return tracks
    except Exception as e:
        print(f"Search error: {e}")
        return []


def _yt_info(video_id: str) -> dict:
    ydl_opts: Dict[str, Any] = {**_base_ydl_opts(), "extract_flat": False}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)


@app.get("/info/{video_id}")
async def get_video_info(video_id: str, request: Request):
    """動画情報を取得"""
    _validate_video_id(video_id)
    try:
        info = await asyncio.to_thread(_yt_info, video_id)
        base_url = f"{request.url.scheme}://{request.url.netloc}"
        return {
            "id": info.get("id"),
            "title": info.get("title", "Unknown"),
            "thumbnail": info.get("thumbnail") or f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg",
            "duration": info.get("duration", 0),
            "author": info.get("uploader", "Unknown"),
            "streamUrl": f"{base_url}/api/stream/video/{video_id}",
        }
    except Exception as e:
        error_msg = str(e)
        if "Requested format is not available" in error_msg:
            raise HTTPException(
                status_code=400, detail=f"Video format not supported for ID: {video_id}"
            )
        elif "Video unavailable" in error_msg:
            raise HTTPException(status_code=404, detail=f"Video not found: {video_id}")
        else:
            raise HTTPException(
                status_code=500, detail=f"Failed to get video info: {error_msg}"
            )


@app.get("/popular")
async def get_popular_tracks():
    """人気トラックを返す（サンプル）"""
    # 実際のプロダクションでは、データベースやキャッシュから取得
    popular_tracks = [
        {
            "id": "dQw4w9WgXcQ",
            "title": "Rick Astley - Never Gonna Give You Up",
            "thumbnail": "https://img.youtube.com/vi/dQw4w9WgXcQ/maxresdefault.jpg",
            "duration": 213,
            "author": "Rick Astley",
        },
        {
            "id": "9bZkp7q19f0",
            "title": "PSY - GANGNAM STYLE",
            "thumbnail": "https://img.youtube.com/vi/9bZkp7q19f0/maxresdefault.jpg",
            "duration": 252,
            "author": "officialpsy",
        },
    ]
    return popular_tracks


# ヘルパー関数
def _rewrite_manifest_urls(content: str, base_url: str) -> str:
    """HLSマニフェスト内のURLをプロキシURL に書き換え"""
    
    proxy_path = f"{root_path}/proxy" if root_path else "/proxy"

    def replace_url(match):
        original_url = match.group(1)
        if original_url.startswith(proxy_path):
            return original_url
        full_url = (
            original_url
            if original_url.startswith("http")
            else urljoin(base_url, original_url)
        )
        return f"{proxy_path}?url={quote(full_url, safe='')}"

    content = re.sub(r'(https?://[^\s"]+\.(?:m3u8|ts))', replace_url, content)
    content = re.sub(
        r"^(?!#)([^\s]+\.(?:m3u8|ts))$", replace_url, content, flags=re.MULTILINE
    )
    return content


def _get_content_type_for_ts(content: bytes, original_type: str) -> str:
    """TSセグメントのContent-Typeを検出"""
    if len(content) > 0 and content[0] == 0x47:
        return "video/MP2T"  # MPEG-TS magic byte detected
    return original_type


@app.get("/thumbnail/{video_id}")
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


# SSRF対策: プロキシで許可するホストのパターン
_ALLOWED_PROXY_HOSTS = re.compile(
    r"^([\w-]+\.)*googlevideo\.com$"
    r"|^([\w-]+\.)*youtube\.com$"
    r"|^([\w-]+\.)*ytimg\.com$"
    r"|^([\w-]+\.)*ggpht\.com$"
)


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


# video_id バリデーション用正規表現（YouTubeのID形式: 英数字・ハイフン・アンダースコア 11文字）
_VIDEO_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{6,20}$")


def _validate_video_id(video_id: str) -> None:
    """video_idが妥当な形式かどうか検証"""
    if not _VIDEO_ID_PATTERN.match(video_id):
        raise HTTPException(status_code=400, detail="Invalid video ID format")


@app.get("/proxy")
async def proxy_url(url: str, request: Request):
    """許可されたホストのURLのみをプロキシ（HLSセグメント用・SSRF対策済み）"""
    if not _is_safe_proxy_url(url):
        raise HTTPException(status_code=403, detail="Proxy target URL is not allowed")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "*/*",
        "Referer": "https://www.youtube.com/",
        "Origin": "https://www.youtube.com",
    }

    try:
        # follow_redirects=False で自前のホップ検証を行う (リダイレクト先 SSRF 対策)
        async with httpx.AsyncClient(follow_redirects=False, timeout=30.0) as client:
            response = await _safe_get(client, url, headers)
            response.raise_for_status()

            content_type = response.headers.get(
                "content-type", "application/octet-stream"
            )
            is_manifest = url.endswith(".m3u8") or "mpegurl" in content_type

            if is_manifest:
                # マニフェストの場合はURLを書き換え
                base_url = url.rsplit("/", 1)[0] + "/"
                content = _rewrite_manifest_urls(response.text, base_url)
                return Response(
                    content=content,
                    media_type="application/vnd.apple.mpegurl",
                    headers={
                        "Cache-Control": "no-cache",
                    },
                )
            else:
                # TSセグメントの場合はContent-Typeを検出
                is_ts = ".ts" in url or "seg.ts" in url
                final_type = (
                    _get_content_type_for_ts(response.content, content_type)
                    if is_ts
                    else content_type
                )
                return Response(
                    content=response.content,
                    media_type=final_type,
                    headers={
                        "Cache-Control": "public, max-age=3600",
                    },
                )

    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Request timeout")
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail="HTTP error")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Proxy error: {str(e)}")


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


@app.get("/live/{video_id}")
async def stream_live(video_id: str, request: Request):
    """ライブストリーム配信（HLS最適化）"""
    _validate_video_id(video_id)
    try:
        stream_url = await asyncio.to_thread(_yt_live_url, video_id)
        return await proxy_url(stream_url, request)

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


def _yt_video_url(video_id: str) -> str:
    youtube_url = f"https://www.youtube.com/watch?v={video_id}"
    stream_opts: Dict[str, Any] = {
        **_base_ydl_opts(),
        "format": "best[height<=720][ext=mp4]/best[height<=720]/best",
        "youtube_include_dash_manifest": False,
    }
    with yt_dlp.YoutubeDL(stream_opts) as ydl:
        info = ydl.extract_info(youtube_url, download=False)
    stream_url = info.get("url")
    if not stream_url:
        raise ValueError("Stream URL not found")
    return stream_url


# 解決済み googlevideo URL の TTL キャッシュ。
# /video は 1 レスポンスを小さく頭打ちする (短いセグメント化) ため、1 本の動画で
# range リクエストが何度も来る。毎回 yt-dlp を回すと遅く YouTube にも叩かれるので、
# 署名付き URL を再利用する。googlevideo URL は数時間有効なので TTL は短めの 1 時間。
_video_url_cache: Dict[str, Tuple[str, float]] = {}
_VIDEO_URL_TTL = 60 * 60  # 1 hour
# 1 レスポンスの最大バイト数。これを超える range 要求はこのサイズに丸めて返し、
# ブラウザに続きを別リクエストで取りに来させる (= HTTP レベルの短いセグメント)。
# 1 本の長い 206 ストリームが転送途中で QUIC アイドルタイムアウトするのを防ぐ。
_SEGMENT_BYTES = 4 * 1024 * 1024  # 4MB


async def _resolve_video_url(video_id: str) -> str:
    """解決済み URL をキャッシュ付きで返す。"""
    now = time.time()
    cached = _video_url_cache.get(video_id)
    if cached and cached[1] > now:
        return cached[0]
    url = await asyncio.to_thread(_yt_video_url, video_id)
    # 肥大防止: 期限切れを掃除
    if len(_video_url_cache) > 256:
        for k in [k for k, (_, exp) in _video_url_cache.items() if exp <= now]:
            _video_url_cache.pop(k, None)
    _video_url_cache[video_id] = (url, now + _VIDEO_URL_TTL)
    return url


def _capped_range(range_header: Optional[str]) -> str:
    """受信 Range を解析し、1 レスポンスを _SEGMENT_BYTES 以下に丸めた Range 文字列を返す。"""
    start = 0
    req_end: Optional[int] = None
    if range_header:
        m = re.match(r"bytes=(\d+)-(\d*)", range_header)
        if m:
            start = int(m.group(1))
            req_end = int(m.group(2)) if m.group(2) else None
    cap_end = start + _SEGMENT_BYTES - 1
    end = cap_end if req_end is None else min(req_end, cap_end)
    return f"bytes={start}-{end}"


@app.get("/video/{video_id}")
async def stream_video(video_id: str, request: Request):
    """通常動画配信（短いセグメント化プロキシ方式）

    YouTube CDN の URL はサーバー側 IP で署名されるため、
    ブラウザへ直接リダイレクトすると IP 不一致で拒否される。
    サーバー側でプロキシして返し、Range ヘッダーでシーク（seek）も動作させる。

    実装ポイント:
    - **1 レスポンスを _SEGMENT_BYTES に頭打ち**する。ブラウザは続きを別の range
      リクエストで取りに来るので、長い 206 ストリームが転送途中に QUIC アイドル
      タイムアウトで切れる問題を防ぐ (HLS の短いセグメントと同等の効果)。
    - range ごとに yt-dlp を回さないよう、解決済み URL を TTL キャッシュする。
    - `client.send(stream=True)` で body をメモリに溜めず逐次転送する。
    """
    _validate_video_id(video_id)
    try:
        stream_url = await _resolve_video_url(video_id)
        # SSRF defense in depth: yt-dlp 出力も allowlist 検証する
        # (yt-dlp が予期せぬ URL を返すケースをカバー)
        if not _is_safe_proxy_url(stream_url):
            raise HTTPException(status_code=403, detail="Stream URL is not allowed")

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "*/*",
            "Referer": "https://www.youtube.com/",
            "Origin": "https://www.youtube.com",
            # 常に範囲指定して 1 レスポンスを頭打ちする (短いセグメント化)
            "Range": _capped_range(request.headers.get("range")),
        }

        # follow_redirects=False + 自前ホップ検証 (SSRF 対策)
        # 各ホップを _safe_get 経由で allowlist 検証 → 302 で内部 IP に飛ばされない
        timeout = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)
        client = httpx.AsyncClient(follow_redirects=False, timeout=timeout, http2=False)
        upstream = await _safe_get(client, stream_url, headers, stream=True)

        # 署名切れ等で弾かれたらキャッシュを捨てて、次リクエストで再解決させる
        if upstream.status_code in (403, 410):
            _video_url_cache.pop(video_id, None)

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


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

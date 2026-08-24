import asyncio
import ipaddress
import logging
import re
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Dict, Any, Optional, Tuple, TypedDict
from urllib.parse import urljoin, quote, urlparse

from fastapi import FastAPI, HTTPException, Response, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, StreamingResponse
import yt_dlp
import httpx

# ── ロギング設定 ───────────────────────────────────
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("yt-resolver")
# Google Video の署名・PO Token・出口IPを含む完全URLを INFO ログへ残さない。
logging.getLogger("httpx").setLevel(
    os.getenv("HTTPX_LOG_LEVEL", "WARNING").upper()
)

# yt-dlp による URL 解決と httpx による動画取得を同じ IP family に固定する。
# 0.0.0.0 は OS が選ぶ IPv4 アドレスを使用する指定。
_UPSTREAM_SOURCE_ADDRESS = os.getenv("UPSTREAM_SOURCE_ADDRESS", "0.0.0.0")


class TTLCache:
    """シンプルなインメモリ TTL キャッシュ（スレッドセーフ）。

    検索結果 / 動画情報 / 解決済みストリーム URL をキャッシュして、
    yt-dlp への呼び出し回数を削減する。Redis 導入前の第一段階として
    プロセス内 dict で実装。複数レプリカで共有する場合は Redis に差し替える。
    """

    def __init__(self, max_size: int = 512):
        self._store: Dict[str, Tuple[Any, float]] = {}
        self._max_size = max_size
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[Any]:
        now = time.monotonic()
        with self._lock:
            item = self._store.get(key)
            if item is None:
                return None
            value, expires = item
            if expires <= now:
                del self._store[key]
                return None
            return value

    def set(self, key: str, value: Any, ttl: float) -> None:
        now = time.monotonic()
        with self._lock:
            # 肥大防止: 上限を超えたら期限切れを掃除し、それでも満杯なら最古を追い出す（FIFO）
            if len(self._store) >= self._max_size:
                for k in [k for k, (_, exp) in self._store.items() if exp <= now]:
                    del self._store[k]
                if len(self._store) >= self._max_size:
                    oldest = next(iter(self._store))
                    del self._store[oldest]
            self._store[key] = (value, now + ttl)

    def delete(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

# ROOT_PATH環境変数を取得（Kubernetes Ingressでのプレフィックス対応）
root_path = os.getenv("ROOT_PATH", "")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """アプリケーションライフサイクル。シャットダウン時にスレッドプールを安全に終了する。"""
    yield
    logger.info("Shutting down yt-dlp thread pool...")
    _ytdlp_executor.shutdown(wait=True, cancel_futures=True)
    logger.info("yt-dlp thread pool shut down")


app = FastAPI(
    title="Ubichill Video Player API",
    version="1.0.0",
    root_path=root_path,
    lifespan=lifespan,
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
        logger.warning("yt-dlp: %s", msg)

    def error(self, msg):
        logger.error("yt-dlp: %s", msg)


def _base_ydl_opts() -> Dict[str, Any]:
    """全 yt-dlp 呼び出し共通の基本設定。

    YouTube は近年データセンター IP を bot 判定して
    「Sign in to confirm you're not a bot」を返すことがある。確実な回避は
    cookies の提供なので、env で渡せるようにしている:

    - YTDLP_COOKIES_FILE        : Netscape 形式 cookies.txt のパス（マウント推奨）
    - YTDLP_COOKIES_FROM_BROWSER : 'chrome' 等（ブラウザがある環境向け。コンテナでは不可）
    - YTDLP_PLAYER_CLIENT        : 'ios,web' 等。extractor の player_client を上書き
                                   （cookies 無しで通る client を試したいとき）
    - YTDLP_POT_PROVIDER_URL     : BgUtils PO Token Provider の HTTP URL
    - UPSTREAM_SOURCE_ADDRESS    : yt-dlp / httpx 共通の送信元。既定は 0.0.0.0 (IPv4)
    """
    socket_timeout = int(os.getenv("YTDLP_SOCKET_TIMEOUT", "30"))
    opts: Dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "logger": YTDLPLogger(),
        "socket_timeout": socket_timeout,
        "source_address": _UPSTREAM_SOURCE_ADDRESS,
    }
    cookiefile = os.getenv("YTDLP_COOKIES_FILE")
    if cookiefile:
        opts["cookiefile"] = cookiefile
    from_browser = os.getenv("YTDLP_COOKIES_FROM_BROWSER")
    if from_browser:
        opts["cookiesfrombrowser"] = (from_browser,)
    extractor_args: Dict[str, Dict[str, list[str]]] = {}
    player_client = os.getenv("YTDLP_PLAYER_CLIENT")
    if player_client:
        extractor_args["youtube"] = {"player_client": player_client.split(",")}
    pot_provider_url = os.getenv("YTDLP_POT_PROVIDER_URL")
    if pot_provider_url:
        extractor_args["youtubepot-bgutilhttp"] = {"base_url": [pot_provider_url]}
    if extractor_args:
        opts["extractor_args"] = extractor_args
    return opts


# ── yt-dlp 同時実行制御 ─────────────────────────
# Cloudflare 502 の主因: すべての uvicorn worker が yt-dlp の重い呼び出しで
# ブロックされ、リクエストを受け付けられなくなる。Semaphore で同時実行数を
# 制限し、枠を超えたリクエストは速やかに 503 を返して Cloudflare のタイム
# アウトを回避する。
_YTDLP_MAX_CONCURRENT = int(os.getenv("YTDLP_MAX_CONCURRENT", "3"))
_ytdlp_semaphore = asyncio.Semaphore(_YTDLP_MAX_CONCURRENT)
# yt-dlp 専用スレッドプール。asyncio.to_thread はデフォルトプールを使うため
# 他のブロッキング処理と競合する。分離することで yt-dlp が詰まっても他の
# エンドポイント（サムネイル/proxy）は影響を受けない。
_ytdlp_executor = ThreadPoolExecutor(
    max_workers=_YTDLP_MAX_CONCURRENT,
    thread_name_prefix="ytdlp",
)
# yt-dlp 全体のタイムアウト（秒）。socket_timeout とは別に、
# extract_info 全体がこの時間を超えたら強制中断する。
_YTDLP_TASK_TIMEOUT = int(os.getenv("YTDLP_TASK_TIMEOUT", "60"))


class YTDLPError(Exception):
    """yt-dlp 呼び出し失敗（タイムアウト / bot 判定 / ネットワークエラー等）。"""

    def __init__(self, message: str, status_code: int = 502, kind: str = "YTDLP_ERROR"):
        super().__init__(message)
        self.status_code = status_code
        self.kind = kind


async def _run_ytdlp(func, *args, **kwargs):
    """yt-dlp の重い同期呼び出しを Semaphore + ThreadPool + Timeout で安全に実行する。

    3 段階の防御:
    1. Semaphore — 同時実行数超過ならここで待機（先行リクエストが終わるのを待つだけ）
    2. asyncio.wait_for — 全体タイムアウトで永久ブロック防止
    3. socket_timeout — yt-dlp 内の個別ネットワーク操作にもタイムアウト

    失敗時は YTDLPError（Cloudflare に適切なエラーコードを返すため HTTPException に変換される）。
    """
    sem_acquired = False
    try:
        # Semaphore の獲得にもタイムアウトを設定（詰まってるなら素早く 503 を返す）
        sem_acquired = await asyncio.wait_for(
            _ytdlp_semaphore.acquire(),
            timeout=float(os.getenv("YTDLP_SEMAPHORE_TIMEOUT", "15")),
        )
    except asyncio.TimeoutError:
        raise YTDLPError(
            "Server is overloaded, please try again later",
            status_code=503,
            kind="OVERLOADED",
        )

    loop = asyncio.get_running_loop()
    task_start = time.monotonic()
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(_ytdlp_executor, func, *args, **kwargs),
            timeout=_YTDLP_TASK_TIMEOUT,
        )
        elapsed = time.monotonic() - task_start
        logger.info("yt-dlp call completed in %.1fs (func=%s)", elapsed, getattr(func, "__name__", func))
        return result
    except asyncio.TimeoutError:
        elapsed = time.monotonic() - task_start
        logger.error("yt-dlp call timed out after %.1fs (func=%s)", elapsed, getattr(func, "__name__", func))
        raise YTDLPError(
            "YouTube request timed out, please try again",
            status_code=504,
            kind="TIMEOUT",
        )
    except Exception as e:
        elapsed = time.monotonic() - task_start
        msg = str(e)
        logger.error("yt-dlp call failed after %.1fs: %s (func=%s)", elapsed, msg[:200], getattr(func, "__name__", func))
        # bot 判定 / IP ブロックの典型的メッセージを判別
        if "sign in" in msg.lower() or "bot" in msg.lower():
            raise YTDLPError(
                "YouTube is blocking this server (bot detection). Try setting YTDLP_COOKIES_FILE.",
                status_code=502,
                kind="BOT_DETECTED",
            )
        raise YTDLPError(msg, status_code=502, kind="YTDLP_ERROR")
    finally:
        if sem_acquired:
            _ytdlp_semaphore.release()


# ── キャッシュ設定 ───────────────────────────────
# yt-dlp 呼び出しは高コスト（ネットワーク + リソース）なので、結果を TTL 付きで
# インメモリにキャッシュする。複数レプリカで共有する場合は Redis に差し替える。
_CACHE_MAX_SIZE = int(os.getenv("CACHE_MAX_SIZE", "512"))
_CACHE_SEARCH_TTL = int(os.getenv("CACHE_SEARCH_TTL", "300"))  # 5分
_CACHE_INFO_TTL = int(os.getenv("CACHE_INFO_TTL", "3600"))    # 1時間
_CACHE_LIVE_TTL = int(os.getenv("CACHE_LIVE_TTL", "30"))      # 30秒

_search_cache = TTLCache(max_size=_CACHE_MAX_SIZE)
_info_cache = TTLCache(max_size=_CACHE_MAX_SIZE)
_live_cache = TTLCache(max_size=_CACHE_MAX_SIZE)
# 解決済み googlevideo URL のキャッシュ（/video 用）。署名付き URL は数時間有効なので
# TTL は 1 時間。キャッシュミス時にのみ yt-dlp を回す。
_video_url_cache = TTLCache(max_size=_CACHE_MAX_SIZE)
_VIDEO_URL_TTL = 60 * 60  # 1 hour


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
        _search_cache.set(cache_key, tracks, _CACHE_SEARCH_TTL)
        return tracks
    except YTDLPError as e:
        raise HTTPException(status_code=e.status_code, detail={"error": e.kind, "message": str(e)})
    except Exception as e:
        logger.exception("Unexpected search error")
        raise HTTPException(status_code=500, detail={"error": "INTERNAL", "message": f"Search error: {str(e)[:200]}"})


def _yt_info(video_id: str) -> dict:
    ydl_opts: Dict[str, Any] = {**_base_ydl_opts(), "extract_flat": False}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)


@app.get("/info/{video_id}")
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
        _info_cache.set(cache_key, result, _CACHE_INFO_TTL)
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
    """ライブストリーム配信（HLS最適化・TTL キャッシュ付き）"""
    _validate_video_id(video_id)
    cache_key = f"live:{video_id}"
    cached = _live_cache.get(cache_key)
    if cached is not None:
        return await proxy_url(cached, request)
    try:
        stream_url = await _run_ytdlp(_yt_live_url, video_id)
        _live_cache.set(cache_key, stream_url, _CACHE_LIVE_TTL)
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


class VideoStreamInfo(TypedDict):
    url: str
    http_headers: Dict[str, str]
    chunk_size: int
    available_at: float


def _yt_video_url(video_id: str) -> VideoStreamInfo:
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


# 1 レスポンスの最大バイト数。これを超える range 要求はこのサイズに丸めて返し、
# ブラウザに続きを別リクエストで取りに来させる (= HTTP レベルの短いセグメント)。
# 1 本の長い 206 ストリームが転送途中で QUIC アイドルタイムアウトするのを防ぐ。
#
# 注意: MP4 の moov（メタデータ）は先頭にあり、4MB だと高解像度・長尺動画で
# moov サイズが超過して <video> が「Format error」になる。実測で 3.5MB 超の moov
# があったため、余裕を持って 16MB に引き上げた。
_SEGMENT_BYTES = 16 * 1024 * 1024  # 16MB


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


_VIDEO_REQUEST_HEADER_ALLOWLIST = {
    "accept",
    "accept-language",
    "origin",
    "referer",
    "sec-fetch-mode",
    "user-agent",
}


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
        transport = httpx.AsyncHTTPTransport(local_address=_UPSTREAM_SOURCE_ADDRESS)
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


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

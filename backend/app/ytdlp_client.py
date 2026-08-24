"""yt-dlp 呼び出しの共通設定・同時実行制御・エラーハンドリング。"""

import asyncio
import os
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict

from .config import UPSTREAM_SOURCE_ADDRESS, logger


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
        "source_address": UPSTREAM_SOURCE_ADDRESS,
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
YTDLP_MAX_CONCURRENT = int(os.getenv("YTDLP_MAX_CONCURRENT", "3"))
_ytdlp_semaphore = asyncio.Semaphore(YTDLP_MAX_CONCURRENT)
# yt-dlp 専用スレッドプール。asyncio.to_thread はデフォルトプールを使うため
# 他のブロッキング処理と競合する。分離することで yt-dlp が詰まっても他の
# エンドポイント（サムネイル/proxy）は影響を受けない。
ytdlp_executor = ThreadPoolExecutor(
    max_workers=YTDLP_MAX_CONCURRENT,
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
            loop.run_in_executor(ytdlp_executor, func, *args, **kwargs),
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

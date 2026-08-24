"""ロギングと環境変数ベースの設定値。"""

import logging
import os

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
UPSTREAM_SOURCE_ADDRESS = os.getenv("UPSTREAM_SOURCE_ADDRESS", "0.0.0.0")

# Kubernetes Ingress でのプレフィックス対応
ROOT_PATH = os.getenv("ROOT_PATH", "")

ALLOWED_ORIGINS = os.getenv(
    "CORS_ORIGINS", "http://localhost:3000,http://localhost:3001"
).split(",")

# ── キャッシュ設定 ───────────────────────────────
# yt-dlp 呼び出しは高コスト（ネットワーク + リソース）なので、結果を TTL 付きで
# インメモリにキャッシュする。複数レプリカで共有する場合は Redis に差し替える。
CACHE_MAX_SIZE = int(os.getenv("CACHE_MAX_SIZE", "512"))
CACHE_SEARCH_TTL = int(os.getenv("CACHE_SEARCH_TTL", "300"))  # 5分
CACHE_INFO_TTL = int(os.getenv("CACHE_INFO_TTL", "3600"))  # 1時間
CACHE_LIVE_TTL = int(os.getenv("CACHE_LIVE_TTL", "30"))  # 30秒

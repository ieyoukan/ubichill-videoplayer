"""ロギングと環境変数ベースの設定値。"""

import logging
import os
import secrets

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

# ── サービストークン（Ubichill からの依頼であることの確認） ──────────
# 信頼する Ubichill の発行元（公開オリジン、カンマ区切り）。トークンの iss がこの中に無ければ拒否する。
UBICHILL_ISSUERS = [
    issuer.strip().rstrip("/")
    for issuer in os.getenv("UBICHILL_ISSUERS", "").split(",")
    if issuer.strip()
]
# このサービス自身のオリジン（トークンの aud と一致しなければ拒否する）。
SERVICE_AUDIENCE = os.getenv("SERVICE_AUDIENCE", "").strip().rstrip("/")
# 受け付ける mod の ID（カンマ区切り）。
SERVICE_ALLOWED_MODS = [
    mod.strip()
    for mod in os.getenv("SERVICE_ALLOWED_MODS", "video-player").split(",")
    if mod.strip()
]
# false にすると誰でも使える（ローカル開発用）。本番では true のまま。
REQUIRE_SERVICE_TOKEN = os.getenv("REQUIRE_SERVICE_TOKEN", "true").lower() == "true"

# ── 利用者（トークンの sub）ごとの回数制限 ─────────────────────
# 「回数/秒数」。YouTube への取得を伴うものほど厳しくする。
RATE_LIMIT_RESOLVE = os.getenv("RATE_LIMIT_RESOLVE", "30/600")
RATE_LIMIT_SEARCH = os.getenv("RATE_LIMIT_SEARCH", "20/60")
RATE_LIMIT_INFO = os.getenv("RATE_LIMIT_INFO", "60/60")

# ── 動画 URL の署名 ───────────────────────────────────
# /resolve が発行する再生 URL の HMAC 鍵。未設定なら起動ごとに作る（再起動で再生中の URL は切れる）。
MEDIA_URL_SECRET = os.getenv("MEDIA_URL_SECRET") or secrets.token_urlsafe(32)
# 再生 URL の有効期間（秒）。長い動画を最後まで再生できる長さにする。
MEDIA_URL_TTL = int(os.getenv("MEDIA_URL_TTL", "21600"))

if REQUIRE_SERVICE_TOKEN and (not UBICHILL_ISSUERS or not SERVICE_AUDIENCE):
    raise RuntimeError(
        "REQUIRE_SERVICE_TOKEN=true のときは UBICHILL_ISSUERS と SERVICE_AUDIENCE を設定してください"
        "（ローカル開発で認証を外すなら REQUIRE_SERVICE_TOKEN=false）"
    )

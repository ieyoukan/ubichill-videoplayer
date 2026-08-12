FROM python:3.12-slim-bookworm AS base

RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# ── Builder ──────────────────────────────────────────────────
FROM base AS builder
WORKDIR /app

COPY pyproject.toml ./
RUN uv venv .venv
ENV PATH="/app/.venv/bin:$PATH"
RUN uv pip install -r pyproject.toml

# yt-dlp は YouTube 仕様変更で頻繁に壊れるため、毎ビルドで最新へ更新する。
# YTDLP_REFRESH に毎回変わる値 (CI では commit sha) を渡すとこのレイヤのキャッシュが
# 無効化され、確実に最新の yt-dlp を取り込む。他の依存はキャッシュを効かせたまま。
ARG YTDLP_REFRESH=dev
RUN uv pip install --upgrade yt-dlp

# ── Production ───────────────────────────────────────────────
FROM base AS production
WORKDIR /app

RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

COPY --from=builder --chown=appuser:appuser /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

COPY --chown=appuser:appuser main.py ./

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/ || exit 1

EXPOSE 8000

# シングルワーカーで起動する。
#
# このアプリは I/O バウンド（yt-dlp のネットワーク待ち + httpx プロキシ）なので、
# マルチプロセスより非同期イベントループ + ThreadPoolExecutor の方が効率的。
# さらに main.py の yt-dlp 同時実行 Semaphore はプロセス単位でしか効かないため、
# --workers を増やすと実効同時実行数が「ワーカー数 × YTDLP_MAX_CONCURRENT」に膨らみ、
# yt-dlp のメモリ消費 (1 本 512MB〜2GB) で OOM を招く。
# 同時実行制御を正しく効かせるには --workers 1 が前提。
# スケールする場合はプロセス数ではなくレプリカ数（docker-compose の replicas）で増やす。
CMD ["uvicorn", "main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "1", \
     "--proxy-headers", \
     "--forwarded-allow-ips", "*"]

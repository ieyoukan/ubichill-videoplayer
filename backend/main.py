"""FastAPI アプリのエントリポイント。ルーティングは app/routers 以下に分割されている。"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import ALLOWED_ORIGINS, ROOT_PATH, logger
from app.routers import info, live, meta, proxy, search, thumbnail, video
from app.ytdlp_client import ytdlp_executor


@asynccontextmanager
async def lifespan(app: FastAPI):
    """アプリケーションライフサイクル。シャットダウン時にスレッドプールを安全に終了する。"""
    yield
    logger.info("Shutting down yt-dlp thread pool...")
    ytdlp_executor.shutdown(wait=True, cancel_futures=True)
    logger.info("yt-dlp thread pool shut down")


app = FastAPI(
    title="Ubichill Video Player API",
    version="1.0.0",
    root_path=ROOT_PATH,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in ALLOWED_ORIGINS]
    if ALLOWED_ORIGINS != ["*"]
    else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for router_module in (meta, search, info, thumbnail, proxy, live, video):
    app.include_router(router_module.router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

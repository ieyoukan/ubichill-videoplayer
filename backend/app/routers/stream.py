"""Playback descriptor and opaque HLS gateway routes."""

import re
from collections.abc import AsyncIterator
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
import yt_dlp
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from ..config import UPSTREAM_SOURCE_ADDRESS
from ..hls_gateway import StreamSession, create_stream, get_stream
from ..manifest import _rewrite_manifest_urls
from ..security import _is_safe_proxy_url, _safe_get, _validate_video_id
from ..ytdlp_client import YTDLPError, _base_ydl_opts, _run_ytdlp
from .live import resolve_live_url

router = APIRouter()
_OPAQUE_ID = re.compile(r"^[A-Za-z0-9_-]{20,64}$")
_OPAQUE_RESOURCE_NAME = re.compile(r"^(?:playlist\.m3u8|segment\.(?:aac|m4a|m4s|mp4|ts|vtt)|key\.bin)$")


def _yt_vod_hls_sources(video_id: str, audio_only: bool) -> dict[str, Any]:
    selector = (
        "bestaudio[protocol=m3u8_native]/bestaudio[protocol=m3u8]"
        if audio_only
        else "bestvideo[height<=720][protocol=m3u8_native]+bestaudio[protocol=m3u8_native]/"
        "bestvideo[height<=720][protocol=m3u8]+bestaudio[protocol=m3u8]"
    )
    with yt_dlp.YoutubeDL({**_base_ydl_opts(), "format": selector}) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
    requested = info.get("requested_formats") or []
    video_url = next((item.get("url") for item in requested if item.get("vcodec") != "none"), None)
    audio_url = next((item.get("url") for item in requested if item.get("acodec") != "none"), None)
    if audio_only:
        audio_url = info.get("url") or audio_url
    if not audio_url or (not audio_only and not video_url):
        raise ValueError("HLS playback formats are not available")
    headers: dict[str, str] = {}
    safe_header_names = {"user-agent": "User-Agent", "referer": "Referer", "origin": "Origin"}
    for item in [info, *requested]:
        for name, value in (item.get("http_headers") or {}).items():
            canonical_name = safe_header_names.get(str(name).lower())
            if canonical_name and isinstance(value, str):
                headers[canonical_name] = value
    return {"video_url": video_url, "audio_url": audio_url, "headers": headers}


async def resolve_vod_hls_sources(video_id: str, audio_only: bool) -> dict[str, Any]:
    try:
        return await _run_ytdlp(_yt_vod_hls_sources, video_id, audio_only)
    except YTDLPError:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Unable to resolve HLS playback") from exc


def _vod_master_manifest(video_url: str | None, audio_url: str) -> str:
    if video_url is None:
        return "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=256000\n" f"{audio_url}\n"
    return (
        "#EXTM3U\n"
        "#EXT-X-VERSION:6\n"
        f'#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio",NAME="Default",DEFAULT=YES,AUTOSELECT=YES,URI="{audio_url}"\n'
        '#EXT-X-STREAM-INF:BANDWIDTH=4000000,AUDIO="audio"\n'
        f"{video_url}\n"
    )


def _upstream_headers(session: StreamSession, request: Request) -> dict[str, str]:
    headers = {
        "User-Agent": session.headers.get("User-Agent", "Mozilla/5.0"),
        "Accept": "*/*",
        "Referer": session.headers.get("Referer", "https://www.youtube.com/"),
        "Origin": session.headers.get("Origin", "https://www.youtube.com"),
    }
    range_header = request.headers.get("range")
    if range_header and re.fullmatch(r"bytes=\d*-\d*", range_header):
        headers["Range"] = range_header
    return headers


async def _fetch_resource(stream_id: str, url: str, request: Request):
    if not _is_safe_proxy_url(url):
        raise HTTPException(status_code=403, detail="Stream resource is not allowed")
    session = get_stream(stream_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Stream expired")
    transport = httpx.AsyncHTTPTransport(local_address=UPSTREAM_SOURCE_ADDRESS)
    client = httpx.AsyncClient(transport=transport, follow_redirects=False, timeout=30.0)
    try:
        response = await _safe_get(client, url, _upstream_headers(session, request), stream=True)
        response.raise_for_status()
        return session, client, response
    except httpx.TimeoutException as exc:
        await client.aclose()
        raise HTTPException(status_code=504, detail="Upstream timeout") from exc
    except httpx.HTTPStatusError as exc:
        await client.aclose()
        raise HTTPException(status_code=exc.response.status_code, detail="Upstream HTTP error") from exc
    except HTTPException:
        await client.aclose()
        raise
    except Exception as exc:
        await client.aclose()
        raise HTTPException(status_code=502, detail="Upstream stream error") from exc


def _opaque_resource_name(url: str) -> str:
    path = urlparse(url).path.lower()
    if path.endswith(".m3u8") or "hls_playlist" in path or "/manifest/" in path:
        return "playlist.m3u8"
    suffix = path.rsplit(".", 1)[-1] if "." in path.rsplit("/", 1)[-1] else ""
    if suffix in {"aac", "m4a", "m4s", "mp4", "ts", "vtt"}:
        return f"segment.{suffix}"
    if suffix in {"key", "bin"}:
        return "key.bin"
    return "segment.m4s"


def _gateway_url(request: Request, stream_id: str, token: str, resource_url: str) -> str:
    return str(
        request.url_for(
            "stream_resource",
            stream_id=stream_id,
            token=token,
            opaque_name=_opaque_resource_name(resource_url),
        )
    )


async def _respond(stream_id: str, url: str, request: Request, *, force_manifest: bool = False):
    session, client, upstream = await _fetch_resource(stream_id, url, request)
    content_type = upstream.headers.get("content-type", "application/octet-stream")
    is_manifest = force_manifest or "mpegurl" in content_type.lower() or url.split("?", 1)[0].endswith(".m3u8")
    if is_manifest:
        try:
            body = (await upstream.aread()).decode("utf-8", errors="replace")
            rewritten = _rewrite_manifest_urls(
                body,
                str(upstream.url),
                lambda resource_url: _gateway_url(
                    request,
                    stream_id,
                    session.register(resource_url),
                    resource_url,
                ),
            )
            return Response(
                content=rewritten,
                media_type="application/vnd.apple.mpegurl",
                headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
            )
        finally:
            await upstream.aclose()
            await client.aclose()

    async def body_iterator() -> AsyncIterator[bytes]:
        try:
            async for chunk in upstream.aiter_bytes(64 * 1024):
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    response_headers = {"Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff"}
    for source, target in (("content-length", "Content-Length"), ("content-range", "Content-Range"), ("accept-ranges", "Accept-Ranges")):
        if value := upstream.headers.get(source):
            response_headers[target] = value
    return StreamingResponse(
        body_iterator(),
        status_code=upstream.status_code,
        media_type=content_type,
        headers=response_headers,
    )


@router.get("/resolve/{video_id}", name="resolve_playback")
async def resolve_playback(
    video_id: str,
    request: Request,
    mode: Literal["video", "live"] = "video",
    presentation: Literal["audio", "video"] = "video",
    delivery: Literal["auto", "file", "hls"] = "auto",
):
    _validate_video_id(video_id)
    media_id = f"youtube:{mode}:{presentation}:{video_id}"
    if mode == "video" and delivery != "hls":
        route_name = (
            "stream_file_audio" if presentation == "audio" else "stream_file_video"
        )
        return JSONResponse(
            {
                "source": {
                    "id": media_id,
                    "url": str(request.url_for(route_name, video_id=video_id)),
                    "type": "file",
                }
            },
            headers={
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    if mode == "video":
        try:
            sources = await resolve_vod_hls_sources(video_id, audio_only=presentation == "audio")
        except YTDLPError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail={"error": exc.kind, "message": str(exc)},
            ) from exc
        stream_id = create_stream(
            headers=sources["headers"],
            inline_manifest=_vod_master_manifest(sources["video_url"], sources["audio_url"]),
        )
        return JSONResponse(
            {
                "source": {
                    "id": media_id,
                    "url": str(request.url_for("stream_master", stream_id=stream_id)),
                    "type": "hls",
                }
            },
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    stream_url = await resolve_live_url(video_id, audio_only=presentation == "audio")
    stream_id = create_stream(stream_url)
    return JSONResponse(
        {
            "source": {
                "id": media_id,
                "url": str(request.url_for("stream_master", stream_id=stream_id)),
                "type": "hls",
            }
        },
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.get("/stream/{stream_id}/master.m3u8", name="stream_master")
async def stream_master(stream_id: str, request: Request):
    if not _OPAQUE_ID.fullmatch(stream_id):
        raise HTTPException(status_code=404, detail="Stream not found")
    session = get_stream(stream_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Stream expired")
    if session.inline_manifest is not None:
        rewritten = _rewrite_manifest_urls(
            session.inline_manifest,
            "",
            lambda resource_url: _gateway_url(
                request,
                stream_id,
                session.register(resource_url),
                resource_url,
            ),
        )
        return Response(
            content=rewritten,
            media_type="application/vnd.apple.mpegurl",
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )
    if session.master_url is None:
        raise HTTPException(status_code=404, detail="Stream expired")
    return await _respond(stream_id, session.master_url, request, force_manifest=True)


@router.get("/stream/{stream_id}/resource/{token}/{opaque_name}", name="stream_resource")
async def stream_resource(stream_id: str, token: str, opaque_name: str, request: Request):
    if (
        not _OPAQUE_ID.fullmatch(stream_id)
        or not _OPAQUE_ID.fullmatch(token)
        or not _OPAQUE_RESOURCE_NAME.fullmatch(opaque_name)
    ):
        raise HTTPException(status_code=404, detail="Stream resource not found")
    session = get_stream(stream_id)
    resource_url = session.resolve(token) if session else None
    if resource_url is None:
        raise HTTPException(status_code=404, detail="Stream resource expired")
    return await _respond(stream_id, resource_url, request, force_manifest=opaque_name == "playlist.m3u8")

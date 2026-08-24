"""許可されたホストのURLのみをプロキシ（HLSセグメント用・SSRF対策済み）。"""

import httpx
from fastapi import APIRouter, HTTPException, Request, Response

from ..manifest import _get_content_type_for_ts, _rewrite_manifest_urls
from ..security import _is_safe_proxy_url, _safe_get

router = APIRouter()


@router.get("/proxy")
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

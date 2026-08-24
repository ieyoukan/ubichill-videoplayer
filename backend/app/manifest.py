"""HLSマニフェスト書き換えとTSセグメントのContent-Type検出。"""

import re
from urllib.parse import quote, urljoin

from .config import ROOT_PATH


def _rewrite_manifest_urls(content: str, base_url: str) -> str:
    """HLSマニフェスト内のURLをプロキシURL に書き換え"""

    proxy_path = f"{ROOT_PATH}/proxy" if ROOT_PATH else "/proxy"

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

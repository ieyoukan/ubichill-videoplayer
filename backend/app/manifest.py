"""HLS manifest URI rewriting helpers."""

import re
from collections.abc import Callable
from urllib.parse import urljoin

_URI_ATTRIBUTE = re.compile(r'URI="([^"]+)"')


def _rewrite_manifest_urls(content: str, base_url: str, rewrite_url: Callable[[str], str]) -> str:
    """Rewrite every HLS URI, including tag URI attributes and extensionless segments."""

    def absolute(value: str) -> str:
        return value if value.startswith(("http://", "https://")) else urljoin(base_url, value)

    rewritten: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            rewritten.append(line)
        elif stripped.startswith("#"):
            rewritten.append(_URI_ATTRIBUTE.sub(lambda match: f'URI="{rewrite_url(absolute(match.group(1)))}"', line))
        else:
            rewritten.append(rewrite_url(absolute(stripped)))
    return "\n".join(rewritten) + ("\n" if content.endswith("\n") else "")


def _get_content_type_for_ts(content: bytes, original_type: str) -> str:
    if content and content[0] == 0x47:
        return "video/MP2T"
    return original_type

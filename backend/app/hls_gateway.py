"""Opaque, bounded HLS stream/resource mapping kept entirely on the backend."""

import secrets
import threading
from collections import OrderedDict
from dataclasses import dataclass, field

from .cache import TTLCache
from .config import CACHE_MAX_SIZE

STREAM_TTL_SECONDS = 5 * 60
MAX_RESOURCES_PER_STREAM = 4096


@dataclass
class StreamSession:
    master_url: str | None
    headers: dict[str, str]
    inline_manifest: str | None = None
    resources: OrderedDict[str, str] = field(default_factory=OrderedDict)
    tokens_by_url: dict[str, str] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def register(self, url: str) -> str:
        with self.lock:
            existing = self.tokens_by_url.get(url)
            if existing:
                self.resources.move_to_end(existing)
                return existing
            token = secrets.token_urlsafe(24)
            self.resources[token] = url
            self.tokens_by_url[url] = token
            while len(self.resources) > MAX_RESOURCES_PER_STREAM:
                old_token, old_url = self.resources.popitem(last=False)
                self.tokens_by_url.pop(old_url, None)
            return token

    def resolve(self, token: str) -> str | None:
        with self.lock:
            url = self.resources.get(token)
            if url:
                self.resources.move_to_end(token)
            return url


_sessions = TTLCache(max_size=CACHE_MAX_SIZE)


def create_stream(
    master_url: str | None = None,
    headers: dict[str, str] | None = None,
    inline_manifest: str | None = None,
) -> str:
    if not master_url and not inline_manifest:
        raise ValueError("master_url or inline_manifest is required")
    stream_id = secrets.token_urlsafe(24)
    _sessions.set(
        stream_id,
        StreamSession(master_url=master_url, headers=headers or {}, inline_manifest=inline_manifest),
        STREAM_TTL_SECONDS,
    )
    return stream_id


def get_stream(stream_id: str) -> StreamSession | None:
    session = _sessions.get(stream_id)
    if session is not None:
        _sessions.set(stream_id, session, STREAM_TTL_SECONDS)
    return session

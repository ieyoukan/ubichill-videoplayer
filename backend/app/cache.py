"""シンプルなインメモリ TTL キャッシュ。"""

import threading
import time
from typing import Any, Dict, Optional, Tuple


class TTLCache:
    """スレッドセーフな TTL キャッシュ。

    検索結果 / 動画情報 / 解決済みストリーム URL をキャッシュして、
    yt-dlp への呼び出し回数を削減する。Redis 導入前の第一段階として
    プロセス内 dict で実装。複数レプリカで共有する場合は Redis に差し替える。
    """

    def __init__(self, max_size: int = 512):
        self._store: Dict[str, Tuple[Any, float]] = {}
        self._max_size = max_size
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[Any]:
        now = time.monotonic()
        with self._lock:
            item = self._store.get(key)
            if item is None:
                return None
            value, expires = item
            if expires <= now:
                del self._store[key]
                return None
            return value

    def set(self, key: str, value: Any, ttl: float) -> None:
        now = time.monotonic()
        with self._lock:
            # 肥大防止: 上限を超えたら期限切れを掃除し、それでも満杯なら最古を追い出す（FIFO）
            if len(self._store) >= self._max_size:
                for k in [k for k, (_, exp) in self._store.items() if exp <= now]:
                    del self._store[k]
                if len(self._store) >= self._max_size:
                    oldest = next(iter(self._store))
                    del self._store[oldest]
            self._store[key] = (value, now + ttl)

    def delete(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

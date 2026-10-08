"""接続元 IP の仮名ごとの回数制限。プロセス内のトークンバケット（replica 1 前提）。"""

import threading
import time
from collections import OrderedDict
from typing import Callable

from fastapi import Depends, HTTPException

from .config import RATE_LIMIT_INFO, RATE_LIMIT_RESOLVE, RATE_LIMIT_SEARCH
from .service_auth import ServiceUser, require_service_user


def parse_rate(spec: str) -> tuple[int, float]:
    """「回数/秒数」を (回数, 秒数) にする。"""
    count, _, seconds = spec.partition("/")
    parsed = (int(count), float(seconds))
    if parsed[0] <= 0 or parsed[1] <= 0:
        raise ValueError(f"回数制限の指定が不正です: {spec}")
    return parsed


class RateLimiter:
    """接続元ごとに capacity 回まで。window 秒で満タンに戻る（少しずつ回復する）。"""

    def __init__(self, capacity: int, window_seconds: float, *, max_keys: int = 10_000,
                 clock: Callable[[], float] = time.monotonic):
        self.capacity = capacity
        self.refill_per_second = capacity / window_seconds
        self.max_keys = max_keys
        self.clock = clock
        self._buckets: OrderedDict[str, tuple[float, float]] = OrderedDict()
        self._lock = threading.Lock()

    def take(self, key: str) -> float:
        """1 回分を使う。使えたら 0、使えなければ次に使えるまでの秒数を返す。"""
        now = self.clock()
        with self._lock:
            tokens, updated = self._buckets.get(key, (float(self.capacity), now))
            tokens = min(self.capacity, tokens + (now - updated) * self.refill_per_second)
            if tokens < 1:
                self._buckets[key] = (tokens, now)
                self._buckets.move_to_end(key)
                return (1 - tokens) / self.refill_per_second
            self._buckets[key] = (tokens - 1, now)
            self._buckets.move_to_end(key)
            while len(self._buckets) > self.max_keys:
                self._buckets.popitem(last=False)
            return 0.0


def rate_limited(limiter: RateLimiter):
    """FastAPI の依存。利用トークンを確かめたうえで、その接続元の回数を数える。"""

    async def dependency(user: ServiceUser = Depends(require_service_user)) -> ServiceUser:
        wait = limiter.take(user.subject)
        if wait > 0:
            raise HTTPException(
                status_code=429,
                detail={"error": "RATE_LIMITED", "message": "しばらく待ってから再度お試しください"},
                headers={"Retry-After": str(int(wait) + 1)},
            )
        return user

    return dependency


resolve_limiter = RateLimiter(*parse_rate(RATE_LIMIT_RESOLVE))
search_limiter = RateLimiter(*parse_rate(RATE_LIMIT_SEARCH))
info_limiter = RateLimiter(*parse_rate(RATE_LIMIT_INFO))

"""Simple per-user rate limiting."""

from __future__ import annotations

import time
from collections import defaultdict, deque

_HITS: dict[str, deque[float]] = defaultdict(deque)


def allow(key: str, *, max_hits: int, window_sec: float) -> bool:
    """Return True if under limit; records this hit when allowed."""
    now = time.monotonic()
    q = _HITS[key]
    while q and now - q[0] > window_sec:
        q.popleft()
    if len(q) >= max_hits:
        return False
    q.append(now)
    return True


def retry_after(key: str, *, window_sec: float) -> int:
    q = _HITS.get(key)
    if not q:
        return 0
    wait = window_sec - (time.monotonic() - q[0])
    return max(1, int(wait) + 1)

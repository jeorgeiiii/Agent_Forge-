import asyncio
import socket
import time
from collections import defaultdict
from typing import Callable
from urllib.parse import urlparse
from fastapi import HTTPException, Request, Response, status
import redis.asyncio as aioredis

from .config import get_settings


def _is_redis_reachable(url_str: str) -> bool:
    try:
        parsed = urlparse(url_str)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 6379
        with socket.create_connection((host, port), timeout=0.08):
            return True
    except OSError:
        return False


class RedisRateLimiter:
    """Distributed Sliding Window Rate Limiter powered by Redis,

    with automatic in-memory fallback for local development.
    """

    def __init__(self):
        self._redis: aioredis.Redis | None = None
        self._redis_available: bool | None = None
        # In-memory fallback: dict of client_key -> list of timestamps
        self._memory_cache: dict[str, list[float]] = defaultdict(list)

    async def get_redis(self) -> aioredis.Redis | None:
        if self._redis_available is False:
            return None
        if self._redis is None:
            settings = get_settings()
            if not _is_redis_reachable(settings.redis_url):
                self._redis_available = False
                self._redis = None
                return None

            try:
                client = aioredis.from_url(
                    settings.redis_url,
                    socket_connect_timeout=0.2,
                    socket_timeout=0.2,
                    decode_responses=True,
                )
                await client.ping()
                self._redis = client
                self._redis_available = True
            except Exception:
                self._redis_available = False
                self._redis = None
        return self._redis

    async def check_rate_limit(
        self,
        identifier: str,
        endpoint: str,
        max_requests: int,
        window_seconds: int,
    ) -> tuple[bool, int, int]:
        """Check if request is within rate limits.

        Returns: (allowed, remaining, retry_after)
        """
        now = time.time()
        key = f"ratelimit:{identifier}:{endpoint}"
        client = await self.get_redis()

        if client is not None:
            try:
                # Redis Sliding Window using Sorted Set
                clear_before = now - window_seconds
                pipeline = client.pipeline()
                pipeline.zremrangebyscore(key, "-inf", clear_before)
                pipeline.zadd(key, {str(now): now})
                pipeline.zcard(key)
                pipeline.expire(key, window_seconds)
                results = await pipeline.execute()

                current_count = results[2]
                remaining = max(0, max_requests - current_count)
                allowed = current_count <= max_requests
                retry_after = window_seconds if not allowed else 0
                return allowed, remaining, retry_after
            except Exception:
                # Fallback to memory on transient Redis error
                pass

        # In-memory sliding window fallback
        timestamps = self._memory_cache[key]
        clear_before = now - window_seconds
        # Evict old entries
        valid_timestamps = [t for t in timestamps if t > clear_before]
        valid_timestamps.append(now)
        self._memory_cache[key] = valid_timestamps

        current_count = len(valid_timestamps)
        remaining = max(0, max_requests - current_count)
        allowed = current_count <= max_requests
        retry_after = int(valid_timestamps[0] + window_seconds - now) if not allowed else 0
        return allowed, remaining, max(1, retry_after)


# Singleton instance
limiter = RedisRateLimiter()


def client_ip(request: Request) -> str:
    """The caller's IP. X-Forwarded-For is honoured ONLY when the direct peer is a
    configured trusted proxy - otherwise anyone could fake a new IP per request
    and walk straight past the rate limits."""
    peer = request.client.host if request.client else "127.0.0.1"
    trusted = {p.strip() for p in get_settings().trusted_proxies.split(",") if p.strip()}
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded and peer in trusted:
        # rightmost entry not added by one of our own proxies is the real client
        hops = [h.strip() for h in forwarded.split(",") if h.strip()]
        for hop in reversed(hops):
            if hop not in trusted:
                return hop
    return peer


async def enforce_limit(key: str, endpoint: str, max_requests: int, window_seconds: int) -> None:
    """Rate-limit on an arbitrary key (e.g. the email being logged into)."""
    allowed, _remaining, retry_after = await limiter.check_rate_limit(key, endpoint, max_requests, window_seconds)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many attempts. Please retry in {retry_after} seconds.",
            headers={"Retry-After": str(retry_after)},
        )


def rate_limit(max_requests: int = 20, window_seconds: int = 60) -> Callable:
    """FastAPI route dependency to enforce sliding-window rate limiting."""

    async def dependency(request: Request, response: Response):
        endpoint = request.url.path

        allowed, remaining, retry_after = await limiter.check_rate_limit(
            identifier=client_ip(request),
            endpoint=endpoint,
            max_requests=max_requests,
            window_seconds=window_seconds,
        )

        response.headers["X-RateLimit-Limit"] = str(max_requests)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Window"] = f"{window_seconds}s"

        if not allowed:
            response.headers["Retry-After"] = str(retry_after)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded: maximum {max_requests} requests per {window_seconds}s. Please retry in {retry_after} seconds.",
                headers={"Retry-After": str(retry_after)},
            )

    return dependency

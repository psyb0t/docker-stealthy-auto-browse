"""Eventually synchronize browser cookie jars through Redis."""

from __future__ import annotations

import asyncio
import json
import math
import os
import re
import time
from typing import Any

from logger import get_logger
from redis.asyncio import from_url as redis_from_url
from redis.exceptions import WatchError

REDIS_URL = os.environ.get("REDIS_URL")
KEY_PREFIX = "SABROWSE:"
COOKIES_KEY = f"{KEY_PREFIX}COOKIES"
UPDATE_CHANNEL = f"{KEY_PREFIX}UPDATE"
COOKIE_SYNC_INTERVAL_SECONDS = 1.0
COOKIE_SYNC_IO_TIMEOUT_SECONDS = 10.0
COOKIE_SYNC_TRANSACTION_ATTEMPTS = 5

logger = get_logger(__name__)
CookieKey = tuple[str, str, str]


class CookieSyncError(Exception):
    """Cookie synchronization could not complete safely."""


def _cookie(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise CookieSyncError("cookie must be an object")
    for field in ("name", "value", "domain", "path"):
        if not isinstance(raw.get(field), str):
            raise CookieSyncError("invalid cookie string field")
    if not raw["domain"] or not raw["path"].startswith("/"):
        raise CookieSyncError("invalid cookie scope")
    expires = raw.get("expires", -1)
    if (
        isinstance(expires, bool)
        or not isinstance(expires, (int, float))
        or not math.isfinite(expires)
        or (expires < 0 and expires != -1)
    ):
        raise CookieSyncError("invalid cookie expiry")
    result = {field: raw[field] for field in ("name", "value", "domain", "path")}
    result["expires"] = expires
    for field in ("httpOnly", "secure"):
        value = raw.get(field, False)
        if not isinstance(value, bool):
            raise CookieSyncError("invalid cookie boolean field")
        result[field] = value
    same_site = raw.get("sameSite", "Lax")
    if same_site not in ("Strict", "Lax", "None"):
        raise CookieSyncError("invalid cookie sameSite")
    result["sameSite"] = same_site
    return result


def _key(cookie: dict[str, Any]) -> CookieKey:
    return cookie["name"], cookie["domain"], cookie["path"]


def _expired(cookie: dict[str, Any]) -> bool:
    return cookie["expires"] != -1 and cookie["expires"] <= time.time()


def _field(key: CookieKey) -> str:
    # Keep the existing Redis hash format for stored profiles.
    return ":".join(key)


class RedisSync:
    """Share website and API cookie changes with stale-write protection.

    When the shared baseline changed concurrently, the committed Redis value
    wins rather than reviving stale local state.
    """

    def __init__(self) -> None:
        self._redis = None
        self._pubsub = None
        self._listen_task: asyncio.Task | None = None
        self._poll_task: asyncio.Task | None = None
        self._enabled = bool(REDIS_URL)
        self._cookie_cache: dict[CookieKey, dict[str, Any]] = {}
        self._observed: dict[CookieKey, dict[str, Any]] = {}
        self._context: Any = None
        self._lock = asyncio.Lock()
        self._wake = asyncio.Event()

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def start(self) -> None:
        if not self._enabled or self._redis is not None:
            return
        self._redis = redis_from_url(
            REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=COOKIE_SYNC_IO_TIMEOUT_SECONDS,
            socket_timeout=COOKIE_SYNC_IO_TIMEOUT_SECONDS,
        )
        try:
            await self._redis.ping()
            self._pubsub = self._redis.pubsub()
            await self._pubsub.subscribe(UPDATE_CHANNEL)
        except Exception as error:
            # Redis exception text may contain credentials.
            logger.error(
                "Redis sync startup failed", extra={"error_type": type(error).__name__}
            )
            await self.stop()
            raise CookieSyncError("Redis sync startup failed") from error
        self._listen_task = asyncio.create_task(self._listen_loop())
        logger.info("Redis cookie sync started")

    async def set_context(self, context: Any) -> None:
        if not self._enabled:
            return
        async with self._lock:
            self._context = context
            if context is not None:
                await self._synchronize(initial=True)
        if context is not None and self._poll_task is None:
            self._poll_task = asyncio.create_task(self._poll_loop())

    def clear_context(self) -> None:
        # A detached/crashed browser is not a website deleting every cookie.
        self._context = None

    async def stop(self) -> None:
        self.clear_context()
        tasks = [
            task for task in (self._poll_task, self._listen_task) if task is not None
        ]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._poll_task = self._listen_task = None
        if self._pubsub is not None:
            await self._pubsub.aclose()
            self._pubsub = None
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None
        self._cookie_cache.clear()
        self._observed.clear()
        logger.debug("Redis cookie sync stopped")

    async def _listen_loop(self) -> None:
        while True:
            try:
                message = await self._pubsub.get_message(
                    timeout=COOKIE_SYNC_INTERVAL_SECONDS
                )
                if message and message["type"] == "message":
                    # Messages only wake a read of authoritative Redis state.
                    self._wake.set()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                # A transient notification failure must not kill reconciliation.
                logger.warning(
                    "Redis notification failed; retrying",
                    extra={"error_type": type(error).__name__},
                )
                await asyncio.sleep(COOKIE_SYNC_INTERVAL_SECONDS)

    async def _poll_loop(self) -> None:
        while True:
            self._wake.clear()
            try:
                async with self._lock:
                    if self._context is not None:
                        await self._synchronize()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                # Retain the last successful baseline so local changes retry.
                logger.warning(
                    "Cookie sync failed; retrying",
                    extra={"error_type": type(error).__name__},
                )
            try:
                await asyncio.wait_for(self._wake.wait(), COOKIE_SYNC_INTERVAL_SECONDS)
            except TimeoutError:
                continue

    async def _synchronize(
        self,
        *,
        initial: bool = False,
        force_keys: set[CookieKey] | None = None,
    ) -> None:
        context = self._context
        if context is None or self._redis is None:
            return
        local = {_key(item): item for item in map(_cookie, await context.cookies())}
        changed = {
            key: value
            for key, value in local.items()
            if self._observed.get(key) != value
        }
        removed = self._observed.keys() - local.keys()
        for _ in range(COOKIE_SYNC_TRANSACTION_ATTEMPTS):
            async with self._redis.pipeline(transaction=True) as pipeline:
                await pipeline.watch(COOKIES_KEY)
                stored = await pipeline.hgetall(COOKIES_KEY)
                shared = {}
                for field, raw in stored.items():
                    try:
                        value = _cookie(json.loads(raw))
                        if field != _field(_key(value)):
                            raise CookieSyncError("cookie identity mismatch")
                    except (ValueError, TypeError, CookieSyncError):
                        logger.warning(
                            "Invalid shared cookie ignored",
                            extra={"reason": "invalid_cookie_record"},
                        )
                        continue
                    if not _expired(value):
                        shared[_key(value)] = value
                target = dict(shared)
                for key, value in changed.items():
                    may_write = (
                        key not in shared
                        if initial
                        else shared.get(key) == self._cookie_cache.get(key)
                    )
                    if force_keys and key in force_keys:
                        may_write = True
                    if may_write and not _expired(value):
                        target[key] = value
                if not initial:
                    for key in removed:
                        if shared.get(key) == self._cookie_cache.get(key):
                            target.pop(key, None)
                encoded = {
                    _field(key): json.dumps(value, sort_keys=True)
                    for key, value in target.items()
                }
                updates = {
                    field: raw
                    for field, raw in encoded.items()
                    if stored.get(field) != raw
                }
                deleted = stored.keys() - encoded.keys()
                pipeline.multi()
                if updates:
                    pipeline.hset(COOKIES_KEY, mapping=updates)
                if deleted:
                    pipeline.hdel(COOKIES_KEY, *deleted)
                if updates or deleted:
                    pipeline.publish(UPDATE_CHANNEL, json.dumps({"kind": "changed"}))
                try:
                    await pipeline.execute()
                except WatchError:
                    continue
                break
        else:
            raise CookieSyncError("cookie sync transaction contention")

        if context is not self._context:
            return
        for key in local.keys() - target.keys():
            await context.clear_cookies(
                name=re.compile(f"^{re.escape(key[0])}$"),
                domain=re.compile(f"^{re.escape(key[1])}$"),
                path=re.compile(f"^{re.escape(key[2])}$"),
            )
        incoming = [value for key, value in target.items() if local.get(key) != value]
        if incoming:
            await context.add_cookies(incoming)
        # A concurrent website write must differ from this baseline next pass.
        self._cookie_cache = target
        self._observed = dict(target)
        if updates or deleted:
            logger.debug(
                "Shared cookie changes committed",
                extra={"updated_count": len(updates), "deleted_count": len(deleted)},
            )

    async def set_cookie(self, cookie: dict[str, Any], context: Any) -> None:
        async with self._lock:
            await context.add_cookies([cookie])
            if self._enabled:
                # Explicit API writes are intentional, not stale jar replay.
                current = await context.cookies(cookie.get("url"))
                force_keys = {
                    _key(item)
                    for item in current
                    if item["name"] == cookie["name"]
                    and item["value"] == cookie["value"]
                    and ("domain" not in cookie or item["domain"] == cookie["domain"])
                    and ("path" not in cookie or item["path"] == cookie["path"])
                }
                await self._synchronize(force_keys=force_keys)

    async def get_cookies(
        self, context: Any, urls: list[str] | None = None
    ) -> list[dict]:
        async with self._lock:
            if self._enabled:
                await self._synchronize()
            return await context.cookies(urls)

    async def delete_cookies(self, context: Any) -> None:
        async with self._lock:
            await context.clear_cookies()
            if not self._enabled:
                return
            async with self._redis.pipeline(transaction=True) as pipeline:
                pipeline.delete(COOKIES_KEY)
                pipeline.publish(UPDATE_CHANNEL, json.dumps({"kind": "clear_cookies"}))
                await pipeline.execute()
            self._observed.clear()
            self._cookie_cache.clear()

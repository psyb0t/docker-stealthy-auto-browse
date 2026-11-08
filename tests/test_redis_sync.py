"""Cookie validation and real-Redis reconciliation regression tests."""

from __future__ import annotations

import asyncio
import copy
import json
import os
import re
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, "/app")

import redis_sync
from redis.asyncio import from_url

TEST_COOKIE = {
    "name": "session",
    "value": "EXAMPLE-DO-NOT-USE",
    "domain": "example.com",
    "path": "/",
    "expires": -1,
    "secure": False,
    "httpOnly": True,
    "sameSite": "Lax",
}
TEST_TIMEOUT_SECONDS = 5
TEST_POLL_SECONDS = 0.05


class CookieValidationTests(unittest.TestCase):
    def test_cookie_shapes(self):
        for invalid in (
            None,
            [],
            {},
            {**TEST_COOKIE, "expires": float("nan")},
            {**TEST_COOKIE, "expires": True},
            {**TEST_COOKIE, "secure": "true"},
            {**TEST_COOKIE, "sameSite": "invalid"},
            {**TEST_COOKIE, "path": "relative"},
        ):
            with (
                self.subTest(invalid=invalid),
                self.assertRaises(redis_sync.CookieSyncError),
            ):
                redis_sync._cookie(invalid)
        self.assertEqual(redis_sync._cookie(TEST_COOKIE), TEST_COOKIE)

    def test_expiry_boundary_and_session_cookie(self):
        with patch.object(redis_sync.time, "time", return_value=100):
            for expires, expected in (
                (-1, False),
                (99, True),
                (100, True),
                (101, False),
            ):
                self.assertEqual(
                    redis_sync._expired({**TEST_COOKIE, "expires": expires}), expected
                )


class BrowserJar:
    """Browser boundary double; Redis remains the real infrastructure."""

    def __init__(self):
        self.jar = {}
        self.fail = False

    async def cookies(self, urls=None):
        if self.fail:
            raise RuntimeError("EXAMPLE-DO-NOT-LOG-cookie-value")
        return copy.deepcopy(
            [
                c
                for c in self.jar.values()
                if c["expires"] == -1 or c["expires"] > time.time()
            ]
        )

    async def add_cookies(self, cookies):
        for cookie in cookies:
            self.jar[(cookie["name"], cookie["domain"], cookie["path"])] = (
                copy.deepcopy(cookie)
            )

    async def clear_cookies(self, **filters):
        for key, cookie in list(self.jar.items()):
            if all(
                pattern.fullmatch(cookie[field]) for field, pattern in filters.items()
            ):
                del self.jar[key]


class RedisReconciliationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.url = os.environ["REDIS_TEST_URL"]
        self.client = from_url(self.url, decode_responses=True)
        await self.client.flushdb()
        self.patches = [
            patch.object(redis_sync, "REDIS_URL", self.url),
            patch.object(redis_sync, "COOKIE_SYNC_INTERVAL_SECONDS", TEST_POLL_SECONDS),
        ]
        for item in self.patches:
            item.start()
        self.syncs, self.jars = [], []
        for _ in range(2):
            await self.join()

    async def join(self):
        sync, jar = redis_sync.RedisSync(), BrowserJar()
        self.syncs.append(sync)
        self.jars.append(jar)
        await sync.start()
        await sync.set_context(jar)

    async def asyncTearDown(self):
        for sync in self.syncs:
            await sync.stop()
        await self.client.aclose()
        for item in reversed(self.patches):
            item.stop()

    async def converge(self, expected):
        async def wait():
            while True:
                actual = [await jar.cookies() for jar in self.jars]
                if all(
                    sorted(items, key=str) == sorted(expected, key=str)
                    for items in actual
                ):
                    return
                await asyncio.sleep(TEST_POLL_SECONDS)

        await asyncio.wait_for(wait(), TEST_TIMEOUT_SECONDS)

    async def test_website_changes_delete_and_no_echo(self):
        for value in ("first", "second", "caf\u00e9"):
            cookie = {**TEST_COOKIE, "value": value}
            await self.jars[0].add_cookies([cookie])
            await self.converge([cookie])
        stored = await self.client.hgetall(redis_sync.COOKIES_KEY)
        await asyncio.sleep(TEST_POLL_SECONDS * 4)
        self.assertEqual(await self.client.hgetall(redis_sync.COOKIES_KEY), stored)
        async with self.client.pubsub() as observer:
            await observer.subscribe(redis_sync.UPDATE_CHANNEL)
            await observer.get_message(timeout=TEST_TIMEOUT_SECONDS)
            message = await observer.get_message(
                ignore_subscribe_messages=True,
                timeout=TEST_POLL_SECONDS * 4,
            )
            self.assertIsNone(message, "unchanged jars must not publish a feedback loop")
        await self.jars[0].clear_cookies()
        await self.converge([])
        self.assertEqual(await self.client.hlen(redis_sync.COOKIES_KEY), 0)

    async def test_distinct_paths_concurrent_writes_and_late_join(self):
        cookies = [TEST_COOKIE, {**TEST_COOKIE, "path": "/other", "value": "other"}]
        await asyncio.gather(
            *(jar.add_cookies([cookie]) for jar, cookie in zip(self.jars, cookies))
        )
        await self.converge(cookies)
        await self.join()
        await self.converge(cookies)
        await self.jars[0].clear_cookies(path=re.compile("^/other$"))
        await self.converge([TEST_COOKIE])

    async def test_expiry_and_explicit_clear_do_not_resurrect(self):
        cookie = {**TEST_COOKIE, "expires": time.time() + 0.5}
        await self.jars[0].add_cookies([cookie])
        await self.converge([cookie])
        await asyncio.sleep(0.6)
        await self.converge([])
        await self.jars[0].add_cookies([TEST_COOKIE])
        await self.converge([TEST_COOKIE])
        await self.syncs[1].delete_cookies(self.jars[1])
        await self.converge([])
        await asyncio.sleep(TEST_POLL_SECONDS * 4)
        self.assertEqual(await self.client.hlen(redis_sync.COOKIES_KEY), 0)

    async def test_detach_failure_and_malformed_state(self):
        await self.jars[0].add_cookies([TEST_COOKIE])
        await self.converge([TEST_COOKIE])
        self.syncs[1].clear_context()
        await asyncio.sleep(TEST_POLL_SECONDS * 3)
        self.assertEqual(await self.client.hlen(redis_sync.COOKIES_KEY), 1)
        await self.syncs[1].set_context(self.jars[1])
        await self.client.hset(redis_sync.COOKIES_KEY, "bad", json.dumps(["invalid"]))
        await self.client.publish(redis_sync.UPDATE_CHANNEL, "not-json")
        self.jars[0].fail = True
        with self.assertLogs(redis_sync.logger, level="WARNING") as logs:
            await asyncio.sleep(TEST_POLL_SECONDS * 3)
        self.assertNotIn("EXAMPLE-DO-NOT-LOG", " ".join(logs.output))
        self.jars[0].fail = False
        updated = {**TEST_COOKIE, "value": "recovered"}
        await self.jars[0].add_cookies([updated])
        await self.converge([updated])

    async def test_disabled_sync_has_no_tasks(self):
        with patch.object(redis_sync, "REDIS_URL", None):
            sync = redis_sync.RedisSync()
            await sync.start()
            await sync.set_context(BrowserJar())
            self.assertFalse(sync.enabled)
            self.assertIsNone(sync._poll_task)
            await sync.stop()

    async def test_stale_concurrent_same_key_converges(self):
        await self.jars[0].add_cookies([TEST_COOKIE])
        await self.converge([TEST_COOKIE])
        first = {**TEST_COOKIE, "value": "first-commit"}
        second = {**TEST_COOKIE, "value": "stale-second"}
        async with self.syncs[1]._lock:
            await self.jars[0].add_cookies([first])
            await self.jars[1].add_cookies([second])
            await asyncio.wait_for(
                self._wait_shared_value("first-commit"), TEST_TIMEOUT_SECONDS
            )
        await self.converge([first])

    async def _wait_shared_value(self, expected):
        while True:
            values = await self.client.hvals(redis_sync.COOKIES_KEY)
            if values and json.loads(values[0])["value"] == expected:
                return
            await asyncio.sleep(TEST_POLL_SECONDS)

    async def test_network_reconnect_and_shutdown(self):
        for sync in self.syncs:
            await sync._redis.connection_pool.disconnect()
        cookie = {**TEST_COOKIE, "value": "after-reconnect"}
        await self.jars[0].add_cookies([cookie])
        await self.converge([cookie])
        await self.syncs[1].stop()
        self.assertIsNone(self.syncs[1]._poll_task)
        self.assertIsNone(self.syncs[1]._listen_task)
        self.assertEqual(await self.client.hlen(redis_sync.COOKIES_KEY), 1)

    async def test_explicit_api_update_wins_over_stale_baseline(self):
        task = self.syncs[1]._poll_task
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self.syncs[1]._poll_task = None
        await self.jars[0].add_cookies([TEST_COOKIE])
        await asyncio.wait_for(
            self._wait_shared_value(TEST_COOKIE["value"]), TEST_TIMEOUT_SECONDS
        )
        replacement = {**TEST_COOKIE, "value": "explicit-update"}
        await self.syncs[1].set_cookie(replacement, self.jars[1])
        await asyncio.wait_for(
            self._wait_shared_value("explicit-update"), TEST_TIMEOUT_SECONDS
        )
        await self.syncs[1].set_context(self.jars[1])
        await self.converge([replacement])


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(CookieValidationTests)
    if "--integration" in sys.argv:
        suite.addTests(
            unittest.defaultTestLoader.loadTestsFromTestCase(RedisReconciliationTests)
        )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(not result.wasSuccessful())

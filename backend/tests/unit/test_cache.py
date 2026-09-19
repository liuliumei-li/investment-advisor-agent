"""Cache 单测:JSON 读写/删除与 Redis 故障降级(只告警不阻断)。"""

import pytest
from redis import exceptions as redis_exceptions

from app.cache.redis_client import Cache, profile_cache_key


class TestProfileCacheKey:
    def test_key_format(self):
        assert profile_cache_key(7) == "profile:7"


class TestCacheRoundtrip:
    async def test_set_get_delete(self, cache):
        await cache.set_json("k", {"a": 1}, ttl=60)
        assert await cache.get_json("k") == {"a": 1}
        await cache.delete("k")
        assert await cache.get_json("k") is None

    async def test_get_missing_key_returns_none(self, cache):
        assert await cache.get_json("no-such-key") is None

    async def test_non_ascii_roundtrip(self, cache):
        await cache.set_json("k", {"name": "保守型"})
        assert await cache.get_json("k") == {"name": "保守型"}


class _BrokenRedis:
    """模拟 Redis 故障:全部操作抛 RedisError。"""

    async def get(self, key):
        raise redis_exceptions.ConnectionError("boom")

    async def set(self, key, value, ex=None):
        raise redis_exceptions.ConnectionError("boom")

    async def delete(self, key):
        raise redis_exceptions.ConnectionError("boom")


class TestRedisFailureDegradation:
    @pytest.fixture
    def broken_cache(self):
        return Cache(_BrokenRedis())

    async def test_get_failure_degrades_to_miss(self, broken_cache):
        assert await broken_cache.get_json("k") is None

    async def test_set_failure_is_swallowed(self, broken_cache):
        await broken_cache.set_json("k", {"a": 1}, ttl=60)  # 不抛异常

    async def test_delete_failure_is_swallowed(self, broken_cache):
        await broken_cache.delete("k")  # 不抛异常

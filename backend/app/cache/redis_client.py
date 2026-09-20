"""Redis 缓存封装与键规范(architecture.md §4.4:统一经本模块访问)。

缓存故障只告警不阻断主流程(写透失效,极端情况由 TTL 兜底)。
"""

import json
import logging
from typing import Any

from redis import exceptions as redis_exceptions
from redis.asyncio import Redis

from app.core.config import settings

logger = logging.getLogger(__name__)

# profile:{user_id}:画像热缓存,10 分钟 TTL,画像更新即失效(architecture.md §4.4)
PROFILE_CACHE_TTL_SECONDS = 600

# session:ctx:{session_id}:多轮对话上下文(US-02 对话画像、US-20 咨询会话),30 分钟 TTL 活动续期
SESSION_CTX_TTL_SECONDS = 1800


def profile_cache_key(user_id: int) -> str:
    return f"profile:{user_id}"


def session_ctx_key(session_id: str) -> str:
    return f"session:ctx:{session_id}"


class Cache:
    def __init__(self, client: Any):
        self._client = client

    async def get_json(self, key: str):
        try:
            raw = await self._client.get(key)
        except redis_exceptions.RedisError:
            logger.warning("Redis 读取失败,key=%s,降级为缓存未命中", key)
            return None
        return json.loads(raw) if raw is not None else None

    async def set_json(self, key: str, value: Any, ttl: int | None = None) -> None:
        try:
            await self._client.set(key, json.dumps(value, ensure_ascii=False, default=str), ex=ttl)
        except redis_exceptions.RedisError:
            logger.warning("Redis 写入失败,key=%s,跳过缓存", key)

    async def delete(self, key: str) -> None:
        try:
            await self._client.delete(key)
        except redis_exceptions.RedisError:
            logger.warning("Redis 删除失败,key=%s,依赖 TTL 兜底", key)


_cache: Cache | None = None


def get_cache() -> Cache:
    """FastAPI 依赖:进程级单例(测试经 dependency_overrides 替换)。"""
    global _cache
    if _cache is None:
        _cache = Cache(Redis.from_url(settings.redis_url, decode_responses=True))
    return _cache

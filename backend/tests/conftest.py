"""测试夹具:内存 SQLite(StaticPool)、fakeredis 缓存、FastAPI TestClient(architecture.md §6.2)。"""

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.cache.redis_client import Cache, get_cache
from app.core.deps import get_db
from app.data.questionnaire_v1 import QUESTIONNAIRE_V1
from app.db.base import Base
from app.main import app
from app.models.questionnaire import Questionnaire


@pytest_asyncio.fixture
async def engine():
    """内存 SQLite 引擎:StaticPool 复用单连接,保证跨会话可见同一份数据。"""
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest_asyncio.fixture
async def db_session(session_factory):
    """单测试会话:测试内提交的数据跨查询可见,测试结束回滚隔离。"""
    async with session_factory() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def cache():
    """fakeredis 注入的 Cache:与真实 Redis 同 API,无需外部服务。"""
    from fakeredis.aioredis import FakeRedis

    client = FakeRedis(decode_responses=True)
    yield Cache(client)
    await client.aclose()


@pytest_asyncio.fixture
async def seeded_questionnaire(session_factory) -> int:
    """写入问卷 v1(与 scripts/seed_questionnaire.py 同逻辑),返回问卷 id。"""
    async with session_factory() as session:
        questionnaire = Questionnaire(**QUESTIONNAIRE_V1)
        session.add(questionnaire)
        await session.commit()
        return questionnaire.id


@pytest_asyncio.fixture
async def client(session_factory, cache):
    """TestClient:覆盖 get_db/get_cache,其余依赖(鉴权等)走真实链路。"""

    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_cache] = lambda: cache
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as test_client:
        yield test_client
    app.dependency_overrides.clear()

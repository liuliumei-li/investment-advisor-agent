"""数据库引擎与会话工厂(dev/test 用 SQLite,生产用 PostgreSQL)。"""

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings

engine = create_async_engine(settings.database_url, echo=settings.debug)
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)

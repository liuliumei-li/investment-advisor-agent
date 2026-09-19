"""FastAPI 通用依赖:鉴权与数据库会话。"""

from collections.abc import AsyncIterator

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotAuthenticated
from app.core.security import decode_access_token
from app.db.session import async_session_factory

bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user_id(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> int:
    """JWT Bearer 鉴权(architecture.md §5.1:除 auth/* 外全部接口需认证)。"""
    if credentials is None:
        raise NotAuthenticated()
    return decode_access_token(credentials.credentials)


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖:每请求一个会话。"""
    async with async_session_factory() as session:
        yield session

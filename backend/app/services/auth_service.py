"""认证业务编排:注册与登录(事务边界在 Service 层,architecture.md §3.1)。"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationFailed
from app.core.security import create_access_token, hash_password, verify_password
from app.models.user import User
from app.repositories.user_repo import UserRepository


class AuthService:
    def __init__(self, repo: UserRepository, session: AsyncSession):
        self.repo = repo
        self.session = session

    async def register(self, username: str, password: str) -> User:
        existing = await self.repo.get_by_username(username)
        if existing is not None:
            raise ValidationFailed("用户名已存在")
        user = await self.repo.create(username, hash_password(password))
        await self.session.commit()
        return user

    async def login(self, username: str, password: str) -> tuple[User, str]:
        """校验用户名密码,返回 (user, access_token)。"""
        user = await self.repo.get_by_username(username)
        if user is None or not verify_password(password, user.password_hash):
            raise ValidationFailed("用户名或密码错误")
        return user, create_access_token(user.id)

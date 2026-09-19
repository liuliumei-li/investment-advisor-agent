"""AuthService 单测:注册/登录与密码哈希、JWT 签发链路。"""

import pytest

from app.core.exceptions import ValidationFailed
from app.core.security import decode_access_token, verify_password
from app.repositories.user_repo import UserRepository
from app.services.auth_service import AuthService


def make_service(session) -> AuthService:
    return AuthService(UserRepository(session), session)


class TestRegister:
    async def test_register_creates_user_with_hashed_password(self, db_session):
        service = make_service(db_session)
        user = await service.register("alice", "secret123")

        assert user.id is not None
        assert user.password_hash != "secret123"  # 禁止明文入库
        assert verify_password("secret123", user.password_hash)
        # 事务已提交,同会话可查
        assert await UserRepository(db_session).get_by_username("alice") is not None

    async def test_duplicate_username_rejected(self, db_session):
        service = make_service(db_session)
        await service.register("alice", "secret123")
        with pytest.raises(ValidationFailed, match="用户名已存在"):
            await service.register("alice", "another456")


class TestLogin:
    @pytest.fixture
    async def registered_user(self, db_session):
        service = make_service(db_session)
        user = await service.register("alice", "secret123")
        return user

    async def test_login_returns_user_and_valid_token(self, db_session, registered_user):
        service = make_service(db_session)
        user, token = await service.login("alice", "secret123")

        assert user.id == registered_user.id
        assert decode_access_token(token) == user.id  # token 可解码回 user_id

    async def test_wrong_password_rejected(self, db_session, registered_user):
        service = make_service(db_session)
        with pytest.raises(ValidationFailed, match="用户名或密码错误"):
            await service.login("alice", "wrong-pass")

    async def test_unknown_username_rejected(self, db_session):
        service = make_service(db_session)
        with pytest.raises(ValidationFailed, match="用户名或密码错误"):
            await service.login("nobody", "secret123")

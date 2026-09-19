"""security 单测:bcrypt 哈希与 JWT 签发/校验(含过期与非法 token 分支)。"""

from datetime import datetime, timedelta, timezone

import jwt
import pytest

from app.core.config import settings
from app.core.exceptions import NotAuthenticated, TokenExpired
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


class TestPasswordHashing:
    def test_hash_and_verify_roundtrip(self):
        hashed = hash_password("secret123")
        assert hashed != "secret123"
        assert verify_password("secret123", hashed)

    def test_wrong_password_fails(self):
        hashed = hash_password("secret123")
        assert not verify_password("other456", hashed)

    def test_same_password_hashes_differently(self):
        # 随机盐:同一密码两次哈希结果不同
        assert hash_password("secret123") != hash_password("secret123")


class TestJwt:
    def test_roundtrip(self):
        token = create_access_token(42)
        assert decode_access_token(token) == 42

    def test_expired_token_raises_token_expired(self):
        now = datetime.now(timezone.utc)
        payload = {
            "sub": "1",
            "iat": now - timedelta(hours=1),
            "exp": now - timedelta(minutes=1),
        }
        token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
        with pytest.raises(TokenExpired):
            decode_access_token(token)

    def test_invalid_token_raises_not_authenticated(self):
        with pytest.raises(NotAuthenticated):
            decode_access_token("not-a-valid-token")

    def test_wrong_secret_token_rejected(self):
        token = jwt.encode(
            {"sub": "1", "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
            "another-secret-that-is-long-enough-for-hs256",
            algorithm="HS256",
        )
        with pytest.raises(NotAuthenticated):
            decode_access_token(token)

"""认证接口:注册与登录(Controller 仅取参 → 调 Service → 返回)。"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.core.response import ApiResponse
from app.repositories.user_repo import UserRepository
from app.schemas.auth import LoginRequest, RegisterRequest, TokenData
from app.services.auth_service import AuthService

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def get_auth_service(session: AsyncSession = Depends(get_db)) -> AuthService:
    return AuthService(UserRepository(session), session)


@router.post("/register")
async def register(payload: RegisterRequest, service: AuthService = Depends(get_auth_service)):
    user = await service.register(payload.username, payload.password)
    return ApiResponse(data={"user_id": user.id, "username": user.username})


@router.post("/login")
async def login(payload: LoginRequest, service: AuthService = Depends(get_auth_service)):
    user, token = await service.login(payload.username, payload.password)
    return ApiResponse(data=TokenData(access_token=token, user_id=user.id).model_dump())

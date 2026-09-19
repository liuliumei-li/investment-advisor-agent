"""user_profiles 表数据访问(单一实体,无业务规则)。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user_profile import UserProfile


class ProfileRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_user_id(self, user_id: int) -> UserProfile | None:
        result = await self.session.execute(select(UserProfile).where(UserProfile.user_id == user_id))
        return result.scalar_one_or_none()

    async def save(self, profile: UserProfile) -> UserProfile:
        """新增或更新(由 Service 决定新建/修改后统一经此落库)。"""
        self.session.add(profile)
        await self.session.flush()
        return profile

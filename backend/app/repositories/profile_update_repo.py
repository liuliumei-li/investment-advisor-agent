"""profile_update_events 表数据访问(单一实体,无业务规则)。"""

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.profile_update_event import ProfileUpdateEvent


class ProfileUpdateRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def add(self, event: ProfileUpdateEvent) -> ProfileUpdateEvent:
        self.session.add(event)
        await self.session.flush()
        return event

    async def list_for_user(self, user_id: int, offset: int, limit: int) -> list[ProfileUpdateEvent]:
        """该用户更新事件分页(按 id 降序,最新在前)。"""
        result = await self.session.execute(
            select(ProfileUpdateEvent)
            .where(ProfileUpdateEvent.user_id == user_id)
            .order_by(desc(ProfileUpdateEvent.id))
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def count_for_user(self, user_id: int) -> int:
        result = await self.session.execute(
            select(func.count()).select_from(ProfileUpdateEvent).where(ProfileUpdateEvent.user_id == user_id)
        )
        return result.scalar_one()

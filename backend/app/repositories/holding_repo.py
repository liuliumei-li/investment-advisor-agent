"""holdings / holding_snapshots 数据访问(单一实体,无业务规则)。"""

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.holding import Holding, HoldingSnapshot


class HoldingRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_snapshot(self, snapshot: HoldingSnapshot) -> HoldingSnapshot:
        self.session.add(snapshot)
        await self.session.flush()
        return snapshot

    async def add_holdings(self, holdings: list[Holding]) -> None:
        self.session.add_all(holdings)
        await self.session.flush()

    async def latest_snapshots(self, user_id: int, limit: int = 2) -> list[HoldingSnapshot]:
        """该用户最近 limit 个导入快照(按 id 降序,最新在前)。"""
        result = await self.session.execute(
            select(HoldingSnapshot)
            .where(HoldingSnapshot.user_id == user_id)
            .order_by(desc(HoldingSnapshot.id))
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_holdings_by_snapshot(self, snapshot_id: int) -> list[Holding]:
        result = await self.session.execute(
            select(Holding).where(Holding.snapshot_id == snapshot_id).order_by(Holding.id)
        )
        return list(result.scalars().all())

    async def get_latest_holdings(self, user_id: int) -> list[Holding]:
        """最新快照的持仓(当前持仓);无快照时返回空列表。"""
        snapshots = await self.latest_snapshots(user_id, limit=1)
        if not snapshots:
            return []
        return await self.get_holdings_by_snapshot(snapshots[0].id)

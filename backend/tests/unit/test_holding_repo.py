"""holding_snapshots / holdings 仓储层单测(US-03 Task 1)。"""

from decimal import Decimal

from app.models.holding import AssetType, Holding, HoldingSnapshot
from app.repositories.holding_repo import HoldingRepository


def _holding(snapshot_id, user_id, name="贵州茅台", quantity="100", price="1500"):
    return Holding(
        snapshot_id=snapshot_id,
        user_id=user_id,
        asset_type=AssetType.STOCK,
        code="600519",
        name=name,
        quantity=Decimal(quantity),
        cost_price=Decimal(price),
    )


class TestHoldingRepository:
    async def test_create_snapshot_and_holdings(self, db_session):
        repo = HoldingRepository(db_session)
        snapshot = await repo.create_snapshot(HoldingSnapshot(user_id=1, source="list"))
        assert snapshot.id is not None

        await repo.add_holdings([_holding(snapshot.id, 1), _holding(snapshot.id, 1, name="五粮液")])
        rows = await repo.get_holdings_by_snapshot(snapshot.id)
        assert len(rows) == 2
        assert rows[0].asset_type is AssetType.STOCK
        assert rows[0].cost_price == Decimal("1500")

    async def test_latest_snapshots_orders_desc_and_isolates_users(self, db_session):
        repo = HoldingRepository(db_session)
        s1 = await repo.create_snapshot(HoldingSnapshot(user_id=1, source="csv"))
        s2 = await repo.create_snapshot(HoldingSnapshot(user_id=1, source="text"))
        await repo.create_snapshot(HoldingSnapshot(user_id=2, source="list"))

        latest = await repo.latest_snapshots(1, limit=2)
        assert [s.id for s in latest] == [s2.id, s1.id]  # 最新在前
        assert await repo.latest_snapshots(9999) == []

    async def test_get_latest_holdings_returns_newest_snapshot_rows(self, db_session):
        repo = HoldingRepository(db_session)
        s1 = await repo.create_snapshot(HoldingSnapshot(user_id=1, source="list"))
        await repo.add_holdings([_holding(s1.id, 1)])
        s2 = await repo.create_snapshot(HoldingSnapshot(user_id=1, source="csv"))
        await repo.add_holdings([_holding(s2.id, 1, name="宁德时代", quantity="200", price="200")])

        rows = await repo.get_latest_holdings(1)
        assert len(rows) == 1
        assert rows[0].snapshot_id == s2.id
        assert rows[0].name == "宁德时代"

    async def test_get_latest_holdings_empty_without_snapshot(self, db_session):
        assert await HoldingRepository(db_session).get_latest_holdings(1) == []

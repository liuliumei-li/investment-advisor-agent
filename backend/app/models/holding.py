"""holdings 持仓表与 holding_snapshots 导入快照表(architecture.md §4.2,US-03)。

每次导入生成一个快照批次(holding_snapshots),该批次持仓行共享 snapshot_id;
最新批次 = 当前持仓,最近两个批次对比得出换手特征(US-03 AC-2)。
"""

import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AssetType(str, enum.Enum):
    """持仓资产类别(architecture.md §4.2)。"""

    STOCK = "stock"  # 股票
    ETF = "etf"  # ETF
    CB = "cb"  # 可转债
    FUND = "fund"  # 基金


ASSET_TYPE_LABELS: dict[AssetType, str] = {
    AssetType.STOCK: "股票",
    AssetType.ETF: "ETF",
    AssetType.CB: "可转债",
    AssetType.FUND: "基金",
}


class HoldingSnapshot(Base):
    """持仓导入快照:一次导入一个批次,记录导入方式与时间(US-03 换手特征数据基础)。"""

    __tablename__ = "holding_snapshots"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), ForeignKey("users.id"), nullable=False, index=True
    )
    source: Mapped[str] = mapped_column(String(20), nullable=False)  # list / csv / text
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Holding(Base):
    """持仓行:归属于某次导入快照(architecture.md §4.2 规划 + 快照扩展)。"""

    __tablename__ = "holdings"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    snapshot_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), ForeignKey("holding_snapshots.id"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), ForeignKey("users.id"), nullable=False, index=True
    )
    asset_type: Mapped[AssetType] = mapped_column(Enum(AssetType, name="assettype"), nullable=False)
    code: Mapped[str | None] = mapped_column(String(20))
    name: Mapped[str | None] = mapped_column(String(50))
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(16, 4))
    cost_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

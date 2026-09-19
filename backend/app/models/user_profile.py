"""user_profiles 用户画像表(architecture.md §4.2,BR-IMG-01/02 四要素)。"""

import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Enum, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RiskLevel(str, enum.Enum):
    """BR-IMG-01 风险等级五档。"""

    C1 = "C1"  # 保守型
    C2 = "C2"  # 稳健型
    C3 = "C3"  # 平衡型
    C4 = "C4"  # 进取型
    C5 = "C5"  # 激进型


RISK_LEVEL_LABELS: dict[RiskLevel, str] = {
    RiskLevel.C1: "保守型",
    RiskLevel.C2: "稳健型",
    RiskLevel.C3: "平衡型",
    RiskLevel.C4: "进取型",
    RiskLevel.C5: "激进型",
}


class UserProfile(Base):
    """用户画像:一个用户一份当前画像(user_id 唯一),更新以 version 递增留痕。"""

    __tablename__ = "user_profiles"

    # SQLite 仅 INTEGER PRIMARY KEY 支持自增,故对 SQLite 降级为 Integer(PostgreSQL 仍为 BIGSERIAL)
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), ForeignKey("users.id"), unique=True, nullable=False
    )
    risk_level: Mapped[RiskLevel] = mapped_column(Enum(RiskLevel), nullable=False)
    return_expectation_low: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    return_expectation_high: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    investment_horizon: Mapped[str | None] = mapped_column(String(20))
    holding_habit_summary: Mapped[str | None] = mapped_column(Text)
    source_mix: Mapped[dict | None] = mapped_column(JSON)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))
    confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

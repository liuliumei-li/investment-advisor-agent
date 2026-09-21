"""profile_update_events 画像更新历史表(US-05 AC-3、BR-IMG-06)。

每次画像版本递增留存一条事件:何时(created_at)、因何(trigger)、由哪一要素变化引起(changes),
冲突主张另列(conflicts)。触发标签常量单点定义于 app/services/profile_history.py。
"""

from datetime import datetime

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ProfileUpdateEvent(Base):
    """画像更新事件:user_profiles.version 递增的审计留痕(US-05 更新历史可查)。"""

    __tablename__ = "profile_update_events"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), ForeignKey("users.id"), nullable=False, index=True
    )
    # 本次更新后的画像版本(与 user_profiles.version 对应,BR-IMG-06)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    # 触发来源:问卷测评 / 对话更新 / 持仓更新 / 用户修正 / 用户确认
    trigger: Mapped[str] = mapped_column(String(20), nullable=False)
    # 实际变化的要素:[{field, before, after, source, quote}],JSON 原生类型
    changes: Mapped[list | None] = mapped_column(JSON)
    # 本次触发中保留原值的冲突主张:[{field, current, proposed, source, quote}]
    conflicts: Mapped[list | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

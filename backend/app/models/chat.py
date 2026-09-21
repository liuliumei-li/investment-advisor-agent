"""chat_sessions / chat_messages 咨询会话与消息表(US-06 起启用,US-20 上下文记忆)。"""

import enum
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Scenario(str, enum.Enum):
    """六类投顾场景(BR-ADV-03);US-06 仅开放 market,其余随 US-07~11 逐步启用。"""

    MARKET = "market"  # 大盘研判
    INDUSTRY = "industry"
    STOCK = "stock"
    ETF = "etf"
    CB = "cb"
    PORTFOLIO = "portfolio"
    GENERAL = "general"


SCENARIO_LABELS: dict[Scenario, str] = {
    Scenario.MARKET: "大盘研判",
    Scenario.INDUSTRY: "行业配置",
    Scenario.STOCK: "个股分析",
    Scenario.ETF: "ETF 筛选",
    Scenario.CB: "可转债投资",
    Scenario.PORTFOLIO: "资产组合优化",
    Scenario.GENERAL: "通用咨询",
}


class MessageRole(str, enum.Enum):
    USER = "user"
    ASSISTANT = "assistant"


class ChatSession(Base):
    """咨询会话:一个场景一个会话,消息经其挂载(BR-ADV-01 建议归属会话)。"""

    __tablename__ = "chat_sessions"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), ForeignKey("users.id"), nullable=False, index=True
    )
    scenario: Mapped[Scenario] = mapped_column(Enum(Scenario), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_active_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ChatMessage(Base):
    """会话消息:用户提问与助手答复;答复携带 advice_id 关联完整建议(溯源入口)。"""

    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    session_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), ForeignKey("chat_sessions.id"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), ForeignKey("users.id"), nullable=False
    )
    role: Mapped[MessageRole] = mapped_column(Enum(MessageRole), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    advice_id: Mapped[int | None] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), ForeignKey("advices.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

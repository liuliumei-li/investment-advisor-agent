"""advices / data_citations / agent_runs / compliance_audit_logs 建议聚合实体(US-06)。

BR-ADV-01 四要素落库(逻辑链条/数据来源/风险提示/仓位与收益预期);建议与引用、运行记录、
合规日志同事务提交(architecture.md §4.2、BR-DAT-04、BR-CMP-04)。
"""

import enum
from datetime import datetime

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.chat import Scenario


class ComplianceStatus(str, enum.Enum):
    """合规 gate 结果(BR-CMP-04)。"""

    PENDING = "pending"
    PASSED = "passed"
    REJECTED = "rejected"


class ComplianceAction(str, enum.Enum):
    """合规审核处理动作。"""

    PASS = "pass"
    REWRITE = "rewrite"
    REJECT = "reject"


class Advice(Base):
    """投顾建议(BR-ADV-01 四要素;logic_chain 为结论→依据→数据的逻辑链结构,US-21 逐级展开)。"""

    __tablename__ = "advices"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    session_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), ForeignKey("chat_sessions.id"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), ForeignKey("users.id"), nullable=False, index=True
    )
    scenario: Mapped[Scenario] = mapped_column(Enum(Scenario), nullable=False)
    conclusion: Mapped[str] = mapped_column(Text, nullable=False)
    logic_chain: Mapped[list | None] = mapped_column(JSON)
    risk_tips: Mapped[str] = mapped_column(Text, nullable=False)
    position_suggestion: Mapped[dict | None] = mapped_column(JSON)
    return_expectation: Mapped[dict | None] = mapped_column(JSON)
    divergence_summary: Mapped[dict | None] = mapped_column(JSON)
    compliance_status: Mapped[ComplianceStatus] = mapped_column(
        Enum(ComplianceStatus), nullable=False, default=ComplianceStatus.PENDING
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class DataCitation(Base):
    """数据引用溯源(BR-DAT-04):来源名称须在白名单内(BR-DAT-02),verified 为幻觉校验结果(BR-DAT-05)。"""

    __tablename__ = "data_citations"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    advice_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), ForeignKey("advices.id"), nullable=False, index=True
    )
    source_name: Mapped[str] = mapped_column(String(100), nullable=False)
    source_type: Mapped[str] = mapped_column(
        Enum("quote", "news", "research", "profile", name="sourcetype"), nullable=False
    )
    data_point: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    data_timestamp: Mapped[str] = mapped_column(String(40), nullable=False, default="")  # ISO 字符串(BR-DAT-03 时效)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AgentRun(Base):
    """智能体运行记录(US-19 协作过程可视化;US-06 单智能体链路,coordinator_trace 记录步骤与耗时)。"""

    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    advice_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), ForeignKey("advices.id"), nullable=False, unique=True
    )
    coordinator_trace: Mapped[list | None] = mapped_column(JSON)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ComplianceAuditLog(Base):
    """合规审核审计日志(BR-CMP-04):命中规则与处理动作留存可审计。"""

    __tablename__ = "compliance_audit_logs"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    advice_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), ForeignKey("advices.id"), nullable=False, index=True
    )
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    matched_rules: Mapped[list | None] = mapped_column(JSON)
    action: Mapped[ComplianceAction] = mapped_column(Enum(ComplianceAction), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

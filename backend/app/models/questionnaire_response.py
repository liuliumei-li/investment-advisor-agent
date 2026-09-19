"""questionnaire_responses 问卷作答表(architecture.md §4.2,US-01 AC-3:结果保存/关联/可再查)。"""

from datetime import datetime

from sqlalchemy import JSON, BigInteger, DateTime, Enum, ForeignKey, Integer, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.user_profile import RiskLevel


class QuestionnaireResponse(Base):
    __tablename__ = "questionnaire_responses"

    # SQLite 仅 INTEGER PRIMARY KEY 支持自增,故对 SQLite 降级为 Integer(PostgreSQL 仍为 BIGSERIAL)
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), ForeignKey("users.id"), nullable=False, index=True
    )
    questionnaire_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), ForeignKey("questionnaires.id"), nullable=False
    )
    answers: Mapped[list] = mapped_column(JSON, nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    risk_level: Mapped[RiskLevel] = mapped_column(Enum(RiskLevel), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

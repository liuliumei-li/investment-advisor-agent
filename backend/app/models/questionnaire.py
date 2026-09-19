"""questionnaires 问卷模板表(architecture.md §4.2,US-01)。"""

from datetime import datetime

from sqlalchemy import JSON, BigInteger, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Questionnaire(Base):
    """问卷模板:questions 为 JSON 数组(题目/选项/维度/分值),结构见 docs/api.md。"""

    __tablename__ = "questionnaires"

    # SQLite 仅 INTEGER PRIMARY KEY 支持自增,故对 SQLite 降级为 Integer(PostgreSQL 仍为 BIGSERIAL)
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    title: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    questions: Mapped[list] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

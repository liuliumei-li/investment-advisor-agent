from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """ORM 声明基类,全部模型继承自此(注册进 Base.metadata 供 Alembic 使用)。"""

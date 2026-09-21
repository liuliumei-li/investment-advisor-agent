"""chat_sessions / chat_messages 数据访问(单一实体组,无业务规则)。"""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat import ChatMessage, ChatSession


class ChatRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_session(self, session_row: ChatSession) -> ChatSession:
        self.session.add(session_row)
        await self.session.flush()
        return session_row

    async def get_session(self, session_id: int) -> ChatSession | None:
        result = await self.session.execute(select(ChatSession).where(ChatSession.id == session_id))
        return result.scalar_one_or_none()

    async def touch_session(self, session_row: ChatSession) -> None:
        """刷新会话活跃时间(US-20 上下文记忆基础)。"""
        session_row.last_active_at = datetime.now(timezone.utc)
        await self.session.flush()

    async def add_message(self, message: ChatMessage) -> ChatMessage:
        self.session.add(message)
        await self.session.flush()
        return message

    async def list_messages(self, session_id: int) -> list[ChatMessage]:
        result = await self.session.execute(
            select(ChatMessage).where(ChatMessage.session_id == session_id).order_by(ChatMessage.id)
        )
        return list(result.scalars().all())

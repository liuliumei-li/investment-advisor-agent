"""咨询会话服务(US-06):会话创建、消息收发(SSE 桥接)、历史消息、建议详情。

- 会话归属校验:非本人会话一律 40401;
- 场景开放度:US-06 仅开放大盘研判(market),其余场景随 US-07~11 逐步启用;
- 消息落库:用户消息与助手答复(携带 advice_id)同一事务,答复内容取研判 conclusion;
- 建议详情:BR-ADV-01 四要素 + 逻辑链 + 溯源引用(AC-3 逐级展开的数据基础,UI 属 US-21/22)。
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFound, ValidationFailed
from app.models.chat import SCENARIO_LABELS, ChatMessage, ChatSession, MessageRole, Scenario
from app.repositories.advice_repo import AdviceRepository
from app.repositories.chat_repo import ChatRepository
from app.services.industry_advisor_service import IndustryAdvisorService
from app.services.market_advisor_service import MarketAdvisorService

# 已开放场景(BR-ADV-03 六类场景随迭代开放;US-06 大盘研判、US-07 板块分析)
OPEN_SCENARIOS = {Scenario.MARKET, Scenario.INDUSTRY}


class ChatService:
    """咨询会话编排:校验 → 落消息 → 按场景分派 advisor → 回填助手消息(不碰 SQL,经仓储)。"""

    def __init__(
        self,
        chat_repo: ChatRepository,
        market_advisor: MarketAdvisorService,
        industry_advisor: IndustryAdvisorService | None,
        advice_repo: AdviceRepository,
        session: AsyncSession,
    ):
        self.chat_repo = chat_repo
        self.advice_repo = advice_repo
        self.session = session
        self.advisors: dict[Scenario, object] = {Scenario.MARKET: market_advisor}
        if industry_advisor is not None:
            self.advisors[Scenario.INDUSTRY] = industry_advisor

    async def create_session(self, user_id: int, scenario: Scenario) -> dict:
        if scenario not in OPEN_SCENARIOS:
            raise ValidationFailed(f"场景「{SCENARIO_LABELS[scenario]}」尚未开放(US-06 仅支持大盘研判)")
        session_row = await self.chat_repo.create_session(ChatSession(user_id=user_id, scenario=scenario))
        await self.session.commit()
        return {"session_id": session_row.id, "scenario": session_row.scenario.value}

    async def validate_session(self, user_id: int, session_id: int) -> ChatSession:
        """流开始前的前置校验(404/40001 直接以 HTTP 状态码返回,而非 SSE error 事件);返回会话行。"""
        return await self._owned_session(user_id, session_id)

    async def send_message(self, user_id: int, session_id: int, content: str, progress=None) -> dict:
        """发送咨询消息并生成建议(progress 为可选异步回调,SSE 逐段推送研判进度)。"""
        session_row = await self._owned_session(user_id, session_id)
        await self.chat_repo.add_message(
            ChatMessage(session_id=session_id, user_id=user_id, role=MessageRole.USER, content=content.strip())
        )
        advisor = self.advisors[session_row.scenario]
        result = await advisor.analyze(user_id, session_id, content, progress=progress)
        await self.chat_repo.add_message(
            ChatMessage(
                session_id=session_id,
                user_id=user_id,
                role=MessageRole.ASSISTANT,
                content=result["conclusion"],
                advice_id=result["advice_id"],
            )
        )
        await self.chat_repo.touch_session(session_row)
        await self.session.commit()
        return result

    async def list_messages(self, user_id: int, session_id: int) -> list[dict]:
        await self._owned_session(user_id, session_id)
        messages = await self.chat_repo.list_messages(session_id)
        return [
            {
                "id": message.id,
                "role": message.role.value,
                "content": message.content,
                "advice_id": message.advice_id,
                "created_at": message.created_at.isoformat() if message.created_at else None,
            }
            for message in messages
        ]

    async def _owned_session(self, user_id: int, session_id: int) -> ChatSession:
        """会话归属与场景开放度校验(非本人会话 40401,未开放场景 40001)。"""
        session_row = await self.chat_repo.get_session(session_id)
        if session_row is None or session_row.user_id != user_id:
            raise NotFound("会话不存在")
        if session_row.scenario not in OPEN_SCENARIOS:
            raise ValidationFailed(f"场景「{SCENARIO_LABELS[session_row.scenario]}」尚未开放")
        return session_row

    async def get_advice_detail(self, user_id: int, advice_id: int) -> dict:
        """建议详情(AC-1/AC-3):四要素 + 逻辑链 + 溯源引用 + 合规状态。"""
        advice = await self.advice_repo.get_advice(advice_id)
        if advice is None or advice.user_id != user_id:
            raise NotFound("建议不存在")
        citations = await self.advice_repo.list_citations(advice_id)
        return {
            "advice_id": advice.id,
            "scenario": advice.scenario.value,
            "conclusion": advice.conclusion,
            "logic_chain": advice.logic_chain or [],
            "risk_tips": advice.risk_tips,
            "position_suggestion": advice.position_suggestion,
            "return_expectation": advice.return_expectation,
            "compliance_status": advice.compliance_status.value,
            "citations": [
                {
                    "source_name": citation.source_name,
                    "source_type": citation.source_type,
                    "data_point": citation.data_point,
                    "source_url": citation.source_url,
                    "data_timestamp": citation.data_timestamp,
                    "verified": citation.verified,
                }
                for citation in citations
            ],
            "created_at": advice.created_at.isoformat() if advice.created_at else None,
        }

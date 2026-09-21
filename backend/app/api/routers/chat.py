"""咨询会话接口(US-06):会话创建、SSE 流式咨询、历史消息。

Controller 仅取参 → 调 Service → 返回;SSE 事件序列遵循 architecture.md §5.3:
meta(场景与智能体)→ delta(研判步骤进度)→ result(建议摘要)→ done(耗时);
中途业务异常以 error 事件输出(HTTP 4xx 仅用于流开始前的校验失败)。
"""

import asyncio
import json

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user_id, get_db
from app.core.exceptions import BizError
from app.core.response import ApiResponse
from app.datasource import get_market_data_service
from app.datasource.market_data import MarketDataService
from app.llm import LLMClient, get_llm_client
from app.models.chat import Scenario
from app.repositories.advice_repo import AdviceRepository
from app.repositories.chat_repo import ChatRepository
from app.repositories.profile_repo import ProfileRepository
from app.schemas.chat import ChatMessageRequest, ChatSessionCreateRequest
from app.services.chat_service import ChatService
from app.services.industry_advisor_service import IndustryAdvisorService
from app.services.market_advisor_service import MarketAdvisorService

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])


def get_chat_service(
    session: AsyncSession = Depends(get_db),
    llm: LLMClient = Depends(get_llm_client),
    market_data: MarketDataService = Depends(get_market_data_service),
) -> ChatService:
    profile_repo = ProfileRepository(session)
    advice_repo = AdviceRepository(session)
    market_advisor = MarketAdvisorService(
        llm=llm, market_data=market_data, profile_repo=profile_repo, advice_repo=advice_repo, session=session
    )
    industry_advisor = IndustryAdvisorService(
        llm=llm, market_data=market_data, profile_repo=profile_repo, advice_repo=advice_repo, session=session
    )
    return ChatService(ChatRepository(session), market_advisor, industry_advisor, advice_repo, session)


@router.post("/sessions")
async def create_chat_session(
    payload: ChatSessionCreateRequest,
    user_id: int = Depends(get_current_user_id),
    service: ChatService = Depends(get_chat_service),
):
    result = await service.create_session(user_id, Scenario(payload.scenario))
    return ApiResponse(data=result)


@router.get("/sessions/{session_id}/messages")
async def list_chat_messages(
    session_id: int,
    user_id: int = Depends(get_current_user_id),
    service: ChatService = Depends(get_chat_service),
):
    result = await service.list_messages(user_id, session_id)
    return ApiResponse(data={"items": result, "total": len(result)})


@router.post("/sessions/{session_id}/messages")
async def send_chat_message(
    payload: ChatMessageRequest,
    session_id: int,
    user_id: int = Depends(get_current_user_id),
    service: ChatService = Depends(get_chat_service),
):
    # 流开始前的前置校验:会话归属/场景开放度以 HTTP 状态码返回(40401/40001)
    session_row = await service.validate_session(user_id, session_id)
    agents = ["宏观研究"] if session_row.scenario is Scenario.MARKET else ["行业研究"]

    async def event_stream():
        queue: asyncio.Queue = asyncio.Queue()

        async def progress(step: dict) -> None:
            await queue.put(step)

        yield _sse("meta", {"session_id": session_id, "scenario": session_row.scenario.value, "agents": agents})
        task = asyncio.create_task(service.send_message(user_id, session_id, payload.content, progress))
        while not task.done():
            try:
                step = await asyncio.wait_for(queue.get(), timeout=0.05)
                yield _sse("delta", step)
            except asyncio.TimeoutError:
                continue
        while not queue.empty():
            yield _sse("delta", queue.get_nowait())
        try:
            result = task.result()
        except BizError as exc:
            yield _sse("error", {"code": exc.code, "message": exc.message})
            yield _sse("done", {"duration_ms": 0})
            return
        yield _sse(
            "result",
            {
                "advice_id": result["advice_id"],
                "conclusion": result["conclusion"],
                "risk_tips": result["risk_tips"],
                "compliance_status": result["compliance_status"],
                "degraded": result["degraded"],
                "citations_count": result["citations_count"],
            },
        )
        yield _sse("done", {"duration_ms": result["duration_ms"]})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

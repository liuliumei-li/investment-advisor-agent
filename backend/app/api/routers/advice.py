"""建议接口(US-06):建议详情(BR-ADV-01 四要素 + 逻辑链 + 溯源引用)。

Controller 仅取参 → 调 Service → 返回;逻辑链逐级展开的专用端点(US-21 /trace)后续迭代补充。
"""

from fastapi import APIRouter, Depends

from app.api.routers.chat import get_chat_service
from app.core.deps import get_current_user_id
from app.core.response import ApiResponse
from app.services.chat_service import ChatService

router = APIRouter(prefix="/api/v1/advice", tags=["advice"])


@router.get("/{advice_id}")
async def get_advice_detail(
    advice_id: int,
    user_id: int = Depends(get_current_user_id),
    service: ChatService = Depends(get_chat_service),
):
    result = await service.get_advice_detail(user_id, advice_id)
    return ApiResponse(data=result)

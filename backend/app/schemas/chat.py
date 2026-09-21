"""咨询会话请求 Schema(US-06)。"""

from typing import Literal

from pydantic import BaseModel, Field, field_validator

SCENARIO_VALUES = Literal["market", "industry", "stock", "etf", "cb", "portfolio", "general"]


class ChatSessionCreateRequest(BaseModel):
    """创建咨询会话:scenario 六类场景之一(US-06 仅开放 market)。"""

    scenario: SCENARIO_VALUES = "market"


class ChatMessageRequest(BaseModel):
    """发送咨询消息(SSE 流式):自然语言咨询内容。"""

    content: str = Field(min_length=1, max_length=2000)

    @field_validator("content")
    @classmethod
    def _strip_not_empty(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("消息内容不能为空")
        return stripped

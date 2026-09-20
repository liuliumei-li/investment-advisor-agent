"""LLM 适配层抽象(architecture.md §2.1,TC-02)。"""

from abc import ABC, abstractmethod


class LLMClient(ABC):
    """第三方大模型统一接口。实现须自带超时/重试并记录用量日志(AGENTS.md 规则 10)。"""

    @abstractmethod
    async def chat_json(
        self,
        messages: list[dict],
        *,
        temperature: float = 0.2,
        purpose: str = "general",
    ) -> dict:
        """非流式调用,返回解析后的 JSON 对象;失败抛 LLMServiceError(50004)。

        messages 为 OpenAI 兼容格式 [{"role": ..., "content": ...}];
        purpose 用于用量日志归类(docs/ai-usage-log.md)。
        """

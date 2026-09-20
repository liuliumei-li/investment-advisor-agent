"""app/llm:第三方大模型统一适配层(architecture.md §2.1,TC-02)。

业务层经 get_llm_client 注入 LLMClient,测试以 FakeLLM 替换(architecture.md §6.1)。
"""

from app.llm.base import LLMClient
from app.llm.deepseek_client import DeepSeekClient

__all__ = ["LLMClient", "DeepSeekClient", "get_llm_client"]

_client: LLMClient | None = None


def get_llm_client() -> LLMClient:
    """FastAPI 依赖工厂:进程内单例 DeepSeekClient。"""
    global _client
    if _client is None:
        _client = DeepSeekClient()
    return _client

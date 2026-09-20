"""DeepSeek 实现:OpenAI 兼容 /chat/completions,httpx 直调(architecture.md §2.1)。

超时/连接错误/5xx 线性退避重试,重试耗尽抛 LLMServiceError(50004);
每次调用记录用量日志(AGENTS.md 规则 10)。测试经 transport 注入 httpx.MockTransport。
"""

import asyncio
import json
import logging

import httpx

from app.core.config import settings
from app.core.exceptions import LLMServiceError
from app.llm.base import LLMClient

logger = logging.getLogger("app.llm")

# 可重试的服务端状态码(超时与连接错误在 _post 调用处另行捕获)
_RETRYABLE_STATUS = {500, 502, 503, 504}


class DeepSeekClient(LLMClient):
    """DeepSeek Chat 客户端,json_object 结构化输出。"""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.base_url = (base_url if base_url is not None else settings.llm_base_url).rstrip("/")
        self.api_key = api_key if api_key is not None else settings.llm_api_key
        self.model = model if model is not None else settings.llm_model
        self.timeout = timeout if timeout is not None else settings.llm_timeout_seconds
        self.max_retries = max_retries if max_retries is not None else settings.llm_max_retries
        self.transport = transport  # 单测注入 httpx.MockTransport(architecture.md §6.1)
        if not self.api_key:
            raise LLMServiceError("LLM API Key 未配置(环境变量 LLM_API_KEY)")

    async def chat_json(
        self,
        messages: list[dict],
        *,
        temperature: float = 0.2,
        purpose: str = "general",
    ) -> dict:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "response_format": {"type": "json_object"},
            "stream": False,
        }
        last_error = ""
        for attempt in range(1, self.max_retries + 1):
            try:
                response = await self._post(payload)
            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            else:
                if response.status_code in _RETRYABLE_STATUS:
                    last_error = f"HTTP {response.status_code}"
                else:
                    return self._handle(response, purpose)
            if attempt < self.max_retries:
                await asyncio.sleep(0.5 * attempt)  # 线性退避,避免拖垮整体 3 秒预算(BR-PER-02)
        raise LLMServiceError(f"LLM 调用失败({last_error},重试 {self.max_retries} 次耗尽)")

    async def _post(self, payload: dict) -> httpx.Response:
        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
            return await client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
            )

    def _handle(self, response: httpx.Response, purpose: str) -> dict:
        if response.status_code in (401, 403):
            raise LLMServiceError("LLM 鉴权失败,请检查 LLM_API_KEY")
        if response.status_code == 429:
            raise LLMServiceError("LLM 请求过于频繁(429)")
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise LLMServiceError(f"LLM 调用失败:HTTP {exc.response.status_code}") from exc
        try:
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            usage = data.get("usage")
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise LLMServiceError("LLM 返回结构异常") from exc
        parsed = self._parse_json_content(content)
        self._log_usage(purpose, usage)
        return parsed

    def _parse_json_content(self, content: str) -> dict:
        text = content.strip()
        if text.startswith("```"):  # 容错:剥离 ```json 代码块围栏
            text = text.strip("`")
            if text.startswith("json"):
                text = text[len("json") :]
            text = text.strip()
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMServiceError("LLM 返回内容不是合法 JSON") from exc
        if not isinstance(parsed, dict):
            raise LLMServiceError("LLM 返回 JSON 不是对象")
        return parsed

    def _log_usage(self, purpose: str, usage: dict | None) -> None:
        if not usage:
            return
        logger.info(
            "llm_usage model=%s purpose=%s prompt_tokens=%s completion_tokens=%s",
            self.model,
            purpose,
            usage.get("prompt_tokens"),
            usage.get("completion_tokens"),
        )

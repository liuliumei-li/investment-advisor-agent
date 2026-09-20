"""DeepSeekClient 单测:请求构造、JSON 解析、重试、错误码映射(httpx.MockTransport 注入)。"""

import json

import httpx
import pytest

from app.core.exceptions import LLMServiceError
from app.llm.deepseek_client import DeepSeekClient

MESSAGES = [{"role": "user", "content": "我能承受 20% 回撤,希望年化 15%"}]


def _client(handler, max_retries=2) -> DeepSeekClient:
    return DeepSeekClient(
        base_url="https://mock",
        api_key="test-key",
        model="deepseek-chat",
        timeout=1.0,
        max_retries=max_retries,
        transport=httpx.MockTransport(handler),
    )


def _ok_response() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": '{"risk_tolerance": "C4"}'}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        },
    )


class TestChatJsonSuccess:
    async def test_parses_json_content(self):
        client = _client(lambda req: _ok_response())
        result = await client.chat_json(MESSAGES, purpose="profile-dialog")
        assert result == {"risk_tolerance": "C4"}

    async def test_request_shape(self):
        captured = {}

        def handler(request: httpx.Request):
            captured["payload"] = json.loads(request.content)
            captured["auth"] = request.headers["Authorization"]
            return _ok_response()

        client = _client(handler)
        await client.chat_json(MESSAGES, temperature=0.1)
        assert captured["auth"] == "Bearer test-key"
        assert captured["payload"]["model"] == "deepseek-chat"
        assert captured["payload"]["response_format"] == {"type": "json_object"}
        assert captured["payload"]["stream"] is False
        assert captured["payload"]["temperature"] == 0.1

    async def test_fenced_json_is_tolerated(self):
        content = '```json\n{"a": 1}\n```'
        client = _client(lambda req: httpx.Response(200, json={"choices": [{"message": {"content": content}}]}))
        assert await client.chat_json(MESSAGES) == {"a": 1}


class TestRetry:
    async def test_retries_on_5xx_then_succeeds(self):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return _ok_response() if calls["n"] > 1 else httpx.Response(503)

        client = _client(handler)
        assert await client.chat_json(MESSAGES) == {"risk_tolerance": "C4"}
        assert calls["n"] == 2

    async def test_retries_on_timeout(self):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            if calls["n"] == 1:
                raise httpx.ReadTimeout("timeout")
            return _ok_response()

        client = _client(handler)
        assert await client.chat_json(MESSAGES) == {"risk_tolerance": "C4"}
        assert calls["n"] == 2

    async def test_raises_after_retries_exhausted(self):
        client = _client(lambda req: httpx.Response(500), max_retries=3)
        with pytest.raises(LLMServiceError) as exc_info:
            await client.chat_json(MESSAGES)
        assert exc_info.value.code == 50004


class TestErrorMapping:
    async def test_401_raises_without_retry(self):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return httpx.Response(401)

        client = _client(handler)
        with pytest.raises(LLMServiceError, match="鉴权"):
            await client.chat_json(MESSAGES)
        assert calls["n"] == 1

    async def test_429_raises(self):
        client = _client(lambda req: httpx.Response(429))
        with pytest.raises(LLMServiceError, match="429"):
            await client.chat_json(MESSAGES)

    async def test_non_json_content_raises(self):
        client = _client(lambda req: httpx.Response(200, json={"choices": [{"message": {"content": "好的,没问题"}}]}))
        with pytest.raises(LLMServiceError, match="JSON"):
            await client.chat_json(MESSAGES)

    async def test_missing_api_key_raises_at_init(self):
        with pytest.raises(LLMServiceError):
            DeepSeekClient(base_url="https://mock", api_key="", transport=httpx.MockTransport(lambda r: _ok_response()))


class TestUsageLogging:
    async def test_usage_logged(self, caplog):
        client = _client(lambda req: _ok_response())
        with caplog.at_level("INFO", logger="app.llm"):
            await client.chat_json(MESSAGES, purpose="profile-dialog")
        assert "purpose=profile-dialog" in caplog.text
        assert "prompt_tokens=10" in caplog.text

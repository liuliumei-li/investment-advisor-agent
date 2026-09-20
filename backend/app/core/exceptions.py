"""业务异常定义:由全局异常处理器统一转为错误码响应(architecture.md §5.1 错误码表)。"""

from typing import Any


class BizError(Exception):
    """业务异常基类:code 对应错误码表,http_status 对应 HTTP 状态码。"""

    code: int = 50001
    http_status: int = 500
    message: str = "服务内部错误"

    def __init__(self, message: str | None = None, detail: Any = None):
        self.message = message or self.message
        self.detail = detail
        super().__init__(self.message)


class ValidationFailed(BizError):
    code = 40001
    http_status = 400
    message = "参数校验失败"


class NotAuthenticated(BizError):
    code = 40101
    http_status = 401
    message = "未认证"


class TokenExpired(BizError):
    code = 40102
    http_status = 401
    message = "Token 过期"


class NotFound(BizError):
    code = 40401
    http_status = 404
    message = "资源不存在"


class TooManyRequests(BizError):
    code = 42901
    http_status = 429
    message = "请求过于频繁"


class LLMServiceError(BizError):
    code = 50004
    http_status = 500
    message = "LLM 服务不可用"

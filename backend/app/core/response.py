"""统一响应格式与 trace_id 中间件(architecture.md §5.1:{code, message, data, trace_id})。"""

import uuid
from contextvars import ContextVar
from typing import Any, Generic, TypeVar

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware

trace_id_var: ContextVar[str] = ContextVar("trace_id", default="-")


def get_trace_id() -> str:
    return trace_id_var.get()


class TraceIdMiddleware(BaseHTTPMiddleware):
    """每请求生成/透传 trace_id,贯穿日志与响应头。"""

    async def dispatch(self, request: Request, call_next):
        trace_id = request.headers.get("X-Trace-Id") or uuid.uuid4().hex[:16]
        token = trace_id_var.set(trace_id)
        try:
            response = await call_next(request)
        finally:
            trace_id_var.reset(token)
        response.headers["X-Trace-Id"] = trace_id
        return response


T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    """统一成功响应信封:Controller 直接 return,不处理响应细节。"""

    code: int = 0
    message: str = "ok"
    data: Any = None
    trace_id: str = Field(default_factory=get_trace_id)


def error_response(code: int, message: str, http_status: int) -> JSONResponse:
    """统一错误响应信封,与 ApiResponse 结构一致。"""
    return JSONResponse(
        status_code=http_status,
        content={"code": code, "message": message, "data": None, "trace_id": get_trace_id()},
    )

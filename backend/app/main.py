"""FastAPI 应用工厂与全局异常处理(统一响应信封,architecture.md §5.1)。"""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.routers import advice, auth, chat, profile
from app.core.config import settings
from app.core.exceptions import BizError
from app.core.response import TraceIdMiddleware, error_response


async def biz_error_handler(request: Request, exc: BizError) -> JSONResponse:
    return error_response(exc.code, exc.message, exc.http_status)


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return error_response(40001, "参数校验失败", 400)


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    # 仅将 404 映射到错误码表 40401,其余 HTTPException 维持框架默认行为
    if exc.status_code == 404:
        return error_response(40401, "资源不存在", 404)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    return error_response(50001, "服务内部错误", 500)


def create_app() -> FastAPI:
    app = FastAPI(title=settings.app_name, debug=settings.debug)
    app.add_middleware(TraceIdMiddleware)
    app.include_router(auth.router)
    app.include_router(profile.router)
    app.include_router(chat.router)
    app.include_router(advice.router)
    app.add_exception_handler(BizError, biz_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)
    return app


app = create_app()

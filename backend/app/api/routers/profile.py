"""画像接口:问卷获取/提交/作答查询、对话画像、持仓导入与分析(Controller 仅取参 → 调 Service → 返回)。"""

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.redis_client import Cache, get_cache
from app.core.deps import get_current_user_id, get_db
from app.core.response import ApiResponse
from app.llm import LLMClient, get_llm_client
from app.repositories.holding_repo import HoldingRepository
from app.repositories.profile_repo import ProfileRepository
from app.repositories.questionnaire_repo import QuestionnaireRepository
from app.schemas.holdings import HoldingsImportRequest
from app.schemas.profile import DialogMessageRequest, QuestionnaireSubmitRequest
from app.services.dialog_profile_service import DialogProfileService
from app.services.holdings_service import HoldingsService
from app.services.profile_service import ProfileService

router = APIRouter(prefix="/api/v1/profile", tags=["profile"])


def get_profile_service(
    session: AsyncSession = Depends(get_db),
    cache: Cache = Depends(get_cache),
) -> ProfileService:
    return ProfileService(QuestionnaireRepository(session), ProfileRepository(session), session, cache)


def get_dialog_profile_service(
    profile_service: ProfileService = Depends(get_profile_service),
    llm: LLMClient = Depends(get_llm_client),
    cache: Cache = Depends(get_cache),
) -> DialogProfileService:
    return DialogProfileService(profile_service, llm, cache)


def get_holdings_service(
    profile_service: ProfileService = Depends(get_profile_service),
    session: AsyncSession = Depends(get_db),
    llm: LLMClient = Depends(get_llm_client),
) -> HoldingsService:
    return HoldingsService(session, HoldingRepository(session), llm=llm, profile_service=profile_service)


@router.get("/questionnaires/latest")
async def get_latest_questionnaire(
    user_id: int = Depends(get_current_user_id),
    service: ProfileService = Depends(get_profile_service),
):
    questionnaire = await service.get_latest_questionnaire()
    return ApiResponse(data=questionnaire)


@router.post("/questionnaire")
async def submit_questionnaire(
    payload: QuestionnaireSubmitRequest,
    user_id: int = Depends(get_current_user_id),
    service: ProfileService = Depends(get_profile_service),
):
    result = await service.submit_questionnaire(
        user_id, payload.questionnaire_id, [answer.model_dump() for answer in payload.answers]
    )
    return ApiResponse(data=result)


@router.get("/questionnaire/latest-response")
async def get_latest_response(
    user_id: int = Depends(get_current_user_id),
    service: ProfileService = Depends(get_profile_service),
):
    result = await service.get_latest_response(user_id)
    return ApiResponse(data=result)


@router.post("/dialog")
async def dialog_message(
    payload: DialogMessageRequest,
    user_id: int = Depends(get_current_user_id),
    service: DialogProfileService = Depends(get_dialog_profile_service),
):
    result = await service.process_message(user_id, payload.session_id, payload.message, payload.finish)
    return ApiResponse(data=result)


@router.post("/import")
async def import_holdings(
    payload: HoldingsImportRequest,
    user_id: int = Depends(get_current_user_id),
    service: HoldingsService = Depends(get_holdings_service),
):
    if payload.mode == "list":
        result = await service.import_and_analyze(user_id, "list", [h.model_dump() for h in payload.holdings])
    else:
        result = await service.import_text_and_analyze(user_id, payload.text)
    return ApiResponse(data=result)


@router.post("/import/csv")
async def import_holdings_csv(
    file: UploadFile = File(...),
    user_id: int = Depends(get_current_user_id),
    service: HoldingsService = Depends(get_holdings_service),
):
    result = await service.import_csv_and_analyze(user_id, await file.read())
    return ApiResponse(data=result)

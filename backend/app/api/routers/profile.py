"""画像接口:问卷获取/提交/作答查询、对话画像(Controller 仅取参 → 调 Service → 返回)。"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.redis_client import Cache, get_cache
from app.core.deps import get_current_user_id, get_db
from app.core.response import ApiResponse
from app.llm import LLMClient, get_llm_client
from app.repositories.profile_repo import ProfileRepository
from app.repositories.questionnaire_repo import QuestionnaireRepository
from app.schemas.profile import DialogMessageRequest, QuestionnaireSubmitRequest
from app.services.dialog_profile_service import DialogProfileService
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

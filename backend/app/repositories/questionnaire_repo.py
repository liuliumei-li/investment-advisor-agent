"""问卷模板与作答记录数据访问(单一实体,无业务规则)。"""

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.questionnaire import Questionnaire
from app.models.questionnaire_response import QuestionnaireResponse


class QuestionnaireRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_latest(self) -> Questionnaire | None:
        """最新版本问卷(按 version 降序取首条)。"""
        result = await self.session.execute(
            select(Questionnaire).order_by(desc(Questionnaire.version)).limit(1)
        )
        return result.scalar_one_or_none()

    async def get_by_id(self, questionnaire_id: int) -> Questionnaire | None:
        return await self.session.get(Questionnaire, questionnaire_id)

    async def create_response(self, response: QuestionnaireResponse) -> QuestionnaireResponse:
        self.session.add(response)
        await self.session.flush()
        return response

    async def get_latest_response(self, user_id: int) -> QuestionnaireResponse | None:
        """该用户最近一次问卷作答(按 id 降序取首条)。"""
        result = await self.session.execute(
            select(QuestionnaireResponse)
            .where(QuestionnaireResponse.user_id == user_id)
            .order_by(desc(QuestionnaireResponse.id))
            .limit(1)
        )
        return result.scalar_one_or_none()

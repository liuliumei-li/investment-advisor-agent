"""画像接口请求 Schema。"""

from pydantic import BaseModel, Field


class QuestionnaireAnswer(BaseModel):
    question_id: str = Field(min_length=1, max_length=20)
    option_id: str = Field(min_length=1, max_length=20)


class QuestionnaireSubmitRequest(BaseModel):
    questionnaire_id: int
    answers: list[QuestionnaireAnswer] = Field(min_length=1)

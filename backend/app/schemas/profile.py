"""画像接口请求 Schema。"""

from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class QuestionnaireAnswer(BaseModel):
    question_id: str = Field(min_length=1, max_length=20)
    option_id: str = Field(min_length=1, max_length=20)


class QuestionnaireSubmitRequest(BaseModel):
    questionnaire_id: int
    answers: list[QuestionnaireAnswer] = Field(min_length=1)


class DialogMessageRequest(BaseModel):
    """对话画像消息(US-02):多轮对话;finish=True 时 message 可省略,按已有信息收口。"""

    session_id: str | None = Field(default=None, max_length=64, description="多轮会话 id,不传则新开会话")
    message: str = Field(default="", max_length=2000, description="本轮用户消息(finish=false 时必填)")
    finish: bool = False

    @model_validator(mode="after")
    def _message_required_unless_finish(self):
        if not self.finish and not self.message.strip():
            raise ValueError("finish=false 时 message 不能为空")
        return self


class ReturnExpectationValue(BaseModel):
    """收益预期修正值(US-04 AC-3):年化百分比区间,0~100 且 low ≤ high。"""

    low: float = Field(ge=0, le=100)
    high: float = Field(ge=0, le=100)

    @model_validator(mode="after")
    def _low_le_high(self):
        if self.low > self.high:
            raise ValueError("收益预期区间 low 不能大于 high")
        return self


class RiskLevelAmendment(BaseModel):
    field: Literal["risk_level"]
    value: Literal["C1", "C2", "C3", "C4", "C5"]


class ReturnExpectationAmendment(BaseModel):
    field: Literal["return_expectation"]
    value: ReturnExpectationValue


class HorizonAmendment(BaseModel):
    field: Literal["investment_horizon"]
    value: Literal["短期", "中期", "长期"]


class HabitAmendment(BaseModel):
    field: Literal["holding_habit_summary"]
    value: str = Field(min_length=1, max_length=500)

    @field_validator("value")
    @classmethod
    def _strip_not_empty(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("持仓习惯摘要不能为空")
        return stripped


ProfileAmendment = Annotated[
    RiskLevelAmendment | ReturnExpectationAmendment | HorizonAmendment | HabitAmendment,
    Field(discriminator="field"),
]


class ProfileUpdateRequest(BaseModel):
    """确认/修正画像(US-04 AC-3、BR-IMG-05):confirm 与 amendments 至少其一(服务层校验)。"""

    confirm: bool = False
    amendments: list[ProfileAmendment] = Field(default_factory=list, max_length=4)

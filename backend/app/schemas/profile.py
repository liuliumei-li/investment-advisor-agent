"""画像接口请求 Schema。"""

from pydantic import BaseModel, Field, model_validator


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

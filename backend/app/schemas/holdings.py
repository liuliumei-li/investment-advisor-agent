"""持仓导入请求模型(US-03 AC-1:清单粘贴 / CSV 文件 / 文本描述)。"""

from pydantic import BaseModel, Field, model_validator


class HoldingItem(BaseModel):
    """持仓清单条目(清单粘贴 / CSV / 文本抽取共用行结构)。

    数量与成本价用字符串承接,由服务端统一 Decimal 校验,保证错误提示为中文且带条目位置(AC-4)。
    """

    asset_type: str = Field(min_length=1, max_length=10)
    code: str | None = Field(default=None, max_length=20)
    name: str | None = Field(default=None, max_length=50)
    quantity: str = Field(min_length=1, max_length=30)
    cost_price: str = Field(min_length=1, max_length=30)


class HoldingsImportRequest(BaseModel):
    """POST /api/v1/profile/import 请求体:mode=list 清单粘贴 / mode=text 文本描述。"""

    mode: str = Field(pattern="^(list|text)$")
    holdings: list[HoldingItem] | None = None
    text: str | None = Field(default=None, max_length=5000)

    @model_validator(mode="after")
    def _check_payload(self):
        if self.mode == "list" and not self.holdings:
            raise ValueError("mode=list 时 holdings 不能为空")
        if self.mode == "text" and not (self.text and self.text.strip()):
            raise ValueError("mode=text 时 text 不能为空")
        return self

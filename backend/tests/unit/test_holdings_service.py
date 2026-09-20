"""持仓导入服务单测(US-03 Task 2):归一校验、CSV 解析、文本抽取、快照落库。"""

from decimal import Decimal

import pytest

from app.core.exceptions import ValidationFailed
from app.models.holding import AssetType
from app.repositories.holding_repo import HoldingRepository
from app.services.holdings_service import HoldingsService, normalize_holding_rows, parse_csv
from tests.helpers import FakeLLM


def make_service(db_session, llm=None):
    return HoldingsService(db_session, HoldingRepository(db_session), llm)


def _row(**overrides):
    row = {"asset_type": "stock", "code": "600519", "name": "贵州茅台", "quantity": "100", "cost_price": "1500"}
    row.update(overrides)
    return row


class TestNormalizeHoldingRows:
    async def test_normalize_valid_row_with_chinese_alias(self):
        rows = normalize_holding_rows([_row(asset_type="股票", code="", name="五粮液")], where="清单")
        assert rows[0]["asset_type"] is AssetType.STOCK
        assert rows[0]["code"] is None
        assert rows[0]["name"] == "五粮液"
        assert rows[0]["quantity"] == Decimal("100")
        assert rows[0]["cost_price"] == Decimal("1500")

    async def test_empty_rows_rejected(self):
        with pytest.raises(ValidationFailed, match="未识别到任何持仓"):
            normalize_holding_rows([], where="清单")

    async def test_unknown_asset_type_rejected(self):
        with pytest.raises(ValidationFailed, match="资产类别.*无法识别"):
            normalize_holding_rows([_row(asset_type="期货")], where="清单")

    async def test_quantity_and_price_must_be_positive(self):
        with pytest.raises(ValidationFailed, match="第 1 条.*数量.*必须大于 0"):
            normalize_holding_rows([_row(quantity="0")], where="清单")
        with pytest.raises(ValidationFailed, match="第 1 条.*成本价.*格式不正确"):
            normalize_holding_rows([_row(cost_price="abc")], where="清单")
        with pytest.raises(ValidationFailed, match="第 1 条缺少「数量」"):
            normalize_holding_rows([_row(quantity=None)], where="清单")

    async def test_code_and_name_required_at_least_one(self):
        with pytest.raises(ValidationFailed, match="缺少代码或名称"):
            normalize_holding_rows([_row(code="", name="")], where="清单")


class TestParseCsv:
    async def test_parse_english_headers_utf8_bom(self):
        data = "﻿asset_type,code,name,quantity,cost_price\nstock,600519,贵州茅台,100,1500\n".encode()
        rows = parse_csv(data)
        assert rows == [
            {"asset_type": "stock", "code": "600519", "name": "贵州茅台", "quantity": "100", "cost_price": "1500"}
        ]

    async def test_parse_chinese_headers_gbk(self):
        data = "资产类别,代码,名称,数量,成本价\n股票,510300,沪深300ETF,2000,3.9\n".encode("gbk")
        rows = parse_csv(data)
        assert rows[0]["asset_type"] == "股票"
        assert rows[0]["quantity"] == "2000"

    async def test_parse_missing_asset_type_column(self):
        with pytest.raises(ValidationFailed, match="缺少资产类别列"):
            parse_csv("code,name\n600519,贵州茅台\n".encode())

    async def test_parse_empty_file(self):
        with pytest.raises(ValidationFailed, match="CSV 文件为空"):
            parse_csv(b"")

    async def test_parse_unknown_encoding(self):
        with pytest.raises(ValidationFailed, match="编码无法识别"):
            parse_csv(b"\xff\xfe\x00\x01\x02")


class TestImportHoldings:
    async def test_import_list_creates_snapshot_and_rows(self, db_session):
        service = make_service(db_session)
        result = await service.import_holdings(1, "list", [_row(), _row(code="000858", name="五粮液")])
        assert result["source"] == "list"
        assert result["holding_count"] == 2

        repo = HoldingRepository(db_session)
        rows = await repo.get_latest_holdings(1)
        assert [r.name for r in rows] == ["贵州茅台", "五粮液"]
        assert rows[0].quantity == Decimal("100")

    async def test_invalid_row_does_not_persist_snapshot(self, db_session):
        service = make_service(db_session)
        with pytest.raises(ValidationFailed):
            await service.import_holdings(1, "list", [_row(quantity="0")])
        assert await HoldingRepository(db_session).latest_snapshots(1) == []

    async def test_error_message_contains_row_position(self, db_session):
        service = make_service(db_session)
        with pytest.raises(ValidationFailed, match="清单第 2 条"):
            await service.import_holdings(1, "list", [_row(), _row(asset_type="期货")])


class TestImportFromText:
    async def test_extract_and_import_via_fake_llm(self, db_session):
        fake = FakeLLM(
            responses=[
                {
                    "holdings": [
                        {
                            "asset_type": "stock",
                            "code": "600519",
                            "name": "贵州茅台",
                            "quantity": 100,
                            "cost_price": 1500,
                        }
                    ]
                }
            ]
        )
        service = make_service(db_session, llm=fake)
        result = await service.import_from_text(1, "我买了 100 股贵州茅台,成本 1500")
        assert result["source"] == "text"
        assert result["holding_count"] == 1
        rows = await HoldingRepository(db_session).get_latest_holdings(1)
        assert rows[0].code == "600519"
        assert fake.calls  # 确实走了 LLM

    async def test_no_holdings_extracted_raises(self, db_session):
        fake = FakeLLM(responses=[{"holdings": []}])
        with pytest.raises(ValidationFailed, match="未能从文本描述中识别到持仓信息"):
            await make_service(db_session, llm=fake).extract_from_text("今天天气不错")

    async def test_invalid_structure_raises(self, db_session):
        fake = FakeLLM(responses=[{"foo": 1}])
        with pytest.raises(ValidationFailed, match="抽取结果格式异常"):
            await make_service(db_session, llm=fake).extract_from_text("我买了茅台")

    async def test_missing_price_in_text_mode_hints_supplement(self, db_session):
        fake = FakeLLM(
            responses=[{"holdings": [{"asset_type": "stock", "name": "贵州茅台", "quantity": 100, "cost_price": None}]}]
        )
        with pytest.raises(ValidationFailed, match="文本描述第 1 条缺少「成本价」"):
            await make_service(db_session, llm=fake).import_from_text(1, "我买了 100 股贵州茅台")

    async def test_llm_unconfigured_raises_service_error(self, db_session):
        with pytest.raises(Exception, match="LLM 服务未配置"):
            await make_service(db_session, llm=None).import_from_text(1, "我买了茅台")

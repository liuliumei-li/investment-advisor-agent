"""幻觉检测 gate 单测(US-06 子任务 3,BR-DAT-05):引用校验与数字核验。"""

from app.services.hallucination_service import annotate_dubious, extract_numbers, validate_market_advice

PROVIDED = [
    {"ref": "来源1", "source_name": "新浪财经行情", "data_point": "上证指数(000001) 3949.91,涨跌 0.97%,今开 3914.50"},
    {"ref": "来源2", "source_name": "新浪财经快讯", "data_point": "央行开展逆回购操作"},
]

FIELDS = {
    "conclusion": "上证指数报 3949.91 点,涨 0.97%,市场情绪回暖。",
    "market_review": "今日大盘走高,成交量放大。",
    "key_factors": [{"title": "政策", "detail": "央行开展逆回购操作,流动性宽松。", "source_refs": ["来源2"]}],
    "logic_chain": [{"step": "1", "content": "指数上涨 0.97%。", "source_refs": ["来源1"]}],
    "risk_tips": "注意回调风险",
}


class TestExtractNumbers:
    def test_percent_and_decimal_numbers(self):
        assert "0.97%" in extract_numbers("涨 0.97%,报 3949.91 点")

    def test_thousand_separator_normalized(self):
        assert extract_numbers("3,949.91") == ["3949.91"]


class TestValidateMarketAdvice:
    def test_all_verified_no_issues(self):
        result = validate_market_advice(FIELDS, PROVIDED)
        assert result["issues"] == []
        assert result["verified_refs"] == ["来源1", "来源2"]
        assert result["rejected"] is False

    def test_unknown_reference_flagged(self):
        fields = {**FIELDS, "key_factors": [{"title": "x", "detail": "y", "source_refs": ["来源9"]}]}
        result = validate_market_advice(fields, PROVIDED)
        assert any(issue["type"] == "unknown_reference" for issue in result["issues"])

    def test_unverified_number_flagged(self):
        fields = {**FIELDS, "conclusion": "上证指数报 4000.00 点,市场走强。"}
        result = validate_market_advice(fields, PROVIDED)
        assert any(issue["type"] == "unverified_number" and "4000.00" in issue["detail"] for issue in result["issues"])
        assert result["rejected"] is False  # 仍有可核验引用,不拒答

    def test_non_whitelisted_source_flagged(self):
        provided = [{**PROVIDED[0], "source_name": "野生数据源"}]
        fields = {**FIELDS, "logic_chain": [{"step": "1", "content": "x", "source_refs": ["来源1"]}]}
        result = validate_market_advice(fields, provided)
        assert any(issue["type"] == "source_not_whitelisted" for issue in result["issues"])

    def test_all_refs_invalid_rejects(self):
        fields = {**FIELDS, "key_factors": [{"title": "x", "detail": "y", "source_refs": ["来源9"]}]}
        fields["logic_chain"] = [{"step": "1", "content": "x", "source_refs": ["来源8"]}]
        result = validate_market_advice(fields, PROVIDED)
        assert result["rejected"] is True


class TestAnnotateDubious:
    def test_number_annotated_in_conclusion(self):
        annotated = annotate_dubious({**FIELDS, "conclusion": "报 4000.00 点"}, ["4000.00"])
        assert "4000.00(数据存疑)" in annotated["conclusion"]

    def test_number_not_present_untouched(self):
        annotated = annotate_dubious(FIELDS, ["4000.00"])
        assert "(数据存疑)" not in annotated["conclusion"]

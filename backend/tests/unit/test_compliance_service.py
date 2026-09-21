"""合规审核 gate 单测(US-06 子任务 3,BR-CMP-01/02、BR-ADV-02):承诺表述过滤与免责声明注入。"""

from app.services.compliance_service import DISCLAIMER, MANDATORY_RISK_TIP, audit_advice

FIELDS = {
    "conclusion": "市场整体震荡上行,建议关注政策方向。",
    "market_review": "今日大盘小幅走高。",
    "key_factors": [],
    "risk_tips": "注意外部不确定性带来的回调风险。",
}


class TestAuditAdvice:
    def test_clean_fields_pass_with_disclaimer(self):
        result = audit_advice(FIELDS)
        assert result["passed"] is True
        assert result["action"] == "pass"
        assert result["compliance_status"] == "passed"
        assert result["fields"]["risk_tips"].endswith(DISCLAIMER)
        assert "BR-CMP-02 免责声明注入" in result["matched_rules"]

    def test_promise_word_rewritten(self):
        fields = {**FIELDS, "conclusion": "该品种保证收益,可放心买入。"}
        result = audit_advice(fields)
        assert result["action"] == "rewrite"
        assert "保证收益" not in result["fields"]["conclusion"]
        assert "已按合规要求移除" in result["fields"]["conclusion"]
        assert any("BR-ADV-02" in rule for rule in result["matched_rules"])

    def test_missing_risk_tips_injected(self):
        result = audit_advice({**FIELDS, "risk_tips": ""})
        assert MANDATORY_RISK_TIP in result["fields"]["risk_tips"]
        assert any("BR-CMP-01" in rule for rule in result["matched_rules"])

    def test_all_forbidden_words_detected(self):
        fields = {**FIELDS, "conclusion": "稳赚不赔,无风险,必涨!"}
        result = audit_advice(fields)
        hits = [rule for rule in result["matched_rules"] if "BR-ADV-02" in rule]
        assert len(hits) == 3

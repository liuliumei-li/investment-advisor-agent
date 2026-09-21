"""合规审核 gate(US-06 子任务 3,BR-CMP-01/02、BR-ADV-02;完整版随 US-29/30 增强)。

确定性规则(可测试):
1. 承诺性表述过滤(BR-ADV-02):命中「保证收益/稳赚/无风险」等禁用词 → 替换为合规提示;
2. 风险提示强制(BR-CMP-01):risk_tips 为空 → 注入强制风险提示;
3. 免责声明注入(BR-CMP-02):通过后统一追加「仅供参考,不构成投资建议」。

返回 audit 结果与改写后的文本,由编排层落 compliance_audit_logs(审计可查,BR-CMP-04)。
"""

# BR-ADV-02 收益承诺性表述禁用词表(扩展须同步 requirements.md)
FORBIDDEN_PROMISES = ["保证收益", "稳赚", "稳赚不赔", "无风险", "零风险", "保本", "必涨", "稳赢", "绝对安全"]

# BR-CMP-02 免责声明
DISCLAIMER = "本内容仅供参考,不构成投资建议;市场有风险,投资需谨慎。"

# BR-CMP-01 强制风险提示(原文本缺风险提示时注入)
MANDATORY_RISK_TIP = "投资有风险,市场波动可能导致本金损失,请根据自身风险承受能力审慎决策。"


def audit_advice(fields: dict) -> dict:
    """审核研判文本字段(conclusion/market_review/key_factors/risk_tips)。

    返回 {passed, action, matched_rules, fields(改写后), compliance_status}。
    """
    matched_rules: list[str] = []
    rewritten = {**fields}
    action = "pass"

    for key in ("conclusion", "market_review"):
        text = rewritten.get(key) or ""
        matched = [word for word in FORBIDDEN_PROMISES if word in text]
        # 短词被长词包含时只计长词(如「稳赚」⊂「稳赚不赔」,避免重复命中与二次替换)
        matched = [word for word in matched if not any(word != other and word in other for other in matched)]
        for word in matched:
            matched_rules.append(f"BR-ADV-02 收益承诺表述「{word}」")
            rewritten[key] = rewritten[key].replace(word, "(该表述涉及收益承诺,已按合规要求移除)")

    if not (rewritten.get("risk_tips") or "").strip():
        matched_rules.append("BR-CMP-01 风险提示缺失")
        rewritten["risk_tips"] = MANDATORY_RISK_TIP

    if matched_rules:
        action = "rewrite"
    rewritten["risk_tips"] = (
        f"{rewritten['risk_tips'].rstrip()}\n{DISCLAIMER}" if rewritten["risk_tips"] else DISCLAIMER
    )
    matched_rules.append("BR-CMP-02 免责声明注入")
    return {
        "passed": action == "pass",
        "action": action,
        "matched_rules": matched_rules,
        "fields": rewritten,
        # 改写后内容即合规(可通过);reject 仅由后续 US-29 不可改写场景使用
        "compliance_status": "rejected" if action == "reject" else "passed",
    }

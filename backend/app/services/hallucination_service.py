"""幻觉检测 gate(US-06 子任务 3,BR-DAT-05;完整版随 US-25 增强)。

确定性规则(可测试,不依赖 LLM 自检):
1. 引用校验:LLM 输出中的来源引用编号(source_refs)必须存在于本次提供的数据清单,且来源名在白名单内(BR-DAT-02);
2. 数字校验:结论/解读/影响因素文本中的数字(点位、涨跌幅、百分比)必须能在提供的数据文本中找到;
   无法核验的数字标注「数据存疑」,全部关键数字无法核验时拒答(关键数据无法校验不得输出结论)。
"""

import re

from app.datasource.base import SOURCE_WHITELIST

# 数字模式:百分比(12.3%)、千分位数字(3,949.91)、四位数以上点位(4000.00 / 3949.91)
_NUMBER_PATTERN = re.compile(
    r"\d+(?:,\d{3})+(?:\.\d+)?\s*%?|\d{4,}(?:\.\d+)?\s*%?|\d+(?:\.\d+)?\s*%"
)


def extract_numbers(text: str) -> list[str]:
    """提取文本中的数字断言(归一化:去千分位逗号、去空格)。"""
    return [match.replace(",", "").replace(" ", "") for match in _NUMBER_PATTERN.findall(text)]


def validate_market_advice(fields: dict, provided_points: list[dict]) -> dict:
    """校验 LLM 研判草稿(fields: conclusion/market_review/key_factors/risk_tips)。

    provided_points: [{ref, source_name, data_point}]——提供数据清单(带引用编号)。

    返回 {issues: [...], verified_refs: [...], rejected: bool}。
    """
    issues: list[dict] = []
    ref_to_point = {point["ref"]: point for point in provided_points}

    # 1. 引用校验:引用编号必须存在,来源名必须白名单内
    referenced = _collect_refs(fields)
    unknown_refs = sorted(ref for ref in referenced if ref not in ref_to_point)
    if unknown_refs:
        issues.append({"type": "unknown_reference", "detail": f"引用了不存在的数据来源:{','.join(unknown_refs)}"})
    for ref in referenced:
        point = ref_to_point.get(ref)
        if point and point.get("source_name") not in SOURCE_WHITELIST:
            issues.append({"type": "source_not_whitelisted", "detail": f"来源不在白名单:{point['source_name']}"})

    # 2. 数字校验:所有数字断言必须能在提供数据中找到,否则存疑
    provided_text = " ".join(point["data_point"] for point in provided_points)
    claimed_numbers = set()
    for text in [fields.get("conclusion", ""), fields.get("market_review", "")] + [
        factor.get("detail", "") for factor in fields.get("key_factors", [])
    ]:
        claimed_numbers.update(extract_numbers(text))
    unverified = sorted(number for number in claimed_numbers if number not in provided_text)
    if unverified:
        issues.append({"type": "unverified_number", "detail": "以下数字无法在提供数据中核验:" + ",".join(unverified)})

    verified_refs = sorted(ref for ref in referenced if ref in ref_to_point)
    rejected = bool(issues and referenced and not verified_refs)
    return {"issues": issues, "verified_refs": verified_refs, "rejected": rejected}


def _collect_refs(fields: dict) -> set[str]:
    """收集草稿中全部来源引用编号(来源1/来源2 形式)。"""
    refs: set[str] = set()
    for factor in fields.get("key_factors", []):
        refs.update(str(ref) for ref in factor.get("source_refs", []))
    for step in fields.get("logic_chain", []):
        refs.update(str(ref) for ref in step.get("source_refs", []))
    return refs


def annotate_dubious(fields: dict, unverified_numbers: list[str]) -> dict:
    """无法核验的数字标注「(数据存疑)」(BR-DAT-05 纠正或标注)。"""
    annotated = {**fields}
    for number in unverified_numbers:
        for key in ("conclusion", "market_review"):
            if number in (annotated.get(key) or ""):
                annotated[key] = annotated[key].replace(number, f"{number}(数据存疑)", 1)
    return annotated

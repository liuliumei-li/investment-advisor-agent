"""BR-IMG-07 问卷评分与风险等级映射规则。

纯函数实现,便于单测;权重与阈值单点定义于此,调整时同步更新 docs/requirements.md §6.1 BR-IMG-07。
"""

from app.models.user_profile import RiskLevel

# AC-1 四维度权重,合计 1.0
DIMENSION_WEIGHTS: dict[str, float] = {
    "risk_tolerance": 0.40,
    "return_expectation": 0.20,
    "investment_horizon": 0.20,
    "investment_experience": 0.20,
}

# 总分(0~100)→ 风险等级显式映射:自上而下取首个命中的档位,否则 C1
RISK_LEVEL_THRESHOLDS: list[tuple[int, RiskLevel]] = [
    (80, RiskLevel.C5),
    (60, RiskLevel.C4),
    (45, RiskLevel.C3),
    (30, RiskLevel.C2),
]

OPTION_SCORE_RANGE = (1, 5)


def dimension_score(option_scores: list[int]) -> int:
    """单维度得分归一化到 0~100:选项分值(1~5)之和 / 满分 * 100,四舍五入。"""
    max_total = OPTION_SCORE_RANGE[1] * len(option_scores)
    return round(sum(option_scores) / max_total * 100)


def total_score(dimension_scores: dict[str, int]) -> int:
    """按维度权重合成总分(0~100)。"""
    return round(sum(DIMENSION_WEIGHTS[dim] * score for dim, score in dimension_scores.items()))


def score_to_risk_level(score: int) -> RiskLevel:
    """总分 → 五档风险等级(BR-IMG-01)。"""
    for threshold, level in RISK_LEVEL_THRESHOLDS:
        if score >= threshold:
            return level
    return RiskLevel.C1


def evaluate_answers(questions: list[dict], answers: list[dict]) -> tuple[dict[str, int], int, RiskLevel]:
    """按问卷定义计算维度分、总分与风险等级(要求 answers 已通过完整性校验)。"""
    question_by_id = {q["id"]: q for q in questions}
    scores_by_dimension: dict[str, list[int]] = {}
    for answer in answers:
        question = question_by_id[answer["question_id"]]
        option = next(o for o in question["options"] if o["id"] == answer["option_id"])
        scores_by_dimension.setdefault(question["dimension"], []).append(option["score"])
    dims = {dim: dimension_score(scores) for dim, scores in scores_by_dimension.items()}
    total = total_score(dims)
    return dims, total, score_to_risk_level(total)


def extract_profile_fields(questions: list[dict], answers: list[dict]) -> dict:
    """从作答中提取画像要素:收益预期区间与投资期限(选项携带 profile 元数据)。"""
    answer_by_qid = {a["question_id"]: a["option_id"] for a in answers}
    fields: dict[str, int | str | None] = {
        "return_expectation_low": None,
        "return_expectation_high": None,
        "investment_horizon": None,
    }
    for question in questions:
        field = question.get("profile_field")
        if field == "return_expectation":
            option = _answer_option(question, answer_by_qid)
            fields["return_expectation_low"] = option.get("return_low")
            fields["return_expectation_high"] = option.get("return_high")
        elif field == "investment_horizon":
            option = _answer_option(question, answer_by_qid)
            fields["investment_horizon"] = option.get("horizon")
    return fields


def _answer_option(question: dict, answer_by_qid: dict) -> dict:
    option_id = answer_by_qid[question["id"]]
    return next(o for o in question["options"] if o["id"] == option_id)

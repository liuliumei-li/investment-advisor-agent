"""risk_scoring 纯函数单测(BR-IMG-07:维度分/总分/风险等级映射、画像要素提取)。"""

import pytest

from app.data.questionnaire_v1 import QUESTIONNAIRE_V1
from app.models.user_profile import RiskLevel
from app.services.risk_scoring import (
    DIMENSION_WEIGHTS,
    dimension_score,
    evaluate_answers,
    extract_profile_fields,
    score_to_risk_level,
    total_score,
)


class TestDimensionScore:
    def test_single_option_scales_to_100(self):
        assert dimension_score([5]) == 100
        assert dimension_score([3]) == 60
        assert dimension_score([1]) == 20

    def test_multi_option_average(self):
        # 5 题全 3 分:15 / 25 * 100 = 60
        assert dimension_score([3, 3, 3, 3, 3]) == 60

    def test_rounding(self):
        # [1, 1, 2]:4 / 15 * 100 = 26.67 → 27
        assert dimension_score([1, 1, 2]) == 27

    def test_mixed_scores(self):
        # [2, 3]:5 / 10 * 100 = 50
        assert dimension_score([2, 3]) == 50


class TestTotalScore:
    def test_weights_sum_to_one(self):
        assert sum(DIMENSION_WEIGHTS.values()) == 1.0

    def test_all_dimensions_full(self):
        dims = {
            "risk_tolerance": 100,
            "return_expectation": 100,
            "investment_horizon": 100,
            "investment_experience": 100,
        }
        assert total_score(dims) == 100

    def test_weighted_combination(self):
        # 40 + 10 + 10 + 10 = 70
        dims = {
            "risk_tolerance": 100,
            "return_expectation": 50,
            "investment_horizon": 50,
            "investment_experience": 50,
        }
        assert total_score(dims) == 70

    def test_risk_tolerance_only(self):
        # 0.4 * 100 = 40
        dims = {
            "risk_tolerance": 100,
            "return_expectation": 0,
            "investment_horizon": 0,
            "investment_experience": 0,
        }
        assert total_score(dims) == 40


class TestScoreToRiskLevel:
    @pytest.mark.parametrize(
        ("score", "expected"),
        [
            (100, RiskLevel.C5),
            (80, RiskLevel.C5),  # 阈值 ≥80 命中 C5
            (79, RiskLevel.C4),
            (60, RiskLevel.C4),  # 阈值 ≥60 命中 C4
            (59, RiskLevel.C3),
            (45, RiskLevel.C3),  # 阈值 ≥45 命中 C3
            (44, RiskLevel.C2),
            (30, RiskLevel.C2),  # 阈值 ≥30 命中 C2
            (29, RiskLevel.C1),
            (0, RiskLevel.C1),  # 兜底 C1
        ],
    )
    def test_threshold_mapping(self, score, expected):
        assert score_to_risk_level(score) is expected


class TestEvaluateAnswers:
    def test_all_lowest_options_yield_c1(self):
        answers = [
            {"question_id": q["id"], "option_id": "a"} for q in QUESTIONNAIRE_V1["questions"]
        ]
        dims, total, level = evaluate_answers(QUESTIONNAIRE_V1["questions"], answers)
        # 各维度均 1 分:风险承受 5 题 20 分,其余 3 题 20 分 → 总分 20 → C1
        assert dims == {
            "risk_tolerance": 20,
            "return_expectation": 20,
            "investment_horizon": 20,
            "investment_experience": 20,
        }
        assert total == 20
        assert level is RiskLevel.C1

    def test_all_highest_options_yield_c5(self):
        answers = [
            {"question_id": q["id"], "option_id": "e"} for q in QUESTIONNAIRE_V1["questions"]
        ]
        dims, total, level = evaluate_answers(QUESTIONNAIRE_V1["questions"], answers)
        assert total == 100
        assert level is RiskLevel.C5

    def test_middle_options_yield_c4(self):
        answers = [
            {"question_id": q["id"], "option_id": "c"} for q in QUESTIONNAIRE_V1["questions"]
        ]
        dims, total, level = evaluate_answers(QUESTIONNAIRE_V1["questions"], answers)
        assert dims == {
            "risk_tolerance": 60,
            "return_expectation": 60,
            "investment_horizon": 60,
            "investment_experience": 60,
        }
        assert total == 60
        assert level is RiskLevel.C4

    def test_mixed_dimensions(self):
        # 风险承受全 e(100 分),其余全 a(20 分):40 + 12 = 52 → C3
        answers = [
            {
                "question_id": q["id"],
                "option_id": "e" if q["dimension"] == "risk_tolerance" else "a",
            }
            for q in QUESTIONNAIRE_V1["questions"]
        ]
        dims, total, level = evaluate_answers(QUESTIONNAIRE_V1["questions"], answers)
        assert total == 52
        assert level is RiskLevel.C3


class TestExtractProfileFields:
    @staticmethod
    def _answers(**overrides):
        """完整作答,并按题目 id 覆盖指定选项(服务路径先经完整性校验,契约要求全量作答)。"""
        return [
            {"question_id": q["id"], "option_id": overrides.get(q["id"], "c")}
            for q in QUESTIONNAIRE_V1["questions"]
        ]

    def test_return_expectation_range(self):
        answers = self._answers(re1="d")
        fields = extract_profile_fields(QUESTIONNAIRE_V1["questions"], answers)
        assert fields["return_expectation_low"] == 10
        assert fields["return_expectation_high"] == 20

    def test_return_expectation_open_ended_high(self):
        answers = self._answers(re1="e")
        fields = extract_profile_fields(QUESTIONNAIRE_V1["questions"], answers)
        assert fields["return_expectation_low"] == 20
        assert fields["return_expectation_high"] is None

    def test_investment_horizon(self):
        fields = extract_profile_fields(QUESTIONNAIRE_V1["questions"], self._answers(ih1="c"))
        assert fields["investment_horizon"] == "中期"

    def test_investment_horizon_short_term(self):
        fields = extract_profile_fields(QUESTIONNAIRE_V1["questions"], self._answers(ih1="a"))
        assert fields["investment_horizon"] == "短期"

    def test_default_answers_profile_fields(self):
        # 默认全 c:re1=c → 6%~10%,ih1=c → 中期
        fields = extract_profile_fields(QUESTIONNAIRE_V1["questions"], self._answers())
        assert fields == {
            "return_expectation_low": 6,
            "return_expectation_high": 10,
            "investment_horizon": "中期",
        }

"""画像更新历史构建纯函数单测(US-05 AC-3):changes 只记实际变化,冲突主张另列。"""

from app.services.profile_history import build_update_event


class TestBuildUpdateEvent:
    def test_filters_out_unchanged_updates(self):
        event = build_update_event(
            1,
            2,
            "对话更新",
            [
                {"field": "risk_level", "before": "C4", "after": "C4", "source": "对话", "quote": "q1"},
                {"field": "investment_horizon", "before": "中期", "after": "长期", "source": "对话", "quote": "q2"},
            ],
        )
        assert event["user_id"] == 1
        assert event["version"] == 2
        assert event["trigger"] == "对话更新"
        assert event["changes"] == [
            {"field": "investment_horizon", "before": "中期", "after": "长期", "source": "对话", "quote": "q2"}
        ]

    def test_new_profile_from_none_before_recorded(self):
        event = build_update_event(
            1, 1, "问卷测评", [{"field": "risk_level", "before": None, "after": "C4", "source": "问卷"}]
        )
        assert event["changes"] == [
            {"field": "risk_level", "before": None, "after": "C4", "source": "问卷", "quote": None}
        ]

    def test_conflicts_recorded_as_current_proposed(self):
        event = build_update_event(
            1,
            2,
            "对话更新",
            [{"field": "risk_level", "before": None, "after": "C3", "source": "对话", "quote": "a"}],
            [{"field": "risk_level", "before": "C4", "after": "C1", "source": "对话", "quote": "b"}],
        )
        assert event["conflicts"] == [
            {"field": "risk_level", "current": "C4", "proposed": "C1", "source": "对话", "quote": "b"}
        ]

    def test_no_conflicts_and_no_changes(self):
        event = build_update_event(1, 2, "用户确认", [])
        assert event["changes"] == []
        assert event["conflicts"] == []

    def test_complex_before_after_lists_preserved(self):
        event = build_update_event(
            1,
            2,
            "用户修正",
            [{"field": "return_expectation", "before": [6.0, 10.0], "after": [5.0, 9.0], "source": "用户修正"}],
        )
        assert event["changes"][0]["before"] == [6.0, 10.0]
        assert event["changes"][0]["after"] == [5.0, 9.0]

"""画像更新历史构建(US-05 AC-3、BR-IMG-06):触发标签与事件内容纯函数。

事件内容只存 JSON 原生类型(Decimal/datetime 由调用方转换);changes 仅记录实际变化的要素
(after != before),冲突主张另列 conflicts。画像更新事件落库于 profile_update_events,
与 user_profiles 同事务提交,版本号对应画像更新后的 version。
"""

# 触发来源标签(与 profile_update_events.trigger 对应)
TRIGGER_QUESTIONNAIRE = "问卷测评"
TRIGGER_DIALOG = "对话更新"
TRIGGER_HOLDINGS = "持仓更新"
TRIGGER_USER_CORRECTION = "用户修正"
TRIGGER_CONFIRMATION = "用户确认"


def build_update_event(
    user_id: int, version: int, trigger: str, updates: list[dict], conflicts: list[dict] | None = None
) -> dict:
    """构建事件内容:updates 为已采纳项 [{field, before, after, source?, quote?}],
    conflicts 为保留原值的冲突主张 [{field, before, after, source?, quote?}]。

    仅实际变化的要素进入 changes;before/after 必须是 JSON 原生类型。
    """
    changes = [
        {
            "field": update["field"],
            "before": update["before"],
            "after": update["after"],
            "source": update.get("source"),
            "quote": update.get("quote"),
        }
        for update in updates
        if update["after"] != update["before"]
    ]
    conflict_entries = [
        {
            "field": item["field"],
            "current": item["before"],
            "proposed": item["after"],
            "source": item.get("source"),
            "quote": item.get("quote"),
        }
        for item in (conflicts or [])
    ]
    return {
        "user_id": user_id,
        "version": version,
        "trigger": trigger,
        "changes": changes,
        "conflicts": conflict_entries,
    }

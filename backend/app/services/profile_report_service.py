"""画像报告服务(US-04):报告生成(含溯源)、当前画像读取、确认/修正(BR-IMG-05)。

- 报告为读模型:横跨画像与持仓两个聚合,维度构建纯函数在 profile_report;
- 当前画像走 profile:{user_id} 热缓存 read-through(10 分钟 TTL,画像更新即失效);
- 确认/修正:修正立即生效并写"用户修正"溯源、清除该字段待确认冲突;确认置 confirmed 并清空冲突。
"""

from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.redis_client import PROFILE_CACHE_TTL_SECONDS, Cache, profile_cache_key
from app.core.exceptions import NotFound, ValidationFailed
from app.models.user_profile import RiskLevel
from app.repositories.holding_repo import HoldingRepository
from app.repositories.profile_repo import ProfileRepository
from app.services.holdings_analysis import analyze_holdings
from app.services.profile_report import (
    TRACE_SOURCE_USER_CORRECTION,
    build_dimensions,
    build_trace_entry,
    clear_conflicts,
    ensure_trace_structure,
    incomplete_sources,
    remove_conflict,
    serialize_profile_compact,
    set_element_trace,
)

# 允许用户修正的画像要素(AC-3:确认画像或修正个别要素)
AMENDABLE_FIELDS = {"risk_level", "return_expectation", "investment_horizon", "holding_habit_summary"}


def _apply_amendment(profile, field: str, value) -> tuple | None:
    """应用单个修正(调用前字段名与值形态已经 Schema/服务校验)。

    无实际变化返回 None(不增版本、不重写溯源);有变化返回 (before, after)。
    """
    if field == "risk_level":
        before = profile.risk_level.value
        if value == before:
            return None
        profile.risk_level = RiskLevel(value)
        return before, value
    if field == "return_expectation":
        before = (
            [float(profile.return_expectation_low), float(profile.return_expectation_high)]
            if profile.return_expectation_low is not None
            else None
        )
        low, high = float(value["low"]), float(value["high"])
        if before is not None and abs(before[0] - low) < 0.01 and abs(before[1] - high) < 0.01:
            return None
        profile.return_expectation_low = Decimal(str(low))
        profile.return_expectation_high = Decimal(str(high))
        return before, [low, high]
    if field == "investment_horizon":
        before = profile.investment_horizon
        if value == before:
            return None
        profile.investment_horizon = value
        return before, value
    # holding_habit_summary
    before = profile.holding_habit_summary
    if value == before:
        return None
    profile.holding_habit_summary = value
    return before, value


class ProfileReportService:
    """画像报告编排:查画像/快照 → 构建报告维度;确认/修正落库(不碰 SQL,经 Repository)。"""

    def __init__(
        self,
        profile_repo: ProfileRepository,
        holding_repo: HoldingRepository,
        session: AsyncSession,
        cache: Cache,
    ):
        self.profile_repo = profile_repo
        self.holding_repo = holding_repo
        self.session = session
        self.cache = cache

    async def get_report(self, user_id: int) -> dict:
        """画像报告(AC-1/AC-2/AC-4):四要素维度 + 雷达分数 + 逐要素溯源 + 待确认冲突。"""
        profile = await self.profile_repo.get_by_user_id(user_id)
        if profile is None:
            raise NotFound("画像不存在,请先完成问卷/对话/持仓导入建立画像")
        holdings_info = await self._holdings_info(user_id)
        dimensions = build_dimensions(profile, profile.source_trace, holdings_info)
        trace = ensure_trace_structure(profile.source_trace)
        return {
            "profile_id": profile.id,
            "version": profile.version,
            "confirmed": profile.confirmed,
            "confidence": float(profile.confidence) if profile.confidence is not None else None,
            "source_mix": profile.source_mix,
            "incomplete_sources": incomplete_sources(profile.source_mix),
            "updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
            "dimensions": dimensions,
            "conflicts": trace["conflicts"],
        }

    async def get_current(self, user_id: int) -> dict:
        """当前画像紧凑视图(AC-4):profile:{user_id} 缓存 read-through。"""
        key = profile_cache_key(user_id)
        cached = await self.cache.get_json(key)
        if cached is not None:
            return cached
        profile = await self.profile_repo.get_by_user_id(user_id)
        if profile is None:
            raise NotFound("画像不存在,请先完成问卷/对话/持仓导入建立画像")
        view = serialize_profile_compact(profile)
        await self.cache.set_json(key, view, ttl=PROFILE_CACHE_TTL_SECONDS)
        return view

    async def confirm_or_amend(self, user_id: int, confirm: bool, amendments: list[dict]) -> dict:
        """确认画像或修正个别要素(AC-3、BR-IMG-05)。

        - 修正立即生效,写"用户修正"溯源并清除该字段待确认冲突;确认置 confirmed 并清空冲突;
        - 单次请求最多递增一次版本;修正同值或重复确认不递增、不重写溯源;
        - 修正不改变 source_mix/confidence(用户修正不是证据来源)。
        """
        profile = await self.profile_repo.get_by_user_id(user_id)
        if profile is None:
            raise NotFound("画像不存在,请先完成问卷/对话/持仓导入建立画像")
        if not confirm and not amendments:
            raise ValidationFailed("confirm 与 amendments 至少提供其一")
        fields = [a["field"] for a in amendments]
        if len(fields) != len(set(fields)):
            raise ValidationFailed("amendments 中存在重复字段")
        unknown = [f for f in fields if f not in AMENDABLE_FIELDS]
        if unknown:
            raise ValidationFailed(f"不可修正的画像字段:{unknown[0]}")

        applied: list[dict] = []
        for amendment in amendments:
            change = _apply_amendment(profile, amendment["field"], amendment["value"])
            if change is not None:
                before, after = change
                applied.append({"field": amendment["field"], "before": before, "after": after})
        confirmed_now = confirm and not profile.confirmed
        if not applied and not confirmed_now:
            # 无实际变化:不落库不增版本,返回当前状态
            return self._update_result(profile, applied)

        profile.version += 1  # BR-IMG-06:画像更新以版本递增留痕
        trace = ensure_trace_structure(profile.source_trace)
        for change in applied:
            field = change["field"]
            entry = build_trace_entry(TRACE_SOURCE_USER_CORRECTION, None, profile.version)
            trace = set_element_trace(trace, field, entry)
            trace = remove_conflict(trace, field)
        if confirm:
            profile.confirmed = True
            trace = clear_conflicts(trace)
        profile.source_trace = trace
        await self.profile_repo.save(profile)
        await self.session.commit()
        await self.cache.delete(profile_cache_key(user_id))
        await self.session.refresh(profile)  # 读取 server_default 时间戳
        return self._update_result(profile, applied)

    async def _holdings_info(self, user_id: int) -> dict | None:
        """最新快照的持仓分散度(报告持仓习惯轴分数来源)与快照时间。"""
        snapshots = await self.holding_repo.latest_snapshots(user_id, limit=1)
        if not snapshots:
            return None
        holdings = await self.holding_repo.get_holdings_by_snapshot(snapshots[0].id)
        if not holdings:
            return None
        rows = [
            {
                "asset_type": h.asset_type,
                "code": h.code,
                "name": h.name,
                "quantity": h.quantity,
                "cost_price": h.cost_price,
            }
            for h in holdings
        ]
        analysis = analyze_holdings(rows)
        return {
            "top3_share": analysis["concentration"]["top3_share"],
            "updated_at": snapshots[0].created_at.isoformat() if snapshots[0].created_at else None,
        }

    @staticmethod
    def _update_result(profile, applied: list[dict]) -> dict:
        result = serialize_profile_compact(profile)
        result["applied_amendments"] = applied
        result["conflicts_remaining"] = ensure_trace_structure(profile.source_trace)["conflicts"]
        return result

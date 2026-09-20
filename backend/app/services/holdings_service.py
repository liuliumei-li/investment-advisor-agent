"""持仓导入服务(US-03):三种方式(清单粘贴 / CSV 文件 / 文本描述)→ 校验归一 → 落快照。

设计要点(对齐 docs/architecture.md §2.1、§4.2):
- 每次导入生成一个 holding_snapshots 批次,持仓行共享 snapshot_id;
  最新批次 = 当前持仓,最近两个批次对比得出换手特征(US-03 AC-2);
- 文本描述走 app/llm 适配层抽取结构化持仓(与 US-02 同模式),缺项/格式异常时
  明确报错并提示补充(AC-4);
- 校验归一逻辑单点定义(normalize_holding_rows),三种方式共用,错误提示带条目位置。
"""

import csv
import io
import logging
from decimal import Decimal, InvalidOperation

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import LLMServiceError, ValidationFailed
from app.llm.base import LLMClient
from app.models.holding import AssetType, Holding, HoldingSnapshot
from app.models.user_profile import RiskLevel
from app.repositories.holding_repo import HoldingRepository
from app.services.holdings_analysis import analyze_holdings, analyze_turnover, build_holding_summary
from app.services.profile_service import ProfileService

logger = logging.getLogger(__name__)

# CSV 上传大小上限(AC-4:超出给出明确错误提示)
MAX_CSV_BYTES = 1024 * 1024

# 来源标签(错误提示用)
SOURCE_LABELS = {"list": "清单", "csv": "CSV", "text": "文本描述"}

# 资产类别别名 → AssetType(清单 / CSV / 文本抽取结果统一归一,小写比对)
_ASSET_TYPE_ALIASES = {
    "stock": AssetType.STOCK,
    "股票": AssetType.STOCK,
    "个股": AssetType.STOCK,
    "etf": AssetType.ETF,
    "cb": AssetType.CB,
    "可转债": AssetType.CB,
    "转债": AssetType.CB,
    "fund": AssetType.FUND,
    "基金": AssetType.FUND,
}

# CSV 表头别名 → 规范字段(小写比对)
_CSV_HEADER_ALIASES = {
    "asset_type": ["asset_type", "资产类别", "资产类型", "类别", "类型"],
    "code": ["code", "代码", "编码"],
    "name": ["name", "名称", "名字"],
    "quantity": ["quantity", "数量", "股数", "份额"],
    "cost_price": ["cost_price", "成本价", "成本", "买入价"],
}

SYSTEM_PROMPT = (
    "你是证券投顾系统的持仓抽取模块。用户用自然语言描述自己的持仓,"
    "你的任务是从中抽取持仓清单,输出 JSON。\n"
    '输出格式:{"holdings": [{"asset_type": "stock|etf|cb|fund", "code": "代码", "name": "名称", '
    '"quantity": 数量, "cost_price": 成本价}]}\n'
    "规则:\n"
    "1. asset_type 按语义判断:个股/股票→stock,ETF→etf,可转债/转债→cb,基金→fund;\n"
    "2. quantity 与 cost_price 必须是正数;用户未提及某项时输出 null;\n"
    "3. code 与 name 至少一个非空,未提及时输出 null;\n"
    '4. 与持仓无关的内容不要抽取;没有任何持仓信息时输出 {"holdings": []};\n'
    "5. 只输出 JSON,不要输出其他文字。"
)


def _parse_positive_decimal(value, field_label: str, where: str) -> Decimal:
    """数量/成本价解析:必须为正的有限数字,缺失与格式错误分别提示(AC-4)。"""
    raw = "" if value is None else str(value).strip()
    if not raw:
        raise ValidationFailed(f"{where}缺少「{field_label}」,请补充后重试")
    try:
        number = Decimal(raw)
    except (InvalidOperation, ValueError):
        raise ValidationFailed(f"{where}的「{field_label}」格式不正确:{raw!r}") from None
    if not number.is_finite() or number <= 0:
        raise ValidationFailed(f"{where}的「{field_label}」必须大于 0:{raw!r}")
    return number


def normalize_holding_rows(rows: list[dict], *, where: str) -> list[dict]:
    """逐条校验归一(三种方式共用):asset_type 别名映射、数量/成本价 >0、code/name 至少其一。

    返回 {asset_type: AssetType, code: str|None, name: str|None, quantity: Decimal, cost_price: Decimal}。
    """
    if not rows:
        raise ValidationFailed(f"{where}:未识别到任何持仓,请至少提供一条持仓信息")
    normalized = []
    for i, row in enumerate(rows, start=1):
        where_i = f"{where}第 {i} 条"
        raw_type = str(row.get("asset_type") or "").strip().lower()
        asset_type = _ASSET_TYPE_ALIASES.get(raw_type)
        if asset_type is None:
            raise ValidationFailed(
                f"{where_i}的资产类别「{raw_type}」无法识别,支持:股票/ETF/可转债/基金(stock/etf/cb/fund)"
            )
        code = (row.get("code") or "").strip() or None
        name = (row.get("name") or "").strip() or None
        if not code and not name:
            raise ValidationFailed(f"{where_i}缺少代码或名称,至少提供其一")
        quantity = _parse_positive_decimal(row.get("quantity"), "数量", where_i)
        cost_price = _parse_positive_decimal(row.get("cost_price"), "成本价", where_i)
        normalized.append(
            {"asset_type": asset_type, "code": code, "name": name, "quantity": quantity, "cost_price": cost_price}
        )
    return normalized


def _decode_csv(data: bytes) -> str:
    """编码容错:UTF-8(含 BOM)优先,GBK 兜底(Windows Excel 常见)。"""
    for encoding in ("utf-8-sig", "gbk"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValidationFailed("CSV 文件编码无法识别(支持 UTF-8 / GBK)")


def _csv_cell(line: list[str], col_map: dict[str, int], field: str) -> str:
    idx = col_map.get(field)
    return line[idx].strip() if idx is not None and idx < len(line) else ""


def parse_csv(data: bytes) -> list[dict]:
    """CSV 解析:表头别名映射,返回原始行字典(字段值与清单模式一致,归一校验复用 normalize_holding_rows)。"""
    text = _decode_csv(data)
    try:
        lines = list(csv.reader(io.StringIO(text)))
    except csv.Error as exc:
        raise ValidationFailed(f"CSV 格式错误:{exc}") from exc
    lines = [line for line in lines if any(cell.strip() for cell in line)]  # 跳过空行
    if not lines:
        raise ValidationFailed("CSV 文件为空")
    header = [cell.strip().lower() for cell in lines[0]]
    col_map: dict[str, int] = {}
    for field, aliases in _CSV_HEADER_ALIASES.items():
        for idx, name in enumerate(header):
            if name in aliases:
                col_map[field] = idx
                break
    if "asset_type" not in col_map:
        raise ValidationFailed("CSV 缺少资产类别列(asset_type),请检查表头")
    rows = []
    for line in lines[1:]:
        rows.append(
            {
                "asset_type": _csv_cell(line, col_map, "asset_type"),
                "code": _csv_cell(line, col_map, "code") or None,
                "name": _csv_cell(line, col_map, "name") or None,
                "quantity": _csv_cell(line, col_map, "quantity"),
                "cost_price": _csv_cell(line, col_map, "cost_price"),
            }
        )
    return rows


class HoldingsService:
    """持仓导入编排:抽取/解析 → 归一校验 → 快照落库 → 分析 → 画像合并(不碰 SQL,经 Repository)。"""

    def __init__(
        self,
        session: AsyncSession,
        repo: HoldingRepository,
        llm: LLMClient | None = None,
        profile_service: ProfileService | None = None,
    ):
        self.session = session
        self.repo = repo
        self.llm = llm
        self.profile_service = profile_service

    async def import_holdings(self, user_id: int, source: str, raw_rows: list[dict]) -> dict:
        """归一校验后落库:新快照 + 持仓行,同一事务提交(AC-4 格式错误抛 ValidationFailed)。"""
        rows = normalize_holding_rows(raw_rows, where=SOURCE_LABELS.get(source, source))
        snapshot = await self.repo.create_snapshot(HoldingSnapshot(user_id=user_id, source=source))
        await self.repo.add_holdings(
            [Holding(snapshot_id=snapshot.id, user_id=user_id, **row) for row in rows]
        )
        await self.session.commit()
        logger.info("用户 %s 持仓导入完成:source=%s,快照 %s,持仓 %s 条", user_id, source, snapshot.id, len(rows))
        return {"snapshot_id": snapshot.id, "source": source, "holding_count": len(rows)}

    async def import_from_text(self, user_id: int, text: str) -> dict:
        """文本描述导入(AC-1 方式一):LLM 抽取 → 归一校验 → 落库。"""
        if self.llm is None:
            raise LLMServiceError("LLM 服务未配置")
        rows = await self.extract_from_text(text)
        return await self.import_holdings(user_id, "text", rows)

    async def import_and_analyze(self, user_id: int, source: str, raw_rows: list[dict]) -> dict:
        """导入 → 分析 → 画像合并(AC-2/AC-3):返回导入、分析与画像更新结果。"""
        imported = await self.import_holdings(user_id, source, raw_rows)
        return {**imported, **await self._analyze_and_merge(user_id)}

    async def import_text_and_analyze(self, user_id: int, text: str) -> dict:
        """文本描述导入并分析(AC-1 方式一):LLM 抽取 → 导入 → 分析。"""
        rows = await self.extract_from_text(text)
        return await self.import_and_analyze(user_id, "text", rows)

    async def import_csv_and_analyze(self, user_id: int, data: bytes) -> dict:
        """CSV 文件导入并分析(AC-1 方式三):大小上限 + 编码容错解析 → 导入 → 分析。"""
        if len(data) > MAX_CSV_BYTES:
            raise ValidationFailed("CSV 文件过大,请控制在 1MB 以内")
        return await self.import_and_analyze(user_id, "csv", parse_csv(data))

    async def _analyze_and_merge(self, user_id: int) -> dict:
        """对最新快照做持仓分析,结论纳入画像(未注入 profile_service 时仅分析,供单测)。"""
        snapshots = await self.repo.latest_snapshots(user_id, limit=2)
        current = await self._snapshot_rows(snapshots[0].id)
        previous = await self._snapshot_rows(snapshots[1].id) if len(snapshots) > 1 else None
        analysis = analyze_holdings(current)
        turnover = analyze_turnover(current, previous)
        summary = build_holding_summary(analysis, turnover)
        if self.profile_service is None:
            return {
                "analysis": analysis,
                "turnover": turnover,
                "profile_updates": [],
                "incomplete_sources": [],
                "risk_deviation": None,
            }
        merged = await self.profile_service.merge_holdings_fields(
            user_id,
            holding_habit_summary=summary,
            inferred_risk_level=RiskLevel(analysis["inferred_risk_level"]),
            stock_share=analysis["stock_share"],
        )
        return {"analysis": analysis, "turnover": turnover, **merged}

    async def _snapshot_rows(self, snapshot_id: int) -> list[dict]:
        """ORM 持仓行 → 分析引擎入参(纯 dict 行)。"""
        rows = await self.repo.get_holdings_by_snapshot(snapshot_id)
        return [
            {
                "asset_type": r.asset_type,
                "code": r.code,
                "name": r.name,
                "quantity": r.quantity,
                "cost_price": r.cost_price,
            }
            for r in rows
        ]

    async def extract_from_text(self, text: str) -> list[dict]:
        """LLM 抽取结构化持仓(原始行,归一校验在 import_holdings 中统一进行)。"""
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text.strip()},
        ]
        data = await self.llm.chat_json(messages, temperature=0.1, purpose="holdings-extract")
        holdings = data.get("holdings") if isinstance(data, dict) else None
        if not isinstance(holdings, list):
            raise ValidationFailed("持仓抽取结果格式异常,请重试")
        if not holdings:
            raise ValidationFailed("未能从文本描述中识别到持仓信息,请补充具体持仓(名称、数量、成本价)后重试")
        return holdings

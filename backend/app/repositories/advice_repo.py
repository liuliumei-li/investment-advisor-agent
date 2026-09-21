"""advices / data_citations / agent_runs / compliance_audit_logs 数据访问(建议聚合实体组)。

四者同事务落库(architecture.md §4.2):保存顺序建议 → 引用/运行记录/合规日志由 Service 编排。
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.advice import Advice, AgentRun, ComplianceAuditLog, DataCitation


class AdviceRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def add_advice(self, advice: Advice) -> Advice:
        self.session.add(advice)
        await self.session.flush()
        return advice

    async def get_advice(self, advice_id: int) -> Advice | None:
        result = await self.session.execute(select(Advice).where(Advice.id == advice_id))
        return result.scalar_one_or_none()

    async def add_citations(self, citations: list[DataCitation]) -> None:
        self.session.add_all(citations)
        await self.session.flush()

    async def list_citations(self, advice_id: int) -> list[DataCitation]:
        result = await self.session.execute(
            select(DataCitation).where(DataCitation.advice_id == advice_id).order_by(DataCitation.id)
        )
        return list(result.scalars().all())

    async def add_agent_run(self, run: AgentRun) -> None:
        self.session.add(run)
        await self.session.flush()

    async def add_compliance_log(self, log: ComplianceAuditLog) -> None:
        self.session.add(log)
        await self.session.flush()

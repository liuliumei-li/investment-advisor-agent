"""同花顺问财 SkillHub 适配器占位(赛题指定数据源,architecture.md §2.1)。

赛题源文档给定入口:https://www.iwencai.com/unifiedwap/skillhub。截至 2026-09-21 团队暂无
SkillHub 账号/接口凭据,故本适配器只落地 DataSource 接口与配置位(SKILLHUB_BASE_URL/SKILLHUB_TOKEN,
经 .env 注入,不入库)。拿到凭据后实现 fetch() 即可即插即用:
- 行情/资讯/研报三源在 MarketDataService 中按名注入,无需改动编排层;
- 来源名「同花顺问财SkillHub」已在 SOURCE_WHITELIST 白名单内(BR-DAT-02)。
"""

from app.core.config import settings
from app.datasource.base import DataPoint, DataSource


class SkillHubSource(DataSource):
    """问财 SkillHub 占位:未配置凭据时抛 DataSourceUnavailable(编排层降级并标注)。"""

    name = "同花顺问财SkillHub"

    async def fetch(self) -> list[DataPoint]:
        if not settings.skillhub_token:
            raise self.unavailable("SkillHub 凭据未配置(环境变量 SKILLHUB_TOKEN),接入待凭据到位后启用")
        raise self.unavailable("SkillHub 适配器尚未实现,待接口规格与凭据到位后接入")

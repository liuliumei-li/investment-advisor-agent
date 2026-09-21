"""数据源抽象(architecture.md §2.1):统一 DataSource 接口、白名单校验、溯源标识注入(BR-DAT-01~04)。

- 每个适配器产出 DataPoint 列表:来源名称(白名单内)、来源类型、数据点内容、溯源链接、数据时间戳;
- 白名单(BR-DAT-02):建议中引用的来源必须在此清单内,幻觉检测 gate 据此校验;
- 智能体/服务禁止直连外部数据源,必须经本抽象(架构 §3.2)。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.core.exceptions import DataSourceUnavailable

# 来源类型(data_citations.source_type,architecture.md §4.2)
SOURCE_TYPE_QUOTE = "quote"
SOURCE_TYPE_NEWS = "news"
SOURCE_TYPE_RESEARCH = "research"
SOURCE_TYPE_PROFILE = "profile"

# BR-DAT-02 数据源白名单:建议引用来源仅允许此清单(新增来源须同步本清单与架构文档)
SOURCE_WHITELIST = {"东方财富行情", "东方财富研报", "新浪财经快讯", "同花顺问财SkillHub", "用户画像"}


@dataclass
class DataPoint:
    """一条可溯源数据(BR-DAT-04:来源标识 + 时间戳 + 链接)。"""

    source_name: str
    source_type: str
    data_point: str
    source_url: str
    data_timestamp: str  # ISO-8601 字符串(新闻/研报为发布时间,行情为抓取时间)


class DataSource(ABC):
    """外部数据源适配器抽象:失败抛 DataSourceUnavailable(50003,由编排层降级)。"""

    name: str = ""  # 白名单来源名

    @abstractmethod
    async def fetch(self) -> list[DataPoint]:
        """拉取该源的数据点列表。"""

    def unavailable(self, reason: str) -> DataSourceUnavailable:
        return DataSourceUnavailable(f"数据源「{self.name}」不可用:{reason}")

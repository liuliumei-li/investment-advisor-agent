"""应用配置:全部经环境变量或 .env 注入(禁止硬编码敏感配置,AGENTS.md 规则 8)。"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "investment-advisor-agent"
    debug: bool = False
    # dev/test 默认 SQLite;生产经环境变量切换 PostgreSQL(asyncpg)
    database_url: str = "sqlite+aiosqlite:///./dev.db"
    redis_url: str = "redis://localhost:6379/0"
    # 默认值仅用于本地开发,生产必须经环境变量覆盖
    jwt_secret: str = "dev-only-secret-do-not-use-in-prod"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440
    # 第三方大模型适配层(TC-02):API Key 仅经环境变量/.env 注入,禁止入库(AGENTS.md 规则 8)
    llm_base_url: str = "https://api.deepseek.com"
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"
    llm_timeout_seconds: float = 10.0
    llm_max_retries: int = 2
    # 同花顺问财 SkillHub(赛题指定数据源,US-06 占位):拿到凭据后经环境变量注入即启用
    skillhub_base_url: str = "https://www.iwencai.com/unifiedwap/skillhub"
    skillhub_token: str = ""
    # 外部数据源出站直连(本机注册表代理开关不稳定,BR-DAT-01 三源与 LLM 一致策略)
    datasource_timeout_seconds: float = 8.0


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

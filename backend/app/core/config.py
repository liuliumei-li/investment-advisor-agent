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


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

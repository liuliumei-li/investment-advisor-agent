"""种子数据:写入问卷 v1(幂等,已存在同版本则跳过)。

用法(在 backend 目录下):python scripts/seed_questionnaire.py
"""

import asyncio

from sqlalchemy import select

from app.data.questionnaire_v1 import QUESTIONNAIRE_V1
from app.db.session import async_session_factory
from app.models.questionnaire import Questionnaire


async def main() -> None:
    async with async_session_factory() as session:
        existing = (
            await session.execute(
                select(Questionnaire).where(Questionnaire.version == QUESTIONNAIRE_V1["version"])
            )
        ).scalar_one_or_none()
        if existing is not None:
            print(f"问卷 v{QUESTIONNAIRE_V1['version']} 已存在,跳过")
            return
        session.add(Questionnaire(**QUESTIONNAIRE_V1))
        await session.commit()
        print(f"已写入问卷 v{QUESTIONNAIRE_V1['version']}:{QUESTIONNAIRE_V1['title']}")


if __name__ == "__main__":
    asyncio.run(main())

# AI 使用日志

> 依据 AGENTS.md 规则 10:每次大模型调用(含 AI 生成代码的使用)必须记录(模型、用途、时间、影响范围)。

| 日期 | 模型/工具 | 用途 | 影响范围 |
|---|---|---|---|
| 2026-09-17 ~ 09-19 | Claude Code | Sprint 1 / US-01 后端开发:认证(注册/登录/JWT)、画像(问卷获取/提交/作答查询)、问卷 v1 内容与评分规则(BR-IMG-07)、Alembic 迁移脚手架 | backend/(全部代码) |
| 2026-09-19 | Claude Code | 补齐 US-01 单元/集成测试 77 例;修复测试暴露的缺陷(主键在 SQLite 下不自增、get_db 导入错位);编写 docs/api.md 与本日志 | backend/tests/、backend/app/models/、backend/app/core/deps.py、backend/app/db/session.py、docs/api.md、本文档 |
| 2026-09-20 | Claude Code | US-02 Step 1:app/llm 适配层(LLMClient 抽象 + DeepSeek 实现 + 50004 错误码 + 配置)与单测 | backend/app/llm/、backend/app/core/config.py、backend/app/core/exceptions.py、backend/tests/unit/test_llm_client.py、docs/architecture.md、本文档 |
| 2026-09-20 | Claude Code | US-02 Step 2:对话画像抽取/追问引擎(dialog_profile_service)、画像元素级合并与冲突保留(profile_service)、BR-IMG-01 对话映射规则登记(requirements.md v1.1)与单测 | backend/app/services/dialog_profile_service.py、backend/app/services/profile_service.py、backend/app/cache/redis_client.py、backend/tests/helpers.py、backend/tests/unit/test_dialog_profile_service.py、docs/requirements.md、docs/architecture.md、本文档 |
| 2026-09-20 | Claude Code | US-02 Step 3:POST /api/v1/profile/dialog 接口落地(Schema/DI/Controller)、集成测试、docs/api.md 登记;修复用户名正则致测试失败与 LLM Key 惰性失败 | backend/app/api/routers/profile.py、backend/app/schemas/profile.py、backend/app/llm/deepseek_client.py、backend/tests/integration/test_dialog_profile_api.py、docs/api.md、docs/architecture.md、本文档 |

说明:截至 2026-09-20,开发阶段对第三方大模型的真实调用尚未发生;自 US-02(对话画像)起将引入真实 LLM 调用,届时按次补充(模型、用途、时间、影响范围)。本表当前仅记录 AI 辅助开发行为。

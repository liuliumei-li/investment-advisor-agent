# AI 使用日志

> 依据 AGENTS.md 规则 10:每次大模型调用(含 AI 生成代码的使用)必须记录(模型、用途、时间、影响范围)。

| 日期 | 模型/工具 | 用途 | 影响范围 |
|---|---|---|---|
| 2026-09-17 ~ 09-19 | Claude Code | Sprint 1 / US-01 后端开发:认证(注册/登录/JWT)、画像(问卷获取/提交/作答查询)、问卷 v1 内容与评分规则(BR-IMG-07)、Alembic 迁移脚手架 | backend/(全部代码) |
| 2026-09-19 | Claude Code | 补齐 US-01 单元/集成测试 77 例;修复测试暴露的缺陷(主键在 SQLite 下不自增、get_db 导入错位);编写 docs/api.md 与本日志 | backend/tests/、backend/app/models/、backend/app/core/deps.py、backend/app/db/session.py、docs/api.md、本文档 |

说明:开发阶段依赖同花顺问财 SkillHub 之外的第三方大模型调用尚未发生(见 requirements.md TC-02,属 E2/E3 阶段);本表仅记录 AI 辅助开发行为。开发中实际调用 SkillHub/LLM 的会话记录在进入 E2 后按次补充。

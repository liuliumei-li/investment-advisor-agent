# AI 使用日志

> 依据 AGENTS.md 规则 10:每次大模型调用(含 AI 生成代码的使用)必须记录(模型、用途、时间、影响范围)。

| 日期 | 模型/工具 | 用途 | 影响范围 |
|---|---|---|---|
| 2026-09-17 ~ 09-19 | Claude Code | Sprint 1 / US-01 后端开发:认证(注册/登录/JWT)、画像(问卷获取/提交/作答查询)、问卷 v1 内容与评分规则(BR-IMG-07)、Alembic 迁移脚手架 | backend/(全部代码) |
| 2026-09-19 | Claude Code | 补齐 US-01 单元/集成测试 77 例;修复测试暴露的缺陷(主键在 SQLite 下不自增、get_db 导入错位);编写 docs/api.md 与本日志 | backend/tests/、backend/app/models/、backend/app/core/deps.py、backend/app/db/session.py、docs/api.md、本文档 |
| 2026-09-20 | Claude Code | US-02 Step 1:app/llm 适配层(LLMClient 抽象 + DeepSeek 实现 + 50004 错误码 + 配置)与单测 | backend/app/llm/、backend/app/core/config.py、backend/app/core/exceptions.py、backend/tests/unit/test_llm_client.py、docs/architecture.md、本文档 |
| 2026-09-20 | Claude Code | US-02 Step 2:对话画像抽取/追问引擎(dialog_profile_service)、画像元素级合并与冲突保留(profile_service)、BR-IMG-01 对话映射规则登记(requirements.md v1.1)与单测 | backend/app/services/dialog_profile_service.py、backend/app/services/profile_service.py、backend/app/cache/redis_client.py、backend/tests/helpers.py、backend/tests/unit/test_dialog_profile_service.py、docs/requirements.md、docs/architecture.md、本文档 |
| 2026-09-20 | Claude Code | US-02 Step 3:POST /api/v1/profile/dialog 接口落地(Schema/DI/Controller)、集成测试、docs/api.md 登记;修复用户名正则致测试失败与 LLM Key 惰性失败 | backend/app/api/routers/profile.py、backend/app/schemas/profile.py、backend/app/llm/deepseek_client.py、backend/tests/integration/test_dialog_profile_api.py、docs/api.md、docs/architecture.md、本文档 |
| 2026-09-20 | DeepSeek deepseek-chat(真实调用,3 次) | US-02 对话画像冒烟测试:单消息三要素抽取(25% 回撤 → C3)、追问澄清(C1 判断)、多轮合并与冲突保留验证 | 运行验证(HTTP + 真实 Redis),无代码变更;调用经 app/llm 适配层,用量由 app.llm 日志记录 |
| 2026-09-20 | Claude Code | US-03 持仓历史分析:holdings/holding_snapshots 模型与迁移、三种导入方式(清单/CSV/文本 LLM 抽取)、分析引擎(集中度/资产分布/换手/BR-IMG-04 反推风险等级)、画像合并与偏差提示(≥2 档)、API 与测试(新增 45 例);requirements.md v1.2 补定量化规则 | backend/app/models/holding.py、backend/app/repositories/holding_repo.py、backend/app/services/holdings_service.py、backend/app/services/holdings_analysis.py、backend/app/services/profile_service.py、backend/app/api/routers/profile.py、backend/app/schemas/holdings.py、backend/alembic/、backend/pyproject.toml(新增 python-multipart)、backend/tests/、docs/requirements.md、docs/api.md、docs/architecture.md、本文档 |
| 2026-09-21 | Claude Code | US-04 画像报告可视化:source_trace 溯源持久化(迁移 f9fc8dd8995c)、三合并路径冲突持久化与问卷重提不覆盖修复、报告/当前画像/确认修正三接口(GET /report、GET /profile、PUT /profile)、雷达评分归一与报告维度构建、新增测试 67 例;requirements.md v1.3 补定量化规则 | backend/app/models/user_profile.py、backend/app/services/profile_report.py、backend/app/services/profile_report_service.py、backend/app/services/profile_service.py、backend/app/api/routers/profile.py、backend/app/schemas/profile.py、backend/alembic/、backend/tests/、docs/requirements.md、docs/api.md、docs/architecture.md、本文档 |
| 2026-09-21 | Claude Code | US-05 画像动态更新:profile_update_events 更新历史表(迁移 462d3a7e1d90)、四更新路径(问卷测评/对话/持仓/确认修正)同事务事件记录、GET /api/v1/profile/history 分页查询、新增测试 19 例;requirements.md v1.4 补定量化规则 | backend/app/models/profile_update_event.py、backend/app/repositories/profile_update_repo.py、backend/app/services/profile_history.py、backend/app/services/profile_service.py、backend/app/services/profile_report_service.py、backend/app/api/routers/profile.py、backend/alembic/、backend/tests/、docs/requirements.md、docs/api.md、docs/architecture.md、本文档 |

> 注:US-03 开发与测试全程使用 FakeLLM 替身,无真实大模型调用。

| 2026-09-21 | DeepSeek deepseek-chat(真实调用,1 次) | E1 全链路演示冒烟:对话画像三要素一次抽取(20% 回撤→C2、收益预期、期限),验证冲突保留与报告披露链路 | 运行验证(HTTP + 真实 Redis),无代码变更;调用经 app/llm 适配层,用量由 app.llm 日志记录 |
| 2026-09-21 | DeepSeek deepseek-chat(真实调用,1 次) | US-06 大盘研判冒烟:真实三源数据(新浪行情/新浪快讯/东财研报)注入提示词,LLM 生成走势解读/影响因素/逻辑链/风险提示/仓位建议,经幻觉校验与合规 gate 落库 | 运行验证(HTTP + 真实 Redis),无代码变更;调用经 app/llm 适配层,用量由 app.llm 日志记录 |
| 2026-09-21 | Claude Code | US-06 大盘研判:数据源适配层(免费公开三源+SkillHub 占位、白名单与溯源、行情 5 秒缓存与降级)、研判服务与两道 gate(幻觉校验+合规审核)、咨询会话 SSE 接口与四要素落库、新增测试 44 例;requirements.md v1.5 补定量化规则 | backend/app/datasource/、backend/app/services/(hallucination/compliance/market_advisor/chat_service)、backend/app/models/(chat/advice)、backend/app/repositories/(chat/advice_repo)、backend/app/api/routers/(chat/advice)、backend/app/schemas/chat.py、backend/alembic/、backend/tests/、docs/requirements.md、docs/api.md、docs/architecture.md、本文档 |

说明:自 2026-09-20 起(US-02 冒烟测试)开发阶段已开始真实调用第三方大模型(DeepSeek),上表按次补充(模型、用途、时间、影响范围);开发辅助类 AI(Claude Code)调用与真实 LLM 调用分行记录。

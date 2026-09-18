# AGENTS.md — AI 编程智能体行为约束

本文件是对 AI 编程智能体(Claude Code 等)的强制约束。任何 AI 生成或修改的代码必须满足以下 10 条,违反任一条的提交视为不合格。

架构与分层依据:[docs/architecture.md](docs/architecture.md);需求单一事实来源:[docs/requirements.md](docs/requirements.md)。

## 十条严格约束

1. **Controller 层禁止写业务逻辑**。Controller(app/api)只允许:参数校验、鉴权、调用 Service、响应封装,方法体不得超过"取参 → 调 Service → 返回"三步。出现业务 `if/for` 分支即违规。

2. **必须写单元测试,且全部通过后才能提交**。新增或修改 Service、Repository、Agent(LangGraph 节点)、幻觉检测、合规规则等核心逻辑,必须配套单元测试;禁止注释掉、跳过(skip)或删除失败的测试。核心模块覆盖率低于 80% 不得合并(见 architecture.md §6.4)。

3. **不允许修改与任务无关的文件**。每次任务只修改任务直接相关的文件;禁止"顺手"重构、格式化、删除无关代码。提交前用 `git diff` 自查:任何无关文件的变更必须撤销。

4. **代码与文档必须同步**。接口变更须同步更新 [docs/api.md](docs/api.md);架构变更(新增模块、修改分层依赖、Redis 键、数据模型)须同步更新 [docs/architecture.md](docs/architecture.md);业务规则变更须同步更新 [docs/requirements.md](docs/requirements.md) 及其覆盖矩阵(§7.2)。文档与代码不一致的提交视为违规。

5. **严格分层,禁止跨层调用与反向依赖**。依赖方向唯一:Controller → Service → {Repository, Agent} → {Datasource, Cache}。禁止:Controller 直接访问 Repository/Datasource/Redis;Service 直接写 SQL 或裸操作 Redis;Repository 之间互相调用;Agent 直连外部数据源(必须经 Datasource 适配器)。

6. **所有 API 必须遵循接口规范**。统一响应格式 `{code, message, data, trace_id}`、错误码表、SSE 事件序列见 [docs/architecture.md](docs/architecture.md) §5。新接口必须同时更新 docs/api.md 并配套集成测试;禁止自定义响应格式或新增未登记的错误码。

7. **智能体输出必须过两道 gate**。所有 Agent 生成内容必须经过幻觉检测(validate)与合规审核(compliance)后才能返回用户,禁止绕过 gate 直接输出;禁止直连数据库/外部 API 获取数据(必须经 Repository / Datasource 适配器,含白名单校验)。

8. **禁止硬编码密钥与敏感配置**。API Key、Token、数据库密码等一律走环境变量或 `.env`(且 `.env` 不得入库);禁止在代码、注释、测试用例、提交信息中泄露真实密钥。

9. **禁止提交调试与临时代码**。不得提交 `print`/`console.log` 调试语句、临时代码、注释掉的大段代码、未使用的导入与变量;提交前运行 lint 并确保通过。

10. **记录 AI 使用日志**。每次大模型调用(含 AI 生成代码的使用)必须记录到 [docs/ai-usage-log.md](docs/ai-usage-log.md)(模型、用途、时间、影响范围);提交信息需如实说明 AI 参与程度。

## 提交前自检清单(逐条核对)

- [ ] 1. Controller 无业务逻辑
- [ ] 2. 新增核心逻辑配套单元测试且全部通过
- [ ] 3. `git diff` 只包含任务相关文件
- [ ] 4. 相关文档已同步更新
- [ ] 5. 无跨层调用、无反向依赖
- [ ] 6. 接口符合规范,docs/api.md 已更新
- [ ] 7. Agent 输出经过幻觉检测与合规 gate
- [ ] 8. 无硬编码密钥
- [ ] 9. 无调试/临时代码,lint 通过
- [ ] 10. AI 使用已记录到 docs/ai-usage-log.md

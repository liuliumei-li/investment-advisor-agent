# 系统架构设计:基于同花顺问财 SkillHub 的个性化证券投顾智能体系统

> 版本:v1.0 | 日期:2026-09-18 | 依据:[需求基线 docs/requirements.md](requirements.md)
> 技术栈:后端 FastAPI + LangGraph,前端 Vue 3 + ECharts,缓存 Redis

## 1. 架构概述

### 1.1 架构目标

架构设计服务于需求基线的四类硬性要求,并逐条落实(追溯关系见第 8 章):

| 需求目标 | 架构落实点 |
|---|---|
| "1+N"多智能体协同(BR-AGT-01) | LangGraph supervisor 图:主协调节点 + 4 个专业智能体节点 |
| ≥100 并发、≤3 秒响应、≥99.9% 可用性(BR-PER-01~03) | 异步全链路 + SSE 流式 + Redis 多级缓存 + 超时降级 |
| 数据溯源与幻觉控制(BR-DAT-04~05) | 输出前校验节点(幻觉检测)+ 合规节点两道 gate,引用数据全程落库 |
| 分层可测试(第 6 章) | Controller/Service/Repository 严格分层 + 依赖注入 + 接口抽象 |

### 1.2 技术栈

| 层 | 技术 | 说明 |
|---|---|---|
| 前端 | Vue 3 + Vite + TypeScript + Pinia + Vue Router + ECharts | SPA;ECharts 用于画像雷达图、行情图、逻辑链树、可视化面板(US-19) |
| 后端框架 | FastAPI + Uvicorn(多 worker) | 异步 API、原生 SSE、依赖注入、OpenAPI 自动生成 |
| 智能体编排 | LangGraph | 实现"1+N"supervisor 多智能体图、并行子任务、交叉验证与分歧处理节点 |
| 大模型 | 第三方大模型(经统一 LLM 适配层调用) | 满足技术约束 TC-02;适配层便于切换与记录用量 |
| 数据接入 | 同花顺问财 SkillHub API + 行情/新闻/研报数据源适配器 | 满足技术约束 TC-01/TC-03;适配器模式见 2.1 |
| 缓存 | Redis | 会话上下文、画像、行情、限流、降级信号(键规范见 4.4) |
| 数据库 | PostgreSQL(生产)/ SQLite(开发、测试)+ SQLAlchemy 2.x + Alembic | Repository 层统一访问 |
| 测试 | pytest / Playwright / Locust / testcontainers | 见第 6 章 |

### 1.3 总体架构图

```
┌─────────────────────────────────────────────────────────────────┐
│ 前端 SPA(Vue 3 + ECharts)                                        │
│  views:画像中心 / 咨询会话 / 报告 / 管理后台                        │
│  components:ChatPanel / VisualPanel / TraceTree / RiskRadar      │
│  stores(Pinia)/ router / api client                              │
└──────────────────────────────┬──────────────────────────────────┘
                               │ HTTPS(REST + SSE 流式)
┌──────────────────────────────▼──────────────────────────────────┐
│ 接入层(FastAPI middleware)                                        │
│  JWT 鉴权 / Redis 限流 / 请求日志 / trace_id 注入                  │
├─────────────────────────────────────────────────────────────────┤
│ Controller 层(app/api/routers)  —— 只做:参数校验、鉴权、调用、封装   │
│  auth / profile / chat / advice / admin                          │
├─────────────────────────────────────────────────────────────────┤
│ Service 层(app/services)  —— 业务编排,禁止直接访问 DB/Redis        │
│  profile / portfolio / advisory / report / trace                 │
│  hallucination(幻觉检测) / compliance(合规审核)                    │
├─────────────────────────────────────────────────────────────────┤
│ Agent 层(app/agents,LangGraph)                                   │
│  ┌──────────── 主协调智能体(supervisor graph)──────────────────┐ │
│  │ parse → dispatch → [宏观研究‖行业分析‖个股研究‖基金配置]     │ │
│  │        → validate(交叉验证/幻觉检测)→ diverge(分歧处理)      │ │
│  │        → compliance(合规 gate)→ output                      │ │
│  └────────────────────────────────────────────────────────────┘ │
│  专业智能体各自绑定专属工具(经 Datasource 适配器获取数据)           │
├──────────────────────────────┬──────────────────────────────────┤
│ Repository 层(app/repositories)│ Datasource 适配器层(app/datasource)│
│  SQLAlchemy 封装,无业务逻辑     │  SkillHub / 实时行情 / 财经新闻    │
│                               │  / 研究报告;统一接口 + 白名单       │
├──────────────────────────────┴──────────────────────────────────┤
│ 数据层:PostgreSQL  │  Redis(缓存/限流/降级信号)                     │
└─────────────────────────────────────────────────────────────────┘
```

### 1.4 关键架构决策

| 决策 | 理由 | 对应需求 |
|---|---|---|
| 用 LangGraph supervisor 图实现"1+N",而非多服务 RPC | 与需求"1 个主协调 + N 个专业智能体"天然对应;图内节点间状态显式传递,便于测试与追溯;并行分支实现多智能体并发(BR-AGT-05) | BR-AGT-01~05 |
| 智能体输出过两道 gate(幻觉检测 → 合规审核)后才会输出给用户 | 需求明确要求幻觉控制与合规安全,gate 放在图内输出节点之前,物理上不可绕过 | BR-DAT-05、BR-CMP-04 |
| 咨询接口采用 SSE 流式返回 | 3 秒硬指标下,流式首包即可见,避免长等待;多智能体分步结果可实时推送(可视化协作过程) | BR-PER-02、US-19 |
| 所有外部数据经 Datasource 适配器接入 | 数据源白名单与溯源标识在适配器层统一强制,防止任何模块绕过白名单直接取数 | BR-DAT-01~04 |
| 建议的完整逻辑链与数据引用落库(data_citations),而非仅存结论文本 | 溯源(US-21/24)、合规审计(US-29)、结构化报告(US-13)都需要结构化引用数据 | BR-DAT-04、BR-CMP-04 |

## 2. 模块划分

### 2.1 后端模块(app/)

| 模块 | 职责 | 对应需求 |
|---|---|---|
| `app/api/`(Controller 层) | 路由、参数校验、鉴权依赖、SSE 流封装;无业务逻辑 | 第 5 章接口规范 |
| `app/services/` | 业务编排:画像建模与更新、持仓分析、咨询会话、报告组装、逻辑链/溯源查询、幻觉检测、合规审核 | US-01~30 对应服务 |
| `app/agents/`(LangGraph) | 主协调图(supervisor)+ 4 个专业智能体节点 + 校验/分歧/合规节点;智能体工具注册表 | US-14~18、BR-AGT |
| `app/repositories/` | SQLAlchemy 数据访问封装:users / profiles / profile_updates / holdings / sessions / messages / advices / citations / agent_runs / compliance_logs | 数据模型第 4 章 |
| `app/datasource/` | 外部数据适配器:SkillHub(问财)、实时行情、财经新闻、研究报告;统一 `DataSource` 抽象接口 + 白名单校验 + 溯源标识注入 | BR-DAT-01~04、TC-01/03 |
| `app/models/` | SQLAlchemy ORM 模型与 Pydantic Schema(请求/响应) | 第 4 章 |
| `app/core/` | 配置(环境变量)、JWT、日志、trace_id 中间件、限流、监控指标、异常处理器 | BR-CMP、BR-PER |
| `app/cache/` | Redis 客户端封装与键规范(统一 TTL 管理、降级信号读写) | 第 4.4 节 |
| `app/llm/` | 第三方大模型统一适配层:`LLMClient` 抽象 + DeepSeek 实现(OpenAI 兼容;调用、超时、重试、用量日志),失败抛 50004 | TC-02、docs/ai-usage-log.md |

### 2.2 Agent 层(LangGraph)内部结构

```
主协调图(supervisor graph)状态:AgentState
  { session_id, user_id, profile, messages, scenario, tasks[],
    agent_results[], citations[], validation_issues[], divergence_summary,
    final_advice, compliance_status }

节点编排(一条咨询请求的生命周期,对应 UC-07):
  1. parse       —— 解析意图、判定场景(六类之一)、加载画像(Redis 缓存)
  2. dispatch    —— 任务分解并分派给相关专业智能体(BR-AGT-02)
  3. specialists —— 并行分支:宏观研究 / 行业分析 / 个股研究 / 基金配置
                    (LangGraph parallel superstep,实现 BR-AGT-05)
                    每个智能体经其工具调用 Datasource 适配器取数
  4. validate    —— 交叉验证(事实一致性/逻辑一致性)+ 幻觉检测
                    (hallucination_service;BR-AGT-03、BR-DAT-05)
  5. diverge     —— 分歧处理:投票 / 主协调裁决 / 无法收敛则披露分歧
                    (BR-AGT-04)
  6. compliance  —— 合规审核 gate:承诺性表述过滤、风险提示与免责声明注入
                    (BR-CMP-01~04);不通过则改写或拒绝输出
  7. output      —— 组装结构化建议,落库 advices / data_citations / agent_runs,
                    经 SSE 推送 result 事件
```

专业智能体与工具(每个工具都经 Datasource 适配器取数,禁止智能体直连外部 API):

| 智能体 | 专属工具 | 服务场景 |
|---|---|---|
| 宏观研究智能体 | 大盘行情、宏观指标、政策资讯 | US-06 大盘研判 |
| 行业分析智能体 | 板块行情、资金流向、行业研报 | US-07 行业/概念追踪 |
| 个股研究智能体 | 个股行情、财务数据、公告资讯 | US-08 个股分析 |
| 基金配置智能体 | ETF/基金数据、费率与跟踪误差 | US-09 ETF 筛选、US-11 组合优化 |

### 2.3 前端模块(web/)

| 模块 | 职责 | 对应需求 |
|---|---|---|
| `views/ProfileCenter` | 问卷作答、持仓导入、对话画像、画像报告查看与修正 | US-01~05、UC-01 |
| `views/Chat` | 咨询会话(SSE 流式接收)、多轮对话、追问澄清 | US-20、UC-02~07 |
| `views/Advice` | 建议详情:风险提示/收益预期/仓位建议展示、结构化报告 | US-13、US-22 |
| `views/Admin` | 合规审核日志、系统监控指标(并发/响应/可用性) | US-27~30、UC-10 |
| `components/ChatPanel` | 消息流、流式渲染、追问入口 | US-20 |
| `components/VisualPanel` | ECharts 分析过程与结论可视化面板 | US-19 |
| `components/TraceTree` | 逻辑链逐级展开树 + 数据来源点击溯源 | US-21、US-24 |
| `components/RiskRadar` | ECharts 画像雷达图(风险等级/收益预期/投资期限/持仓习惯) | US-04 |
| `stores/`(Pinia) | user / profile / session 状态管理 | — |
| `api/` | 统一 API 客户端(响应格式解包、错误码映射、SSE 解析) | 第 5 章 |

### 2.4 模块依赖规则

- 依赖方向唯一向下:Controller → Service → {Repository, Agent} → {Datasource, Cache}。
- Agent 层可调用 Service(幻觉检测、合规审核以服务形式被图节点复用),Service 不得反向调用 Agent。
- 前端仅依赖后端 API,不直连任何外部数据源。

## 3. 分层规则(Controller / Service / Repository)

### 3.1 职责定义

| 层 | 允许做什么 | 禁止做什么 |
|---|---|---|
| **Controller**(app/api) | HTTP 参数解析与校验(Pydantic);鉴权依赖;调用 Service;响应/错误封装;SSE 事件序列化 | 写任何业务逻辑、条件分支决策;直接访问 Repository/Datasource/Redis;直接拼装业务对象 |
| **Service**(app/services) | 业务流程编排;跨 Repository/Agent/Datasource 的协调;事务边界;缓存读写(app/cache) | 直接写 SQL;裸操作 Redis 客户端;处理 HTTP 细节(读 header、拼响应码) |
| **Repository**(app/repositories) | 单一实体的 CRUD 与查询封装;分页;SQLAlchemy 会话管理 | 业务规则判断;跨实体业务协调;调用外部 API |
| **Agent 层**(app/agents) | 智能体决策逻辑(LangGraph 节点与工具);任务分解、交叉验证、分歧处理 | 直连外部数据源(必须经 Datasource 适配器);直接写库(结果经 Service 落库);跳过幻觉/合规 gate 输出 |
| **Datasource**(app/datasource) | 外部 API 调用、限速、重试、白名单校验、溯源标识注入、响应标准化 | 业务规则判断;面向用户的文案生成 |

### 3.2 调用规则(强制)

1. Controller 方法体不得超过"取参 → 调 Service → 返回"三步;出现 `if/for` 业务分支即违规。
2. Service 访问数据库必须经 Repository;Repository 之间不得互相调用(跨实体协调上移到 Service)。
3. 所有外部数据必须经 Datasource 适配器;白名单校验(BR-DAT-02)在适配器层强制,任何层不可绕过。
4. 智能体输出必须经 validate(幻觉检测)与 compliance(合规)两个节点后才能进入 output 节点。
5. 异常跨层传播只使用 `core/exceptions` 定义的业务异常,由全局异常处理器统一转为错误码(见 5.1)。
6. 跨层对象转换:Pydantic Schema(HTTP 层)↔ 领域对象(Service)↔ ORM 模型(Repository),禁止 ORM 模型直接泄漏到 Controller 响应。

### 3.3 规范示例

Controller 正确写法(仅示意):

```python
# app/api/routers/profile.py
router = APIRouter(prefix="/api/v1/profile", tags=["profile"])

@router.post("/questionnaire")
async def submit_questionnaire(
    payload: QuestionnaireSubmitRequest,          # ① 参数校验(Pydantic)
    user: User = Depends(get_current_user),       # ② 鉴权
    service: ProfileService = Depends(get_profile_service),
):
    result = await service.submit_questionnaire(user.id, payload)   # ③ 调用
    return ApiResponse(data=result)                # ④ 封装
```

反例(禁止):

```python
@router.post("/questionnaire")
async def submit_questionnaire(...):
    # 违反规则 3.2-1/2:Controller 内出现业务分支并直接访问 Repository
    if payload.score < 20:
        profile.risk_level = "C1"
    await db.execute(update(UserProfile)...)
```

## 4. 数据模型

### 4.1 ER 关系图

```
users ──1:1── user_profiles (版本历史:version 递增 + profile_update_events 留痕)
  │
  ├─1:N─ profile_update_events  (画像更新历史,US-05)
  ├─1:N─ holdings              (持仓)
  ├─1:N─ questionnaire_responses
  ├─1:N─ chat_sessions ──1:N── chat_messages ──N:1── advices
  │                                                  │
  │                                    ├─1:N─ data_citations
  │                                    ├─1:1─ agent_runs(coordinator_trace)
  │                                    └─1:N─ compliance_audit_logs
  └─(questionnaires 为模板表,questionnaire_responses N:1 questionnaires)
```

### 4.2 核心实体定义

**users 用户**

| 字段 | 类型 | 说明 |
|---|---|---|
| id | BIGINT PK | 自增主键 |
| username | VARCHAR(50) UK | 登录名 |
| password_hash | VARCHAR(255) | bcrypt 哈希 |
| created_at / updated_at | TIMESTAMP | 时间戳 |

**user_profiles 用户画像**(BR-IMG-02 四要素)

| 字段 | 类型 | 说明 |
|---|---|---|
| id | BIGINT PK | |
| user_id | BIGINT FK UK | 一个用户一份当前画像 |
| risk_level | ENUM('C1','C2','C3','C4','C5') | 风险等级(BR-IMG-01 五档) |
| return_expectation_low / high | NUMERIC(5,2) | 收益预期区间(年化 %) |
| investment_horizon | VARCHAR(20) | 投资期限(短期/中期/长期) |
| holding_habit_summary | TEXT | 持仓习惯摘要(来自持仓分析) |
| source_mix | JSON | 三来源权重:问卷/对话/持仓(BR-IMG-03) |
| confidence | NUMERIC(3,2) | 画像置信度(信息不完整时降低) |
| confirmed | BOOLEAN | 是否经用户确认(BR-IMG-05) |
| source_trace | JSON | 逐要素溯源与待确认冲突(BR-DAT-04、BR-IMG-05,US-04):`{elements: {字段: {source, quote, version, updated_at}}, conflicts: [...]}` |
| version | INT | 版本号,更新递增(US-05 更新历史) |
| updated_at | TIMESTAMP | |

**profile_update_events 画像更新历史**(US-05,已实现)

| 字段 | 类型 | 说明 |
|---|---|---|
| id | BIGINT PK | |
| user_id | BIGINT FK | 画像所属用户(索引) |
| version | INT | 本次更新后的画像版本(与 user_profiles.version 对应) |
| trigger | VARCHAR(20) | 触发来源:问卷测评/对话更新/持仓更新/用户修正/用户确认 |
| changes | JSON | 实际变化的要素:[{field, before, after, source, quote}] |
| conflicts | JSON | 保留原值的冲突主张:[{field, current, proposed, source, quote}] |
| created_at | TIMESTAMP | 事件时间(何时) |

> 每次画像版本递增与画像同事务写入一条事件(BR-IMG-06:何时/因何/哪一要素变化);事件构建与触发标签单点定义于 app/services/profile_history.py。

**questionnaires / questionnaire_responses 问卷与作答**(US-01)

| 实体 | 关键字段 |
|---|---|
| questionnaires | id, title, version, questions(JSON:题目/选项/维度/分值), created_at |
| questionnaire_responses | id, user_id FK, questionnaire_id FK, answers(JSON), score(INT), risk_level, created_at |

**holdings 持仓**(US-03,已实现)

| 字段 | 类型 | 说明 |
|---|---|---|
| id | BIGINT PK | |
| snapshot_id | BIGINT FK → holding_snapshots.id | 所属导入批次(每次导入一个批次,最新批次 = 当前持仓) |
| user_id | BIGINT FK | |
| asset_type | ENUM('stock','etf','cb','fund') | 资产类别 |
| code / name | VARCHAR(20) / VARCHAR(50) | 代码与名称(至少其一) |
| quantity / cost_price | NUMERIC(16,4) / NUMERIC(12,4) | 数量与成本价(必填正数) |
| created_at | TIMESTAMP | 落库时间 |

**holding_snapshots 持仓导入快照**(US-03,已实现)

| 字段 | 类型 | 说明 |
|---|---|---|
| id | BIGINT PK | |
| user_id | BIGINT FK | |
| source | VARCHAR(20) | 导入方式:list / csv / text |
| created_at | TIMESTAMP | 导入时间 |

> 换手特征(US-03 AC-2)基于最近两个快照对比;此设计相对基线规划新增快照维度(基线规划仅表达"最新持仓"语义)。

**chat_sessions / chat_messages 会话与消息**(US-20 上下文记忆)

| 实体 | 关键字段 |
|---|---|
| chat_sessions | id, user_id FK, scenario ENUM('market','industry','stock','etf','cb','portfolio','general'), status, created_at, last_active_at |
| chat_messages | id, session_id FK, role ENUM('user','assistant'), content TEXT, advice_id FK NULL, created_at |

**advices 投顾建议**(BR-ADV-01 四要素落库)

| 字段 | 类型 | 说明 |
|---|---|---|
| id | BIGINT PK | |
| session_id / user_id | BIGINT FK | 来源会话与用户 |
| scenario | ENUM(六类场景) | 场景类型(BR-ADV-03) |
| conclusion | TEXT | 核心结论 |
| logic_chain | JSON | 逻辑链结构(结论→依据→数据),对应 US-21 |
| risk_tips | TEXT | 风险提示(BR-CMP-01 强制) |
| position_suggestion | JSON | 仓位建议(与画像匹配,BR-IMG-04) |
| return_expectation | JSON | 收益预期区间(非承诺,BR-ADV-02) |
| divergence_summary | JSON NULL | 分歧披露(BR-AGT-04) |
| compliance_status | ENUM('pending','passed','rejected') | 合规 gate 结果(BR-CMP-04) |
| created_at | TIMESTAMP | |

**data_citations 数据引用(溯源,BR-DAT-04)**

| 字段 | 类型 | 说明 |
|---|---|---|
| id | BIGINT PK | |
| advice_id | BIGINT FK | 所属建议 |
| source_name | VARCHAR(100) | 来源名称(白名单内) |
| source_type | ENUM('quote','news','research','profile') | 来源类型 |
| data_point | TEXT | 数据点内容 |
| source_url | VARCHAR(500) | 溯源链接 |
| data_timestamp | TIMESTAMP | 数据时间戳(BR-DAT-03 时效依据) |
| verified | BOOLEAN | 幻觉检测校验结果(BR-DAT-05) |

**agent_runs 智能体运行记录**(协作过程可视化,US-19)

| 字段 | 类型 | 说明 |
|---|---|---|
| id | BIGINT PK | |
| advice_id | BIGINT FK UK | 每次建议对应一次运行 |
| coordinator_trace | JSON | 任务分解、各智能体结果、交叉验证、分歧处理全记录 |
| duration_ms | INT | 执行耗时(响应时间监控来源) |
| started_at / finished_at | TIMESTAMP | |

**compliance_audit_logs 合规审计日志**(US-29/30)

| 字段 | 类型 | 说明 |
|---|---|---|
| id | BIGINT PK | |
| advice_id | BIGINT FK | |
| passed | BOOLEAN | 是否通过 |
| matched_rules | JSON | 命中规则与改写动作 |
| action | ENUM('pass','rewrite','reject') | 处理动作 |
| created_at | TIMESTAMP | |

### 4.3 存储选型

- **数据库**:PostgreSQL 15(生产),SQLite(本地开发与单元测试);ORM 用 SQLAlchemy 2.x,迁移用 Alembic。选型理由:JSON 字段(逻辑链、协作轨迹)、ENUM 约束、事务一致性(建议+引用+审计日志同事务落库)均需要关系库支持。
- **建议与引用同事务落库**:advices、data_citations、agent_runs、compliance_audit_logs 在同一事务提交,保证溯源链路与审计记录不缺失(BR-DAT-04、BR-CMP-04)。

### 4.4 Redis 键设计(统一经 app/cache 访问)

| 键模式 | TTL | 用途 | 对应需求 |
|---|---|---|---|
| `session:ctx:{session_id}` | 30 min(活动续期) | 会话上下文,多轮记忆(US-02 对话画像、US-20 咨询会话) | US-02、US-20 |
| `profile:{user_id}` | 10 min | 画像热缓存:GET /api/v1/profile read-through 读取(US-04);画像更新即失效 | US-04、US-05、BR-IMG-06 |
| `quote:{code}` | 5 s | 行情缓存;值内含时间戳,读取时校验时效 | BR-DAT-03 |
| `rate:{user_id}:{window}` | 窗口长度 | 限流计数(保护并发容量) | BR-PER-01 |
| `degrade:flags` | 无(运维维护) | 降级信号:数据源故障/超载开关 | BR-PER-04 |
| `advice:trace:{advice_id}` | 24 h | 逻辑链与溯源热缓存 | US-21、US-24 |

规则:所有键必须带 TTL(降级信号除外);键名与用途变更须同步更新本节;禁止各层裸用 redis-py 直接拼接键。

## 5. 接口规范

### 5.1 通用规范

| 项 | 规范 |
|---|---|
| 基础路径 | `/api/v1`,版本演进时新增 `/api/v2`,旧版本至少保留一个迭代 |
| 统一响应 | `{ "code": 0, "message": "ok", "data": {...}, "trace_id": "..." }`;`code=0` 表示成功;`trace_id` 由接入层生成,贯穿日志与智能体链路,便于排障 |
| 鉴权 | JWT Bearer;除 `auth/*` 外全部接口需认证;`admin/*` 额外要求 R-03/R-04 角色 |
| 流式接口 | 咨询消息接口采用 SSE(见 5.3) |
| 分页 | `page`/`page_size`,响应统一为 `{ items, total, page, page_size }` |
| 错误码 | 见下表,由全局异常处理器统一生成,业务代码只抛 `core/exceptions` 业务异常 |

错误码表:

| code | 含义 | 场景 |
|---|---|---|
| 0 | 成功 | — |
| 40001 | 参数校验失败 | Pydantic 校验不通过 |
| 40101 | 未认证 | 缺 Token / Token 非法 |
| 40102 | Token 过期 | 需刷新 |
| 40301 | 无权限 | 非管理员访问 admin 接口 |
| 40401 | 资源不存在 | 会话/建议/用户不存在 |
| 42901 | 请求过于频繁 | 触发限流(BR-PER-01 保护) |
| 42902 | 服务降级中 | 超载排队/降级提示(BR-PER-04) |
| 50001 | 服务内部错误 | 未捕获异常 |
| 50002 | 智能体执行失败 | 图执行异常(UC-07 扩展流程 3a) |
| 50003 | 数据源不可用 | 适配器层全部数据源故障 |
| 50004 | LLM 服务不可用 | 适配层调用失败/超时(重试耗尽)、鉴权失败、返回非 JSON |

### 5.2 接口清单

| 方法 | 路径 | 说明 | 关联 |
|---|---|---|---|
| POST | /api/v1/auth/register | 注册 | R-01 |
| POST | /api/v1/auth/login | 登录,返回 JWT | R-01 |
| GET | /api/v1/profile/questionnaires/latest | 获取最新问卷 | US-01 |
| POST | /api/v1/profile/questionnaire | 提交问卷作答,返回风险等级 | US-01、UC-01 |
| POST | /api/v1/profile/dialog | 对话画像抽取(多轮:session_id 可选,追问澄清,完成时返回画像更新 diff) | US-02 |
| POST | /api/v1/profile/import | 持仓导入并分析(mode=list 清单粘贴 / mode=text 文本描述;返回集中度、资产分布、换手特征、画像更新与偏差提示) | US-03 |
| POST | /api/v1/profile/import/csv | 持仓 CSV 文件导入并分析 | US-03 |
| GET | /api/v1/profile/report | 画像报告(含溯源与推断依据) | US-04 |
| PUT | /api/v1/profile | 确认/修正画像 | US-04、BR-IMG-05 |
| GET | /api/v1/profile | 当前画像 | US-04 |
| GET | /api/v1/profile/history | 画像更新历史(分页,最新在前) | US-05、BR-IMG-06 |
| POST | /api/v1/chat/sessions | 创建咨询会话 | US-20 |
| GET | /api/v1/chat/sessions | 会话列表(分页) | US-20 |
| POST | /api/v1/chat/sessions/{session_id}/messages | 发送咨询消息(SSE 流式,多智能体链路入口) | UC-02~07 |
| GET | /api/v1/chat/sessions/{session_id}/messages | 历史消息 | US-20 |
| GET | /api/v1/advice/{advice_id} | 建议详情(四要素展示) | US-22 |
| GET | /api/v1/advice/{advice_id}/trace | 逻辑链逐级展开与数据溯源 | US-21、US-24 |
| GET | /api/v1/advice/{advice_id}/report | 结构化报告(含导出格式) | US-13 |
| GET | /api/v1/admin/compliance-logs | 合规审核日志(分页,仅 R-03/R-04) | US-29、US-30 |
| GET | /api/v1/admin/metrics | 并发数、响应时间分位、可用性指标 | US-27、US-28、UC-10 |
| GET | /api/v1/admin/agents | 各智能体运行状态与近期运行记录 | US-14、US-15 |

### 5.3 关键接口示例

**POST /api/v1/chat/sessions/{session_id}/messages(SSE 流式)**

请求:

```json
{
  "content": "帮我分析一下宁德时代的投资价值",
  "scenario": "stock"
}
```

SSE 事件序列(对应 UC-07 主流程,事件间可穿插多条 `delta`):

```text
event: meta      data: {"session_id": 12, "scenario": "stock", "agents": ["个股研究","宏观研究"]}
event: delta     data: {"agent": "个股研究", "chunk": "宁德时代2025年报显示…"}
event: delta     data: {"agent": "宏观研究", "chunk": "当前宏观环境…"}
event: result    data: {"advice_id": 88, "conclusion": "…", "risk_tips": "…", "compliance_status": "passed"}
event: done      data: {"duration_ms": 2410}
```

约束:SSE 期间出现 `42902` 降级事件时,客户端展示"系统繁忙,正在排队/部分分析未完成"提示(UC-10 扩展流程)。

**GET /api/v1/advice/{advice_id}/trace 响应(节选)**

```json
{
  "code": 0,
  "message": "ok",
  "trace_id": "7f3a…",
  "data": {
    "advice_id": 88,
    "logic_chain": {
      "conclusion": "…",
      "branches": [
        {
          "claim": "公司营收连续两季度增长",
          "basis": "2026Q2 财报",
          "citations": [
            {
              "source_name": "问财·iFinD 财务数据",
              "source_type": "research",
              "data_point": "营收 328.6 亿元,同比 +18.2%",
              "source_url": "…",
              "data_timestamp": "2026-08-28T18:00:00+08:00",
              "verified": true
            }
          ]
        }
      ]
    },
    "agent_contributions": [
      {"agent": "个股研究", "contribution": "基本面与技术面分析"},
      {"agent": "宏观研究", "contribution": "行业景气度交叉验证"}
    ]
  }
}
```

## 6. 可测试性设计

### 6.1 可测试性设计原则

| 原则 | 实现方式 |
|---|---|
| 依赖注入 | FastAPI `Depends` 贯穿全链路;Service 构造参数为 Repository/Datasource 接口,测试注入 fake |
| 接口抽象 | Repository 与 Datasource 全部定义抽象基类;LLM 经 `app/llm` 适配层,测试注入 FakeLLM(固定输出) |
| 纯函数节点 | LangGraph 节点为 `(AgentState) -> AgentState 增量` 的纯函数,工具与 LLM 从状态/注册表注入,可脱离网络单测 |
| 图即被测对象 | 编译后的图导出 `graph` 对象,测试直接 `graph.invoke(state)` 做图级集成验证 |
| 输出 gate 可注入 | 幻觉检测与合规审核为独立 Service,测试可单独构造其规则集,实现错误注入 |
| 时间可控 | 数据时间戳(TTL、时效校验)一律使用可注入的 clock,测试不依赖真实等待 |

### 6.2 测试金字塔

| 层 | 工具 | 范围 | 对应需求 |
|---|---|---|---|
| 单元测试 | pytest + pytest-asyncio | Service 全部业务分支;Repository(内存 SQLite);每个 LangGraph 节点(注入 FakeLLM/FakeDataSource);幻觉检测与合规规则集;限流与降级逻辑 | 全部 BR 逻辑;覆盖率门槛:核心模块(app/services、app/agents、app/datasource)≥80% |
| 集成测试 | pytest + TestClient + testcontainers(PostgreSQL/Redis) | API → Service → Repository → DB 全链路;SSE 事件序列;Redis 缓存失效(画像更新后缓存命中验证);建议+引用+审计同事务落库 | 第 5 章接口规范、UC 主流程 |
| 端到端测试 | Playwright | 主旅程:注册 → 问卷画像 → 发起个股咨询(SSE 渲染)→ 逻辑链溯源点击 → 结构化报告查看 | UC-01、UC-03、UC-08 |
| 性能测试 | Locust | 100 并发混合场景压测,断言 P95 ≤3 秒;连续运行统计可用性 | BR-PER-01、BR-PER-02、BR-PER-03 |
| 故障演练 | 故障注入脚本(切数据源、打满限流) | 数据源故障 → 缓存降级 + 时效标注;超载 → 排队/降级提示;恢复自动 | BR-PER-04 |

### 6.3 关键机制专项测试(与需求基线强绑定)

| 专项 | 测试设计 | 断言 | 对应 BR |
|---|---|---|---|
| 幻觉检测错误注入 | FakeDataSource 向个股智能体注入不存在的财务数据(如虚构营收) | validate 节点拦截;citation 标记 verified=false 或结论拒绝输出"数据存疑" | BR-DAT-05 |
| 合规注入 | 向建议注入"保证收益 20%"表述 | compliance 节点改写或 reject;audit_log 记录 matched_rules | BR-ADV-02、BR-CMP-04 |
| 画像匹配矩阵 | 同一问题分别以 C1 与 C5 画像发起咨询 | 输出建议差异存在且符合 BR-IMG-04 比例矩阵 | BR-IMG-04 |
| 一致性校验 | 注入两个智能体对同一数据的不同引用值 | 触发交叉验证,进入分歧处理并披露 | BR-AGT-03、BR-AGT-04 |
| 溯源完整性 | 任意建议回溯其 data_citations | 每个数据点含来源与时间戳;无白名单外来源 | BR-DAT-02、BR-DAT-04 |
| 降级演练 | 演练环境关闭行情数据源并施加超载 | 返回标注时效的缓存结论/排队提示;故障恢复后自动恢复 | BR-PER-04 |

### 6.4 CI 流水线与质量门槛

1. **提交级**:lint(ruff)+ 单元测试,必须全绿;
2. **PR 级**:集成测试(testcontainers)+ 覆盖率报告,核心模块覆盖率 <80% 阻断合并;
3. **发布前**:Locust 性能报告(100 并发、P95 ≤3 秒)与降级演练记录随发布评审;
4. 性能与可用性指标(US-27/28)以流水线产出数据为准,同时满足赛题"系统测试报告"提交材料要求。

## 7. 非功能性设计要点(汇总)

| 关注点 | 设计 |
|---|---|
| ≤3 秒响应(BR-PER-02) | 全链路异步;SSE 流式首包优先;行情/画像 Redis 缓存(命中路径 <500ms);LangGraph 并行执行子任务;3 秒超时触发降级(部分结论 + 提示,UC-07 扩展 3a) |
| 100 并发(BR-PER-01) | Uvicorn 多 worker + 异步 IO;数据库连接池;Redis 限流保护;会话上下文按 session 隔离 |
| 99.9% 可用性(BR-PER-03) | 降级链路:排队 → 缓存(标注时效)→ 部分结论;健康检查与自动恢复;`admin/metrics` 暴露指标 |
| 合规安全(BR-CMP) | 输出前双 gate(幻觉检测 → 合规审核)不可绕过;全量审计日志;管理员角色隔离 admin 接口 |
| 可追溯性 | trace_id 贯穿 HTTP → Service → LangGraph 状态 → 落库;agent_runs.coordinator_trace 支撑协作过程可视化(US-19) |

## 8. 架构与需求基线追溯

| 需求基线条目 | 架构落实点 |
|---|---|
| BR-AGT-01/02/05("1+N"、任务分解、并发聚合) | LangGraph supervisor 图:dispatch 节点 + 4 专业智能体并行分支(2.2) |
| BR-AGT-03/04(交叉验证、分歧处理) | validate / diverge 节点;divergence_summary 落库并披露(2.2、4.2) |
| BR-DAT-01~03(多源、白名单、秒级) | Datasource 适配器层统一接入与白名单;quote 缓存 TTL 5s 带时间戳(2.1、4.4) |
| BR-DAT-04/05(溯源、幻觉检测) | data_citations 同事务落库 + validate 节点 gate + 错误注入测试(4.2、6.3) |
| BR-CMP-01~04(合规) | compliance 节点 gate、compliance_audit_logs、admin 审计接口(2.2、4.2、5.2) |
| BR-PER-01~04(并发/3 秒/可用性/降级) | 异步 + SSE + Redis 缓存/限流 + 降级链路 + Locust/演练验证(7、6.3) |
| BR-IMG-01~06(画像) | ProfileService + user_profiles 版本化 + profile 缓存失效策略(2.1、4.2、4.4) |
| US-19~22(可视化/交互/溯源) | TraceTree/VisualPanel/RiskRadar 组件 + /trace 接口 + SSE 协作过程推送(2.3、5.3) |
| US-26~28(高并发用户故事) | admin/metrics 监控 + 性能测试流水线(5.2、6.2) |
| 赛题提交材料(测试报告) | CI 流水线自动产出性能与降级演练报告(6.4) |

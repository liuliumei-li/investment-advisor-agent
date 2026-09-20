# 接口文档(API Reference)

> 通用规范(统一响应信封、错误码表、鉴权约定)见 [architecture.md](architecture.md) §5.1;本文档登记**已实现**接口。
> 完整接口规划见 architecture.md §5.2(未实现的接口不在此登记)。

## 已实现接口(US-01、US-02,2026-09-20)

| 方法 | 路径 | 说明 | 鉴权 | 关联 |
|---|---|---|---|---|
| POST | /api/v1/auth/register | 注册 | 否 | R-01 |
| POST | /api/v1/auth/login | 登录,返回 JWT | 否 | R-01 |
| GET | /api/v1/profile/questionnaires/latest | 获取最新问卷 | 是 | US-01 AC-1 |
| POST | /api/v1/profile/questionnaire | 提交问卷作答,返回风险等级与画像要素 | 是 | US-01 AC-2、UC-01 |
| GET | /api/v1/profile/questionnaire/latest-response | 查看本人最近一次测评结果 | 是 | US-01 AC-3 |
| POST | /api/v1/profile/dialog | 对话画像:多轮抽取/追问,完成时返回画像更新 diff | 是 | US-02、UC-01 |

所有接口响应均为统一信封:`{ "code": 0, "message": "ok", "data": ..., "trace_id": "..." }`;`code=0` 表示成功,`trace_id` 同时写入响应头 `X-Trace-Id`。

---

## 1. POST /api/v1/auth/register

注册新用户。

**请求体**

```json
{
  "username": "alice",
  "password": "secret123"
}
```

| 字段 | 类型 | 约束 |
|---|---|---|
| username | string | 3~50 字符,仅 `[a-zA-Z0-9_]` |
| password | string | 6~64 字符 |

**成功响应(200)**

```json
{
  "code": 0,
  "message": "ok",
  "data": { "user_id": 1, "username": "alice" },
  "trace_id": "7f3a2c1d9e0b4a6f"
}
```

**错误**

| code | HTTP | 场景 |
|---|---|---|
| 40001 | 400 | 参数校验失败(格式不合法)/ 用户名已存在 |

---

## 2. POST /api/v1/auth/login

登录并签发 JWT。

**请求体**

```json
{
  "username": "alice",
  "password": "secret123"
}
```

| 字段 | 类型 | 约束 |
|---|---|---|
| username | string | 1~50 字符 |
| password | string | 1~64 字符 |

**成功响应(200)**

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
    "token_type": "bearer",
    "user_id": 1
  },
  "trace_id": "7f3a2c1d9e0b4a6f"
}
```

后续请求携带请求头:`Authorization: Bearer <access_token>`。

**错误**

| code | HTTP | 场景 |
|---|---|---|
| 40001 | 400 | 用户名或密码错误 |

---

## 3. GET /api/v1/profile/questionnaires/latest

获取最新版本问卷(按 version 降序取首条)。

**请求头**:`Authorization: Bearer <access_token>`

**成功响应(200)**

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "id": 1,
    "title": "投资风险承受能力测评",
    "version": 1,
    "questions": [
      {
        "id": "rt1",
        "dimension": "risk_tolerance",
        "dimension_name": "风险承受能力",
        "text": "您能接受的最大投资回撤幅度是多少?",
        "profile_field": null,
        "options": [
          { "id": "a", "text": "不能接受本金亏损(5%以内)", "score": 1 },
          { "id": "b", "text": "5%~10%", "score": 2 }
        ]
      }
    ]
  },
  "trace_id": "7f3a2c1d9e0b4a6f"
}
```

题目字段说明(完整结构定义见 [app/data/questionnaire_v1.py](../backend/app/data/questionnaire_v1.py)):

| 字段 | 说明 |
|---|---|
| dimension | 所属维度:`risk_tolerance` / `return_expectation` / `investment_horizon` / `investment_experience` |
| profile_field | 携带画像要素元数据的题目:收益预期题为 `return_expectation`(选项带 `return_low`/`return_high`),期限题为 `investment_horizon`(选项带 `horizon`),其余为 `null` |
| options.score | 选项分值 1(低风险)~ 5(高风险) |

**错误**

| code | HTTP | 场景 |
|---|---|---|
| 40101 | 401 | 未认证(缺 Token / Token 非法) |
| 40102 | 401 | Token 过期 |

---

## 4. POST /api/v1/profile/questionnaire

提交问卷作答。系统校验完整性 → 评分 → 生成风险等级与画像要素 → 落库(作答记录与画像同事务保存)。

**请求头**:`Authorization: Bearer <access_token>`

**请求体**

```json
{
  "questionnaire_id": 1,
  "answers": [
    { "question_id": "rt1", "option_id": "c" },
    { "question_id": "re1", "option_id": "c" }
  ]
}
```

| 字段 | 类型 | 约束 |
|---|---|---|
| questionnaire_id | int | 必须等于最新问卷 id(防陈旧提交) |
| answers | array | 每题恰好一个答案,不得重复/遗漏 |

**成功响应(200)**(示例为全选中间选项 c 的结果)

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "questionnaire_id": 1,
    "score": 60,
    "risk_level": "C4",
    "risk_level_name": "进取型",
    "dimension_scores": {
      "risk_tolerance": 60,
      "return_expectation": 60,
      "investment_horizon": 60,
      "investment_experience": 60
    },
    "profile": {
      "risk_level": "C4",
      "risk_level_name": "进取型",
      "return_expectation_low": 6.0,
      "return_expectation_high": 10.0,
      "investment_horizon": "中期",
      "holding_habit_summary": null,
      "source_mix": { "questionnaire": 1.0 },
      "confidence": 0.6,
      "confirmed": false,
      "version": 1
    },
    "incomplete_sources": ["对话", "持仓"]
  },
  "trace_id": "7f3a2c1d9e0b4a6f"
}
```

字段说明:

| 字段 | 说明 |
|---|---|
| score | 加权总分(0~100),规则见下文"问卷评分规则" |
| dimension_scores | 各维度归一化得分(0~100) |
| profile.confidence | 画像置信度;仅问卷单一来源时为 0.60(BR-IMG-03:三来源未齐,提示补充) |
| profile.version | 画像版本;每次提交递增(BR-IMG-06 更新留痕) |
| incomplete_sources | 尚未收集的画像来源(BR-IMG-03) |

**错误**

| code | HTTP | 场景 |
|---|---|---|
| 40001 | 400 | 作答不完整 / 题目无效 / 选项无效 / 题目重复作答 |
| 40401 | 404 | 问卷不存在或已更新,请获取最新问卷 |

---

## 5. GET /api/v1/profile/questionnaire/latest-response

查看本人最近一次问卷测评结果(US-01 AC-3:结果保存并与账户关联,可再次查看)。

**请求头**:`Authorization: Bearer <access_token>`

**成功响应(200)**

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "id": 1,
    "questionnaire_id": 1,
    "score": 60,
    "risk_level": "C4",
    "risk_level_name": "进取型",
    "answers": [{ "question_id": "rt1", "option_id": "c" }],
    "created_at": "2026-09-19T12:00:00"
  },
  "trace_id": "7f3a2c1d9e0b4a6f"
}
```

尚未作答时 `data` 为 `null`。

**错误**

| code | HTTP | 场景 |
|---|---|---|
| 40101 | 401 | 未认证 |
| 40102 | 401 | Token 过期 |

---

## 6. POST /api/v1/profile/dialog

自然语言对话建立画像(US-02、UC-01)。每轮返回已抽取要素与追问;风险承受/收益预期/投资期限三要素齐备或用户 `finish=true` 时收口,执行画像合并并返回本次更新 diff(AC-4)。

**请求头**:`Authorization: Bearer <access_token>`

**请求体**

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| session_id | string | 否 | 多轮会话 id;首次不传(服务端生成并返回),后续轮次带回 |
| message | string | 否* | 本轮用户消息(*finish=false 时必填,≤2000 字) |
| finish | bool | 否 | true 时不再抽取,按已有信息收口合并 |

**成功响应(200,追问轮)**

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "session_id": "a1b2c3d4e5f6a7b8",
    "reply": "好的,还想再了解几点:\n1. 您期望的年化收益率大概是多少?(例如:5%~10%)\n2. 这笔钱您计划投资多长时间?(例如:1 年以内、1~3 年、3 年以上)",
    "needs_clarification": true,
    "completed": false,
    "slots": {
      "risk_tolerance": { "level": "C3", "evidence": "我能承受 20% 回撤" }
    },
    "profile_updates": null
  },
  "trace_id": "7f3a2c1d9e0b4a6f"
}
```

**成功响应(200,收口轮)**

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "session_id": "a1b2c3d4e5f6a7b8",
    "reply": "画像已更新,本次更新要素如下,可在画像报告中确认或修正。",
    "needs_clarification": false,
    "completed": true,
    "slots": {
      "risk_tolerance": { "level": "C3", "evidence": "我能承受 20% 回撤" },
      "return_expectation": { "low": 10, "high": 15, "evidence": "希望年化 10% 到 15%" },
      "investment_horizon": { "value": "长期", "evidence": "打算放三年" }
    },
    "profile_updates": [
      {
        "field": "risk_level",
        "before": null,
        "after": "C3",
        "source": "对话",
        "quote": "我能承受 20% 回撤",
        "applied": true,
        "conflict": false
      }
    ]
  },
  "trace_id": "7f3a2c1d9e0b4a6f"
}
```

**行为说明**

- 追问由服务端模板生成,每轮最多 2 个;追问轮数上限 5,超限按已有信息收口(UC-01 扩展 2a);
- 画像合并规则(BR-IMG-03/05):对话要素与已有画像一致则采纳;冲突时保留原值,并在 `profile_updates` 中以 `conflict: true` 披露,待用户在画像报告确认/修正(US-04);
- 每个抽取值经原文引用校验(`evidence` 必须为用户原话片段),校验失败即丢弃并追问;
- 画像来源权重与置信度:仅对话来源时 0.55;问卷+对话各 0.5、置信度 0.85。

**错误**

| code | HTTP | 场景 |
|---|---|---|
| 40001 | 400 | finish=false 且 message 为空;风险承受信息不足且无既有画像(收口时) |
| 40101 | 401 | 未认证 |
| 40102 | 401 | Token 过期 |
| 50004 | 500 | LLM 服务不可用(超时/鉴权失败/返回异常,重试耗尽) |

---

## 附录:问卷评分规则(BR-IMG-07)

评分实现在 [app/services/risk_scoring.py](../backend/app/services/risk_scoring.py),与 [requirements.md](requirements.md) §6.1 BR-IMG-07 保持同步:

| 维度 | 权重 |
|---|---|
| 风险承受能力 risk_tolerance | 40% |
| 收益预期 return_expectation | 20% |
| 投资期限 investment_horizon | 20% |
| 投资经验 investment_experience | 20% |

- 单维度得分 = 该维度选项分值之和 / 满分(题数 × 5)× 100,四舍五入;
- 总分 = 各维度得分加权求和;
- 总分 → 风险等级(BR-IMG-01 五档):≥80 → C5 激进型;≥60 → C4 进取型;≥45 → C3 平衡型;≥30 → C2 稳健型;其余 → C1 保守型。

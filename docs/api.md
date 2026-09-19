# 接口文档(API Reference)

> 通用规范(统一响应信封、错误码表、鉴权约定)见 [architecture.md](architecture.md) §5.1;本文档登记**已实现**接口。
> 完整接口规划见 architecture.md §5.2(未实现的接口不在此登记)。

## 已实现接口(US-01,2026-09-19)

| 方法 | 路径 | 说明 | 鉴权 | 关联 |
|---|---|---|---|---|
| POST | /api/v1/auth/register | 注册 | 否 | R-01 |
| POST | /api/v1/auth/login | 登录,返回 JWT | 否 | R-01 |
| GET | /api/v1/profile/questionnaires/latest | 获取最新问卷 | 是 | US-01 AC-1 |
| POST | /api/v1/profile/questionnaire | 提交问卷作答,返回风险等级与画像要素 | 是 | US-01 AC-2、UC-01 |
| GET | /api/v1/profile/questionnaire/latest-response | 查看本人最近一次测评结果 | 是 | US-01 AC-3 |

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

"""测试辅助函数:完整作答构造与注册/登录快捷流程。"""


class FakeLLM:
    """固定输出序列的 LLMClient 替身(architecture.md §6.1),单测/集成测试注入。"""

    def __init__(self, responses: list[dict]):
        self.responses = list(responses)
        self.calls: list[list[dict]] = []

    async def chat_json(self, messages, *, temperature=0.2, purpose="general"):
        self.calls.append(messages)
        if not self.responses:
            raise AssertionError("FakeLLM 脚本耗尽")
        return self.responses.pop(0)


def full_answers(questions, option_id="c"):
    """按问卷题目生成完整作答(每题同一选项);选项默认 c(分值 3,总分 60 → C4)。"""
    return [{"question_id": q["id"], "option_id": option_id} for q in questions]


async def register_and_login(client, username="tester", password="secret123") -> str:
    """注册并登录,返回 access_token(集成测试用)。"""
    resp = await client.post(
        "/api/v1/auth/register", json={"username": username, "password": password}
    )
    assert resp.json()["code"] == 0
    login = await client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )
    assert login.json()["code"] == 0
    return login.json()["data"]["access_token"]

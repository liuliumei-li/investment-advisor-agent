"""auth 接口集成测试:注册/登录全链路与统一响应信封(architecture.md §5.1)。"""


class TestRegisterApi:
    async def test_register_success_envelope(self, client):
        resp = await client.post(
            "/api/v1/auth/register", json={"username": "alice", "password": "secret123"}
        )
        body = resp.json()
        assert resp.status_code == 200
        assert body["code"] == 0
        assert body["message"] == "ok"
        assert body["data"]["username"] == "alice"
        assert body["data"]["user_id"] > 0
        # 信封含 trace_id,且与响应头一致
        assert body["trace_id"]
        assert resp.headers["X-Trace-Id"] == body["trace_id"]

    async def test_duplicate_username_conflict(self, client):
        payload = {"username": "alice", "password": "secret123"}
        await client.post("/api/v1/auth/register", json=payload)
        resp = await client.post("/api/v1/auth/register", json=payload)
        body = resp.json()
        assert resp.status_code == 400
        assert body["code"] == 40001
        assert body["message"] == "用户名已存在"

    async def test_invalid_payload_rejected(self, client):
        # 用户名过短/密码过短:参数校验失败(统一错误码 40001)
        resp = await client.post(
            "/api/v1/auth/register", json={"username": "ab", "password": "x"}
        )
        body = resp.json()
        assert resp.status_code == 400
        assert body["code"] == 40001
        assert body["message"] == "参数校验失败"


class TestLoginApi:
    async def test_login_success_returns_jwt(self, client):
        await client.post(
            "/api/v1/auth/register", json={"username": "bob", "password": "secret123"}
        )
        resp = await client.post(
            "/api/v1/auth/login", json={"username": "bob", "password": "secret123"}
        )
        body = resp.json()
        assert resp.status_code == 200
        assert body["code"] == 0
        assert body["data"]["token_type"] == "bearer"
        assert body["data"]["access_token"]
        assert body["data"]["user_id"] > 0

    async def test_wrong_password_rejected(self, client):
        await client.post(
            "/api/v1/auth/register", json={"username": "bob", "password": "secret123"}
        )
        resp = await client.post(
            "/api/v1/auth/login", json={"username": "bob", "password": "wrong-pass"}
        )
        body = resp.json()
        assert resp.status_code == 400
        assert body["code"] == 40001
        assert body["message"] == "用户名或密码错误"

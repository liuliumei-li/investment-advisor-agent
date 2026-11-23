import json

from fastapi import FastAPI

from tools.fuyao import get_price_snapshot, resolve_thscode

from pydantic import BaseModel

from agents.trader import agent


app = FastAPI(title="A18 个性化证券投顾智能体", version="0.1.0")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/quote")
def quote(name: str = "贵州茅台"):
    """按名称查行情：先消歧成 thscode，再取快照。"""
    codes = json.loads(resolve_thscode.invoke({"keyword": name}))
    if not codes:
        return {"error": f"没找到标的：{name}"}

    thscode = codes[0]["thscode"]
    snapshot = json.loads(get_price_snapshot.invoke({"thscodes": thscode}))
    return {
        "query": name,
        "resolved": codes[0],
        "snapshot": snapshot[0] if snapshot else None,
    }


class ChatIn(BaseModel):
    message: str
    thread_id: str = "demo"


@app.post("/api/chat")
def chat(body: ChatIn):
    result = agent.invoke(
        {"messages": [{"role": "user", "content": body.message}]},
        config={"configurable": {"thread_id": body.thread_id}},
    )
    msgs = result["messages"]
    # 把模型调用过的工具列出来，方便你观察它到底干了什么
    tools_called = [
        {"name": tc["name"], "args": tc["args"]}
        for m in msgs
        for tc in (getattr(m, "tool_calls", None) or [])
    ]
    return {"reply": msgs[-1].content, "tools_called": tools_called}



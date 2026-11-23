"""投顾智能体：让大模型自己决定调用哪个数据工具。"""
import os

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver

from tools.fuyao import get_price_history, get_price_snapshot, resolve_thscode

load_dotenv()

MODEL = ChatOpenAI(
    model="deepseek-chat",
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
    temperature=0.3,
    timeout=60,
)

SYSTEM_PROMPT = """你是 A 股投顾助手，服务于普通个人投资者。

工作规则：
1. 用户提到股票名称时，必须先调用 resolve_thscode 拿到标准代码（如 600519.SH），不要自己猜代码。
2. 只根据工具返回的数据回答，禁止编造任何数字。工具没返回的数据，就直说"这个数据我拿不到"。
3. 回答里每个关键数字后面注明来源，例如"（扶摇行情快照）"。
4. 涉及买卖建议时必须给出风险提示，并说明这只是信息整理，不构成投资建议。
5. 如果 resolve_thscode 返回多个候选，先问用户指的是哪一个，不要自己选。
6. 用简洁的中文回答，不要长篇大论。
"""

agent = create_agent(
    model=MODEL,
    tools=[resolve_thscode, get_price_snapshot, get_price_history],
    system_prompt=SYSTEM_PROMPT,
    checkpointer=InMemorySaver(),
)
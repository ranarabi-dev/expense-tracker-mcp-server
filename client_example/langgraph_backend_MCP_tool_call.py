import os
import asyncio
import threading

from langgraph.graph import StateGraph, START, END
from dotenv import load_dotenv
from langchain_groq import ChatGroq
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from typing import TypedDict, Annotated
from langgraph.graph.message import add_messages
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langchain_core.tools import tool, BaseTool
from langgraph.prebuilt import ToolNode, tools_condition
from langchain_mcp_adapters.client import MultiServerMCPClient
from binance.client import Client
import aiosqlite

load_dotenv()

# -------------------------------------------------------------
# Dedicated background event loop (needed because MCP tool
# loading, the checkpointer, and streaming are all async)
# -------------------------------------------------------------
_ASYNC_LOOP = asyncio.new_event_loop()
_ASYNC_THREAD = threading.Thread(target=_ASYNC_LOOP.run_forever, daemon=True)
_ASYNC_THREAD.start()


def _submit_async(coro):
    return asyncio.run_coroutine_threadsafe(coro, _ASYNC_LOOP)


def run_async(coro):
    return _submit_async(coro).result()


def submit_async_task(coro):
    """Schedule a coroutine on the backend event loop (used by the frontend for streaming)."""
    return _submit_async(coro)


# -------------------
# 1. LLM
# -------------------
model = ChatGroq(
    model='openai/gpt-oss-120b'
)

# -------------------
# 2. Tools
# -------------------

# Your API credentials
api_key = os.getenv("BINANCE_API_KEY")
api_secret = os.getenv("BINANCE_SECRET_KEY")
client = Client(api_key, api_secret)


@tool
def get_price(symbol: str):
    """Fetch latest price of cryptocurrency or stocks listed on Binance (spot or futures).
    For crypto (BTC, ETH, DOGE, MYX, etc.), the tool auto-converts to trading pairs (BTCUSDT, ETHUSDT, MYXUSDT).
    For stocks (IBM, GOOGLE, AAPL, NETFLIX, etc.), add 'B' suffix (IBMB, APPLEB, NFLXB) for spot trading.
    Works with both spot and futures markets."""

    original_symbol = symbol.upper()

    # Strategy: Try multiple symbol formats to find the price
    symbols_to_try = [
        original_symbol + 'USDT',  # Crypto format (BTCUSDT, MYXUSDT, ETHUSDT)
        original_symbol + 'B',      # Stock spot format (IBMB, APPLEB, NFLXB)
        original_symbol,             # Raw symbol fallback
    ]

    for symbol_attempt in symbols_to_try:
        try:
            ticker = client.get_symbol_ticker(symbol=symbol_attempt)
            price = float(ticker['price'])
            return f"Symbol: {symbol_attempt}, Price: {price}"
        except Exception:
            continue

    # If spot market fails, suggest the correct formats
    return f"Error: Symbol '{original_symbol}' not found on Binance spot market. \nTry these formats:\n- Crypto: {original_symbol}USDT (e.g., BTCUSDT, MYXUSDT, ETHUSDT)\n- Stocks: {original_symbol}B (e.g., IBMB, APPLEB, NFLXB)"


# Remote MCP tool (your fastmcp expense tracker server)
mcp_client = MultiServerMCPClient(
    {
        "expense_tracker": {
            "transport": "streamable_http",  # if this fails, try "sse"
            "url": "https://test-server-very-silver-koala.fastmcp.app/mcp"
        }
    }
)


def load_mcp_tools() -> list[BaseTool]:
    try:
        return run_async(mcp_client.get_tools())
    except Exception:
        return []


mcp_tools = load_mcp_tools()

tools = [get_price, *mcp_tools]

llm_with_tool = model.bind_tools(tools)


# -------------------
# 3. State
# -------------------
class ChatState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


graph = StateGraph(ChatState)


# -------------------
# 4. Nodes
# -------------------
async def chat_node(state: ChatState):
    messages = state['messages']

    system_msg = SystemMessage(content="""You are a helpful assistant with access to tools for fetching cryptocurrency/stock prices and for tracking expenses.

IMPORTANT INSTRUCTION:
- When the user asks about the price of ANY coin or stock (e.g., "What's the price of Bitcoin?", "Tell me SPCX stock price", "How much is ETH?"), you MUST use the get_price tool.
- The get_price tool can fetch prices from Binance. Pass the symbol (like BTC, ETH, DOGE, IBM, GOOGLE etc.) and the tool will handle the USDT conversion.
- When the user asks to add, list, or summarize expenses, use the expense tracker tool(s).
- For questions about prices, ALWAYS use the tool - don't try to guess or use outdated knowledge.
- For other questions (general knowledge, help, etc.), answer directly without tools.
- Be conversational and helpful.""")

    response = await llm_with_tool.ainvoke([system_msg] + messages)

    return {'messages': [response]}


tool_node = ToolNode(tools)

graph.add_node('chatbot', chat_node)
graph.add_node('tools', tool_node)

graph.add_edge(START, 'chatbot')
graph.add_conditional_edges('chatbot', tools_condition)
graph.add_edge('tools', 'chatbot')

# -------------------
# 5. Checkpointer (SQLite instead of Postgres)
# -------------------
async def _init_checkpointer():
    conn = await aiosqlite.connect(database="chatbot.db")
    return AsyncSqliteSaver(conn)

async def _init_checkpointer():
    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chatbot.db")
    conn = await aiosqlite.connect(database=db_path)
    return AsyncSqliteSaver(conn)


checkpointer = run_async(_init_checkpointer())

chatbot = graph.compile(checkpointer=checkpointer)


# -------------------
# 6. Helper
# -------------------
async def _alist_threads():
    all_threads = set()
    async for checkpoint in checkpointer.alist(None):
        all_threads.add(checkpoint.config['configurable']['thread_id'])
    return list(all_threads)


def retrieve_all_threads():
    return run_async(_alist_threads())

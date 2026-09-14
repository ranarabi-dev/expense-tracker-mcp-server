# Expense Tracker MCP Server

A remote MCP (Model Context Protocol) server, built with [FastMCP](https://github.com/jlowin/fastmcp), that lets an LLM agent add, list, and summarize personal expenses through natural language — plus a LangGraph + Streamlit chatbot that consumes it as a tool alongside a live crypto/stock price lookup.

## Why this project
I wanted to learn how to build and deploy a **remote** MCP server (HTTP transport, not just local stdio) and then wire it into a real agent — a LangGraph chatbot that decides on its own when to call the expense tools, when to reach for a different tool, and when to just answer directly.

## How it works

### MCP server (`server/`)
- Exposes **3 tools**:
  - `add_expense(date, amount, category, subcategory="", note="")` — inserts a new expense row
  - `list_expenses(start_date, end_date)` — returns expenses in an inclusive date range
  - `summarize(start_date, end_date, category=None)` — totals and counts expenses by category over a date range
- Exposes **1 resource**: `expense:///categories`, serving a nested category taxonomy (food, transport, housing, utilities, health, education, family & kids, entertainment, shopping, subscriptions, personal care, etc.) from `expense_categories.json`.
- **Storage:** SQLite via `aiosqlite`. The DB path currently lives in the system temp directory (`tempfile.gettempdir()`), so data does **not** persist across restarts/redeploys — fine for a demo, not for real use yet.
- **Transport:** streamable HTTP (`mcp.run(transport="http", host="0.0.0.0", port=8000)`). It's deployed at `https://test-server-very-silver-koala.fastmcp.app/mcp`, so any MCP client can connect over that URL instead of spawning the process locally.

### LangGraph client (`client_example/`)
- `langgraph_backend_MCP_tool_call.py` builds a LangGraph agent (Groq's `openai/gpt-oss-120b` via `ChatGroq`) with two tool sources:
  - the MCP expense-tracker tools above, pulled in live via `langchain_mcp_adapters`'s `MultiServerMCPClient` over `streamable_http`
  - a local `get_price` tool that fetches crypto/stock prices from Binance (included to test the agent routing between an MCP tool source and a plain local tool)
- A system prompt tells the model when to reach for `get_price` vs. the expense tools vs. answering directly; a `ToolNode` + `tools_condition` conditional edge handles the actual tool-calling loop.
- Conversation state is checkpointed to a local SQLite file (`AsyncSqliteSaver`), so chats can be resumed by thread ID.
- `langgraph_frontend_MCP_tool_call.py` is a Streamlit chat UI on top of the backend — multiple conversation threads in the sidebar, streamed assistant tokens, and a status indicator while a tool is running.

## Architecture
```
User → Streamlit UI → LangGraph agent (ChatGroq) ─┬─→ get_price tool → Binance API
                                                     └─→ MCP tools (streamable HTTP) → FastMCP server → SQLite (temp dir)
                       ← streamed response ──────────────────────────────────────────┘
```

## Project structure
```
server/
├── expense_tracker_remote.py   # MCP server entry point — tools, resource, DB init
├── expense_categories.json     # predefined category/subcategory taxonomy
├── pyproject.toml              # server dependencies (uv)
└── src/fastmcp_mcp_remote/     # scaffolded package (currently unused boilerplate)

client_example/
├── langgraph_backend_MCP_tool_call.py   # LangGraph agent + MCP/Binance tools + checkpointer
└── langgraph_frontend_MCP_tool_call.py  # Streamlit chat UI
```

## Running it

### MCP server
```bash
cd server
uv sync
uv run expense_tracker_remote.py
# or: uv run fastmcp run expense_tracker_remote.py
```
Runs at `http://0.0.0.0:8000/mcp`. Point any MCP-compatible client at that URL — or at the hosted URL above.

### LangGraph + Streamlit client
The `client_example/` folder doesn't ship its own `pyproject.toml`/`requirements.txt` yet, so install its dependencies manually:
```bash
cd client_example
pip install langgraph langchain-groq langchain-core langgraph-checkpoint-sqlite \
            langchain-mcp-adapters python-binance python-dotenv aiosqlite streamlit
```
Create a `.env` file with:
```
GROQ_API_KEY=...
BINANCE_API_KEY=...
BINANCE_SECRET_KEY=...
```
Then run the chat UI:
```bash
streamlit run langgraph_frontend_MCP_tool_call.py
```

## Tech Stack
Python 3.11+ · FastMCP · aiosqlite · LangGraph · LangChain · Groq (`ChatGroq`) · `langchain-mcp-adapters` · python-binance · Streamlit · uv

## What I'd improve
- Persistent storage for the MCP server — move the DB out of the OS temp directory
- Authentication on the HTTP MCP endpoint, since it's currently open
- Input validation (date formats, non-negative amounts, known categories)
- A `pyproject.toml`/`requirements.txt` for `client_example/` instead of manual install steps
- Tests for each MCP tool and the categories resource
- Clean up the unused `server/src/fastmcp_mcp_remote` scaffolding left over from `uv init`

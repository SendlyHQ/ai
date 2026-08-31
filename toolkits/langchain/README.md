# Sendly tools for LangChain

`sendly_tools.py` defines five LangChain tools on top of the official `sendly` package:
`send_sms`, `send_otp`, `check_otp`, `list_messages`, `get_balance`. Copy the file into your
project and import from it.

```bash
pip install sendly langchain langchain-anthropic
export SENDLY_API_KEY=sk_live_...
```

Start on a test key (`sk_test_...`) while you shape the prompt. Test keys simulate delivery
and never reach a handset.

## Wire it into an agent

```python
from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain_anthropic import ChatAnthropic
from langchain_core.prompts import ChatPromptTemplate

from sendly_tools import SENDLY_TOOLS

llm = ChatAnthropic(model="claude-sonnet-4-5-20250929")

prompt = ChatPromptTemplate.from_messages([
    (
        "system",
        "You can send SMS and verify phone numbers with Sendly. Phone numbers are "
        "always E.164, e.g. +14155552671. Only set messageType to 'transactional' "
        "for one-time passwords, alerts and receipts. Anything promotional stays "
        "marketing, even if that means it will not go out until quiet hours end.",
    ),
    ("human", "{input}"),
    ("placeholder", "{agent_scratchpad}"),
])

agent = create_tool_calling_agent(llm, SENDLY_TOOLS, prompt)
executor = AgentExecutor(agent=agent, tools=SENDLY_TOOLS)

result = executor.invoke(
    {"input": "Send a 6-digit login code to +14155552671 for the app Northwind."}
)
print(result["output"])
```

By default the tools build one `Sendly` client from `SENDLY_API_KEY` on first use. To supply
your own, for a different workspace or a longer timeout:

```python
from sendly import Sendly
from sendly_tools import set_client

set_client(Sendly(api_key, timeout=60.0, organization_id="org_..."))
```

## Argument names

Tool arguments are the Sendly wire names, so a model that has also read the REST docs stays
correct. That set is genuinely mixed: `send_sms` takes `messageType`, the verify tools take
`app_name`, `code_length` and `timeout_secs`. Leave it alone.

The single exception is the sender argument. `from` is a reserved word in Python, so it is
spelled `from_`, matching the Sendly Python SDK, and is sent as `from`.

## Errors come back as values

No tool raises on an API failure. A failed call returns
`{"ok": False, "error": ..., "message": ...}` plus any recovery fields the API supplied, so
the agent can decide what to do rather than blowing up the executor.

| `error` | What the model should do |
| --- | --- |
| `compliance_blocked` (`code: QUIET_HOURS_VIOLATION`) | The recipient's country is inside its quiet-hours window. `nextAllowedTime` is on the result. Say so, or resend as `transactional` if the content genuinely is. |
| `contact_opted_out` | The recipient texted STOP. Do not retry. |
| `insufficient_credits` | Top up. Stop sending. |
| `invalid_code` | Wrong OTP. `remaining_attempts` says how many guesses are left. |
| `expired` / `max_attempts_exceeded` | Send a fresh code instead of asking the user to retype. |

## Known narrowing

`list_messages` exposes `limit` only. The Python SDK's `messages.list()` forwards nothing
else to the API, so offering `offset`, `q` or `sandbox` here would silently drop them. If you
need search or pagination, use the MCP server or call
`GET /api/v1/messages` directly.

## When five tools is not enough

Numbers, campaigns, contacts, conversations, webhooks, templates, WhatsApp and 10DLC
registration are all reachable, but hand-wrapping them is the wrong move. Point the agent at
the MCP server, which exposes the full API surface:

- Hosted, no install: `https://mcp.sendly.live` (Streamable HTTP, Bearer auth with your API key)
- Local: `npx @sendly/mcp`

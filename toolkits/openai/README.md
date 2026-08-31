# Sendly tools for OpenAI function calling

`tools.json` is a ready-to-paste OpenAI `tools` array covering the five things an agent
actually needs from Sendly: send an SMS, send a one-time password, check that code, read
recent messages, read the credit balance.

The parameter names are the wire names. Whatever you send to the model comes straight back
out of the tool call and goes straight into the request body, with no renaming layer in
between. The API mixes conventions on purpose: `send_sms` takes `messageType` in camelCase
while the verify endpoints take `app_name`, `code_length` and `timeout_secs` in snake_case.
Do not normalise them.

## Tool to endpoint

| Tool | Request |
| --- | --- |
| `send_sms` | `POST /api/v1/messages` |
| `send_otp` | `POST /api/v1/verify` |
| `check_otp` | `POST /api/v1/verify/{verification_id}/check` |
| `list_messages` | `GET /api/v1/messages` |
| `get_balance` | `GET /api/v1/credits` |

Base URL is `https://sendly.live/api/v1`. Auth is `Authorization: Bearer sk_live_...` or
`sk_test_...`. An API key passed as a query parameter is rejected with `400 invalid_request`.

Start on a test key (`sk_test_...`) while you shape the prompt. Test keys simulate delivery
and never reach a handset.

## Dispatching a tool call

Every tool but `check_otp` passes its arguments through untouched, so the dispatcher is
close to mechanical.

```bash
pip install openai requests
export SENDLY_API_KEY=sk_test_...
```

```python
import json
import os
import uuid

import requests
from openai import OpenAI

BASE = "https://sendly.live/api/v1"
HEADERS = {"Authorization": f"Bearer {os.environ['SENDLY_API_KEY']}"}


def call_sendly_tool(name, args, call_id):
    # Idempotency-Key makes a retried send return the original result
    # instead of texting the recipient twice.
    write = {**HEADERS, "Idempotency-Key": call_id or str(uuid.uuid4())}

    if name == "send_sms":
        r = requests.post(f"{BASE}/messages", headers=write, json=args)
    elif name == "send_otp":
        r = requests.post(f"{BASE}/verify", headers=write, json=args)
    elif name == "check_otp":
        vid = args["verification_id"]
        r = requests.post(
            f"{BASE}/verify/{vid}/check", headers=HEADERS, json={"code": args["code"]}
        )
    elif name == "list_messages":
        r = requests.get(f"{BASE}/messages", headers=HEADERS, params=args)
    elif name == "get_balance":
        r = requests.get(f"{BASE}/credits", headers=HEADERS)
    else:
        return {"error": "unknown_tool", "message": name}

    return r.json()


client = OpenAI()
tools = json.load(open("tools.json"))["tools"]

messages = [{"role": "user", "content": "Text +14155552671 that their table is ready."}]
response = client.chat.completions.create(
    model="gpt-4o", messages=messages, tools=tools
)
choice = response.choices[0].message
messages.append(choice)

for call in choice.tool_calls or []:
    result = call_sendly_tool(
        call.function.name, json.loads(call.function.arguments), call.id
    )
    messages.append(
        {"role": "tool", "tool_call_id": call.id, "content": json.dumps(result)}
    )
```

Hand the error body back to the model rather than raising. Sendly returns a machine-readable
`error` field the model can act on, and the useful failures are recoverable:

| `error` | HTTP | What the model should do |
| --- | --- | --- |
| `compliance_blocked` (`code: QUIET_HOURS_VIOLATION`) | 400 | The recipient's country is inside its quiet-hours window. The body carries `nextAllowedTime`. Schedule for then, or resend as `transactional` if the content genuinely is. |
| `contact_opted_out` | 400 | The recipient texted STOP. Do not retry. |
| `insufficient_credits` | 402 | Top up. Stop sending. |
| `invalid_code` | 400 | Wrong OTP. `remaining_attempts` says how many guesses are left. |
| `expired` | 410 | The OTP window closed. Send a new one. |
| `max_attempts_exceeded` | 429 | Guess budget spent. Send a new code. |

## Responses API

`tools.json` is in the Chat Completions shape. For the Responses API, hoist each `function`
object to the top level:

```bash
jq '[.tools[] | .function + {type: "function"}]' tools.json
```

## When five tools is not enough

This set is deliberately small. Numbers, campaigns, contacts, conversations, webhooks,
templates, WhatsApp and 10DLC registration are all reachable, but wrapping them by hand is
the wrong move. Point the agent at the MCP server instead, which exposes the full API
surface and stays in step with it:

- Hosted, no install: `https://mcp.sendly.live` (Streamable HTTP, Bearer auth with your API key)
- Local: `npx @sendly/mcp`

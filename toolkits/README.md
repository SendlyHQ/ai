# Framework toolkits

Ready-to-paste Sendly tool definitions for agent frameworks. Copy the file for your
framework into your project. There is nothing to install from this directory.

| Directory | For | File to copy |
| --- | --- | --- |
| [`openai/`](openai/) | OpenAI function calling, and anything that consumes an OpenAI `tools` array | `tools.json` |
| [`langchain/`](langchain/) | LangChain, on the `sendly` PyPI package | `sendly_tools.py` |
| [`vercel-ai/`](vercel-ai/) | Vercel AI SDK, on the `@sendly/node` npm package | `sendly-tools.ts` |

## The same five tools everywhere

| Tool | Does | Request |
| --- | --- | --- |
| `send_sms` | Send one SMS | `POST /api/v1/messages` |
| `send_otp` | Send a one-time password | `POST /api/v1/verify` |
| `check_otp` | Check the code the user typed | `POST /api/v1/verify/{id}/check` |
| `list_messages` | Read recent messages and their delivery status | `GET /api/v1/messages` |
| `get_balance` | Read the credit balance | `GET /api/v1/credits` |

Five tools is the whole point. A model choosing between five well-described tools picks
correctly far more often than one choosing between a hundred, and these five cover the work:
tell someone something, prove they own a phone number, check what happened, check you can
afford it.

## Three things the definitions get right, and that hand-written ones tend not to

**Argument names are the wire names.** What the model emits is what the API receives. So the
set is inconsistent on purpose: `send_sms` takes `messageType` in camelCase while the verify
tools take `app_name`, `code_length` and `timeout_secs` in snake_case. That is what the API
does. Normalising it would break every call. The one forced exception is Python, where `from`
is a reserved word and the sender argument is spelled `from_`, exactly as the Sendly Python
SDK spells it.

**Quiet hours are per country, not global.** `messageType` defaults to `marketing`, which is
held outside the recipient country's quiet-hours window. That window is 9pm to 8am in the US
and 8pm to 9am in the UK, and differs again elsewhere. A tool description that states one
global window teaches the model to mislabel sends as `transactional` to get around a rule it
has been told wrongly. Transactional is for one-time passwords, alerts and receipts only.

**Failures are returned, not thrown.** A blocked send is information, not a crash. The
LangChain and Vercel tools resolve to `{ ok: false, error, message }` with whatever recovery
fields the API supplied, such as `nextAllowedTime` on a quiet-hours block or
`remaining_attempts` on a wrong code, so the agent can recover in the same turn. The OpenAI
README shows the equivalent dispatcher.

## When five tools is not enough

Numbers, campaigns, contacts, conversations, webhooks, templates, WhatsApp and 10DLC
registration are all reachable. Do not hand-wrap them. Point the agent at the MCP server,
which exposes the full API surface:

- Hosted, no install: `https://mcp.sendly.live` (Streamable HTTP, Bearer auth with your API key)
- Local: `npx @sendly/mcp`

## Keys

`sk_test_...` simulates delivery and never reaches a handset. Develop on a test key, then
switch to `sk_live_...`. Auth is `Authorization: Bearer <key>` against
`https://sendly.live/api/v1`. A key passed as a query parameter is rejected.

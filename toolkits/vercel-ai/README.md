# Sendly tools for the Vercel AI SDK

`sendly-tools.ts` defines five tools on top of the official `@sendly/node` package:
`send_sms`, `send_otp`, `check_otp`, `list_messages`, `get_balance`. Copy the file into your
project and import from it.

```bash
npm install ai @sendly/node zod
export SENDLY_API_KEY=sk_live_...
```

Start on a test key (`sk_test_...`) while you shape the prompt. Test keys simulate delivery
and never reach a handset.

## Wire it into an agent

`sendlyTools` is already keyed by tool name, so it drops straight into `tools`.

```ts
import { anthropic } from "@ai-sdk/anthropic";
import { generateText, stepCountIs } from "ai";

import { sendlyTools } from "./sendly-tools";

const result = await generateText({
  model: anthropic("claude-sonnet-4-5-20250929"),
  tools: sendlyTools,
  stopWhen: stepCountIs(5),
  system:
    "You can send SMS and verify phone numbers with Sendly. Phone numbers are " +
    "always E.164, e.g. +14155552671. Only set messageType to 'transactional' " +
    "for one-time passwords, alerts and receipts. Anything promotional stays " +
    "marketing, even if that means it will not go out until quiet hours end.",
  prompt: "Send a 6-digit login code to +14155552671 for the app Northwind.",
});

console.log(result.text);
```

Import the tools individually if you want a subset, for example `import { sendSms, getBalance }`.

By default the tools build one `Sendly` client from `SENDLY_API_KEY` on first use. To supply
your own, for a different workspace or a longer timeout:

```ts
import Sendly from "@sendly/node";
import { setClient } from "./sendly-tools";

setClient(new Sendly({ apiKey, timeout: 60000, organizationId: "org_..." }));
```

## Argument names

Tool arguments are the Sendly wire names, so a model that has also read the REST docs stays
correct. That set is genuinely mixed: `send_sms` takes `messageType`, the verify tools take
`app_name`, `code_length` and `timeout_secs`. The SDK spells its own options in camelCase, so
the tools translate at the call, which is the only place the two conventions meet.

## Repeated sends are safe

`send_sms` passes the AI SDK's `toolCallId` as the Sendly idempotency key. If the same tool
call is retried, after a network blip or a resumed stream, Sendly returns the original result
rather than texting the recipient a second time.

## Errors come back as values

No tool rejects on an API failure. A failed call resolves to
`{ ok: false, error, message }` plus any recovery fields the API supplied, so the model can
decide what to do rather than killing the run.

| `error` | What the model should do |
| --- | --- |
| `compliance_blocked` (`code: QUIET_HOURS_VIOLATION`) | The recipient's country is inside its quiet-hours window. `nextAllowedTime` is on the result. Say so, or resend as `transactional` if the content genuinely is. |
| `contact_opted_out` | The recipient texted STOP. Do not retry. |
| `insufficient_credits` | Top up. Stop sending. |
| `invalid_code` | Wrong OTP. `remaining_attempts` says how many guesses are left. |
| `expired` / `max_attempts_exceeded` | Send a fresh code instead of asking the user to retype. |

## AI SDK version

`sendly-tools.ts` targets AI SDK 5, where `tool()` takes `inputSchema`. On AI SDK 4 that one
field is called `parameters`, and nothing else in the file changes. The agent example above
is AI SDK 5 too: on 4, `stopWhen: stepCountIs(5)` is written `maxSteps: 5`.

## Known narrowing

`list_messages` exposes `limit` and `offset`. It does not expose the SDK's `status` option,
because `GET /api/v1/messages` does not read a `status` query parameter and the filter would
silently do nothing. For full-text search, use the MCP server or call the endpoint directly
with `?q=`.

## When five tools is not enough

Numbers, campaigns, contacts, conversations, webhooks, templates, WhatsApp and 10DLC
registration are all reachable, but hand-wrapping them is the wrong move. Point the agent at
the MCP server, which exposes the full API surface:

- Hosted, no install: `https://mcp.sendly.live` (Streamable HTTP, Bearer auth with your API key)
- Local: `npx @sendly/mcp`

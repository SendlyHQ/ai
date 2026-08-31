# Sendly, for agents

You are an AI agent integrating Sendly. Read this file end to end before you write
code. It is the canonical orientation. Everything here is true of the running API.

The tool list, the endpoint list and the per-country compliance rules live in
[`reference/`](./reference), which is generated from the Sendly source tree and
re-derived by a verifier that fails the build when a claim stops being true. This
file states no tool count, no endpoint count and no per-country window, because a
number written by hand is a number that goes stale. When you need one, open
`reference/`.

---

## 1. What Sendly is

A REST API for reaching phones. One credential and one base URL gets you:

- **SMS and MMS**, domestic and international
- **WhatsApp** and **RCS** as additional channels
- **Phone verification (OTP)**, send a code and check it
- **Conversations**, inbound replies threaded against contacts, with drafts and rules
- **Contacts and lists**, with carrier lookup and opt-out state
- **Campaigns, templates and batches** for bulk sending
- **Phone numbers**, search, buy, configure, and US 10DLC registration
- **Webhooks** for delivery status, inbound messages and lifecycle events

Base URL: `https://sendly.live/api/v1`

Every surface, MCP server, SDKs, CLI and raw REST, is a client of that one API.
None of them can do something the API cannot.

---

## 2. Authentication

Every request carries the key as a bearer token:

```
Authorization: Bearer $SENDLY_API_KEY
```

### Two kinds of key, and the difference is enormous

| Prefix | Type | What a send actually does |
| --- | --- | --- |
| `sk_test_v1_...` | Test | **Simulated.** Nothing reaches a handset, nothing is charged, no carrier is involved. The response shape is identical to live. |
| `sk_live_v1_...` | Live | Real delivery to a real phone, charged in credits. |

Do all integration work with a test key. Onboarding creates a default test key for
accounts that choose the test path, and one can be minted at any time from the
dashboard or with `POST /api/v1/account/keys`.

Creating a **live** key is gated: the account needs an approved sender first
(see [Going live](#10-going-live)). Getting `verification_required` when minting a
live key is the normal state of a fresh account, not a bug in your code.

### Getting a key

In order of preference:

1. **It already exists.** Look for `SENDLY_API_KEY` in the environment and in the
   project's `.env` before you do anything else. Do not mint duplicates.
2. **Ask the human.** <https://sendly.live/api-keys>, copy, paste into `.env`.
3. **Drive the device flow yourself.** `POST /api/cli/auth/device-code`, show the
   human the returned `verificationUrl`, poll `POST /api/cli/auth/token`, then
   exchange the session token for a real key at `POST /api/v1/account/keys`. The
   full sequence, including the poll interval and every terminal error, is written
   out at <https://sendly.live/auth.md>.

A newly minted key's raw value is returned exactly once. Write it to `.env`
immediately, confirm `.env` is gitignored, and put only the variable name in
`.env.example`. Never write a key into source.

### Confirm the key works

```bash
curl https://sendly.live/api/v1/credits \
  -H "Authorization: Bearer $SENDLY_API_KEY"
```

A JSON body with a `balance` field means you are authenticated.

### Scopes

Keys can be scoped. The scope names follow `resource:action`, for example
`sms:send`, `sms:read`, `verify:send`, `webhooks:write`, `contacts:read`. An
unscoped key carries full access. If a call returns a scope error, the key is
narrower than the endpoint requires; mint a new key rather than trying to widen
an existing one.

### Rate limits

Per key, per minute. Test keys are limited well below live keys, and enterprise
keys higher again. Do not hard-code a number: read the current limit off a response
that carries the headers. The tiers are documented at
<https://sendly.live/docs/rate-limits>.

A rate-limited response carries `X-RateLimit-Limit`, `X-RateLimit-Remaining` and
`X-RateLimit-Reset` (seconds). Most v1 endpoints are rate limited and set them, but
a minority are not and set nothing at all, so read the headers when they are there
and never write a client that requires them.

A 429 usually tells you how long to wait: the per-key limiter sets `Retry-After`,
and the per-number limit on verification codes returns `retryAfter` in the body.
Honour whichever you get. Do not retry immediately, and fall back to a backoff of
your own only when the response gives you neither.

---

## 3. The most common task, end to end

Send one message and confirm it. This is the whole loop.

```bash
curl -X POST https://sendly.live/api/v1/messages \
  -H "Authorization: Bearer $SENDLY_API_KEY" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: $(uuidgen)" \
  -d '{
        "to": "+15005550000",
        "text": "Your order has shipped",
        "messageType": "transactional"
      }'
```

`201 Created`. `+15005550000` is a sandbox destination, so that send is simulated
whichever key you used, and a simulated body is the short one: `id`, `to`, `from`,
`text`, `status`, `metadata`, and `simulated: true`. A send that really goes out
carries more, including `segments`, `creditsUsed`, `senderType` and `createdAt`. So
do not shape your parsing around a sandbox response, and check `simulated` before
you treat any response as a delivery.

Read it back:

```bash
curl https://sendly.live/api/v1/messages/{id} \
  -H "Authorization: Bearer $SENDLY_API_KEY"
```

The same call in Node:

```typescript
import Sendly from '@sendly/node';

const sendly = new Sendly(process.env.SENDLY_API_KEY!);

const message = await sendly.messages.send({
  to: '+15005550000',
  text: 'Your order has shipped',
  messageType: 'transactional',
});
```

Three things about that request are load-bearing, and each has its own section
below: `to` must be E.164, `messageType` must be set deliberately, and
`Idempotency-Key` is what stops a retry becoming a second charged send.

### `status: "delivered"` is not proof of delivery. Check `simulated`.

This is the single most common way an integration silently lies to its owner.

When a send is simulated, the response carries `simulated: true`. That happens
with a test key, with a sandbox destination, **and** when a live key belongs to an
account that is not yet authorised to send to that destination. In the third case
the response also carries `simulatedReason` explaining why, and `actionUrl`
pointing at what the human has to go and do.

The stored status is still `"delivered"`, for sandbox-test compatibility. So:

```typescript
if (message.simulated) {
  // Nothing reached a phone. Do not tell the user the message was sent.
}
```

Treat `simulated: true` on a live key as a configuration failure to surface, not
a success to log.

### Sending fails loudly in two cases

Silent simulation is the fallback, not the rule. Two situations produce a hard 403
instead, because simulating them would hide a real misconfiguration:

- `403 destination_not_authorized`, when an otherwise active account's approved
  sender does not cover that destination country. The response names
  `destinationCountry`.
- `403 sender_not_authorized`, when the request explicitly names a `from` number
  the workspace owns and that is active, but which is not authorised to send to
  that destination. A freshly bought US local number that has not yet been assigned
  to a registered 10DLC campaign is the usual cause.

Both mean a human has configuration to finish. Neither is retryable.

### Idempotency

A small set of write endpoints honour an idempotency key, sent as either
`Idempotency-Key` or `X-Idempotency-Key`. Sending, scheduling, batching, group MMS
and starting a verification are all covered. Number purchase is **not**, so never
build a naive retry loop around buying a number.

The exact endpoint list is at <https://sendly.live/docs/idempotency>. Use a fresh
UUID per logical operation, and reuse the same one on every retry of that
operation.

---

## 4. Phone numbers must be E.164

`+` followed by country code followed by the subscriber number, digits only.
`+15551234567`. Not `(555) 123-4567`, not `5551234567`, not `001555...`.

A malformed number is rejected before anything else happens, and the error code
depends on the endpoint. `POST /api/v1/messages` returns `400 invalid_request` with
the message `Invalid phone number format: ...`. `POST /api/v1/verify` returns
`400 invalid_phone_format`. Branch on the status and the endpoint rather than on one
shared code. Normalise on the way in, at the edge of your system, not at the call
site.

---

## 5. Verification and OTP

Two calls. Send a code, then check it.

```bash
curl -X POST https://sendly.live/api/v1/verify \
  -H "Authorization: Bearer $SENDLY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"to": "+15005550000"}'
```

`201` with `id`, `status: "pending"`, `phone` and `expires_at`.

**With a test key the response also contains `sandbox: true` and `sandbox_code`.**
That is the code to check, and it is how you test the whole flow without a handset.
It is never present on a live key.

```bash
curl -X POST https://sendly.live/api/v1/verify/{id}/check \
  -H "Authorization: Bearer $SENDLY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"code": "123456"}'
```

Success returns `status: "verified"` and `verified_at`. A wrong code returns
`400 invalid_code` with `remaining_attempts`, so show the human how many tries are
left rather than a generic failure. Attempts are capped, and the verification is
burned once exceeded; resend with `POST /api/v1/verify/{id}/resend` instead of
retrying past the cap.

Codes are also rate limited per destination phone number, independently of the
per-key limit, which is deliberate: it is what stops your integration being used to
harass someone. A `429` here means back off on that number, not on the API.

---

## 6. Sandbox testing

Two independent mechanisms. Use both.

**Test keys** simulate every send, to any destination.

**Sandbox destination numbers** simulate regardless of key type, and each one forces
a specific outcome. This is how you test error handling without waiting for a real
failure:

| Number | Outcome |
| --- | --- |
| `+15005550000` | Delivered |
| `+15005550001` | Failed, invalid phone number |
| `+15005550002` | Failed, cannot route to destination |
| `+15005550003` | Failed, queue full |
| `+15005550004` | Failed, rate limit exceeded |
| `+15005550006` | Failed, carrier violation |

Because a sandbox destination short-circuits even on a live key, these are safe to
leave in a test suite that runs with production credentials.

Opt-out checks and quiet-hours checks are skipped for test keys and sandbox
destinations. The restricted-content check is not: SHAFT screening runs on the text
of every send, sandbox included, so a blocked word fails identically in a test suite
and in production.

The two that are skipped are a trap: a message that sails through in the sandbox can
still be blocked in production. Read section 8 rather than assuming a green test run
means a green send.

---

## 7. What blocks a send: `messageType`

**If you omit `messageType`, it defaults to `marketing`.** Not transactional. The
strict default is deliberate.

The only two accepted values are `marketing` and `transactional`. Anything else, or
nothing at all, resolves to `marketing`, which means quiet hours apply and your
2am OTP does not go out.

- `transactional`: OTPs, receipts, shipping updates, appointment reminders, alerts.
  Not subject to quiet hours.
- `marketing`: anything promotional. Subject to quiet hours.

Set it explicitly on every send. This is the most common cause of a message that
"just did not arrive".

Do not reach for `transactional` as a way around quiet hours. If a message marked
transactional reads as promotional **and** the destination is currently inside its
quiet-hours window, the send is rejected with the matched keywords in the response.
Misclassification is also a real legal exposure for the account owner, and the API
returns a compliance warning on early transactional sends saying so.

---

## 8. What blocks a send: compliance

Each of these returns a structured error you can act on. Quiet hours and the
opt-out check run only on live sends to real destinations, so a test key or a
sandbox destination skips them. Restricted-content screening runs on every send.

### Quiet hours are per country, not global

**There is no single global quiet-hours window.** Each country has its own, defined
by its own regulator, and Sendly evaluates the window for the recipient's country in
the recipient's local time.

The United States and the United Kingdom have different windows. So do several other
countries. Any code or documentation that quotes one window as universal is wrong,
and quoting the US window as though it applied everywhere has already caused real
sends to be blocked unexpectedly.

**The authoritative per-country table is [`reference/compliance.md`](./reference/compliance.md).**
Read the window for the country you are actually sending to. Where a destination's
jurisdiction cannot be determined, a deliberately conservative fallback is applied
instead of a permissive one, so an unknown destination is more restricted, not
less.

A block returns `400 compliance_blocked` with code `QUIET_HOURS_VIOLATION`, and the
response tells you everything needed to recover: `recipientTimezone`,
`recipientLocalTime`, `quietHoursStart`, `quietHoursEnd` and `nextAllowedTime`.

The correct recovery is to schedule, not to retry:

```
POST /api/v1/messages/schedule
```

using the `nextAllowedTime` the error handed you.

### Restricted content

SHAFT content (sex, hate, alcohol, firearms, tobacco and cannabis) is checked on the
message text at send time, before a sender is resolved, so the check is not scoped
to a sender type and no key type or destination exempts it. The rejection is
`400 compliance_blocked` with code `SHAFT_CONTENT_DETECTED`, and it names the
`category` and the `matchedTerms`, so you can show the human exactly which words
tripped it rather than guessing.

### Opt-outs are enforced for you

When a recipient texts STOP, the contact is marked opted out automatically and a
`message.opt_out` webhook event fires. START opts them back in and fires
`message.opt_in`. You do not implement this, and you must not work around it.

Sending to an opted-out contact returns `400 contact_opted_out`. This is a
permanent state for that contact until they opt back in themselves. Never retry it,
never "fix" it by creating a duplicate contact record.

The standard opt-out keywords registered for US campaigns are STOP, STOPALL,
UNSUBSCRIBE, CANCEL, END and QUIT.

### Undeliverable and unsupported destinations

- A number already known to be a landline or otherwise unreachable returns
  `422 undeliverable_number`.
- A destination country Sendly does not serve returns `400 unsupported_destination`
  with the detected `country`.
- MMS is US and Canada only. An MMS to anywhere else returns `400 invalid_request`.

The countries Sendly serves, with their compliance rules, are in
[`reference/compliance.md`](./reference/compliance.md).
`GET /api/v1/countries` needs no auth and returns the live list with each
country's dial code, tier and credits per SMS, which is the right thing to call
when you need it at runtime rather than at authoring time.

---

## 9. Webhooks

Subscribe to delivery status, inbound messages and lifecycle events:

```bash
curl -X POST https://sendly.live/api/v1/webhooks \
  -H "Authorization: Bearer $SENDLY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
        "url": "https://example.com/webhooks/sendly",
        "events": ["message.delivered", "message.failed", "message.received"]
      }'
```

Every delivery is signed. The request carries `X-Sendly-Signature` in the form
`sha256=<hex>`, plus `X-Sendly-Timestamp`, `X-Sendly-Delivery`,
`X-Sendly-Event-Id` and `X-Sendly-Event-Type`.

Verify it by computing an HMAC-SHA256 over the string `{timestamp}.{raw body}`
using the webhook's secret, and comparing in constant time. **Use the raw request
body**, before any JSON parsing or re-serialisation, or the signature will never
match.

Deduplicate on `X-Sendly-Event-Id`. Delivery is at-least-once.

Subscribe with exact event-type strings. The current list is at
<https://sendly.live/docs/webhooks>, and the webhook endpoints themselves are in
[`reference/endpoints.md`](./reference/endpoints.md).

---

## 10. Going live

Live traffic needs a live key **and** an approved sender. The approval is a
human, business step that takes a few business days, and you cannot do it for your
user. Start it early and keep building against the test key in parallel.

Tell your user:

1. Verify the business at <https://sendly.live/verify> for a toll-free sender in
   the US and Canada, **or** buy and register a number at
   <https://sendly.live/numbers> for US 10DLC or an international sender.
2. Once approved, create a live key at <https://sendly.live/api-keys>.
3. Change `SENDLY_API_KEY` to the `sk_live_v1_...` value. **No code changes.**

If the swap is the only change and your integration still behaves oddly, check
`simulated` on the response first (section 3). That is almost always the answer.

---

## 11. Choosing a surface

- **Building into a codebase:** use the SDK for that language. See the README.
- **Adding capability to an agent or MCP client:** use the hosted MCP server at
  `https://mcp.sendly.live`, Streamable HTTP, `Authorization: Bearer <key>`. No
  install. `npx @sendly/mcp` is the same tool set over stdio if you need the traffic
  to originate locally.
- **Shell, scripts, CI:** `@sendly/cli`. Pass `--json`, and note it already switches
  to JSON automatically when stdout is not a TTY, so piped output is parseable
  without the flag.
- **A language with no SDK:** raw REST against <https://sendly.live/openapi.yaml>.

---

## 12. Where facts live

| You need | Go to |
| --- | --- |
| Per-country quiet hours and compliance rules | [`reference/compliance.md`](./reference/compliance.md) |
| Every MCP tool and its parameters | [`reference/mcp-tools.md`](./reference/mcp-tools.md) |
| Every REST endpoint, including webhooks | [`reference/endpoints.md`](./reference/endpoints.md) |
| Anything else generated from source | [`reference/`](./reference) |
| What an error means and how to recover | [`guides/handle-errors.md`](./guides/handle-errors.md) |
| Worked, task-shaped walkthroughs | [`guides/`](./guides) |
| Drop-in tool definitions for agent frameworks | [`toolkits/`](./toolkits) |
| The OpenAPI document | <https://sendly.live/openapi.yaml> |

`reference/` is generated from source and re-verified in CI. If this file and
`reference/` ever disagree, `reference/` is right and this file is a bug.

---

## 13. Rules for you

1. Never invent an endpoint, a parameter, a tool name or a limit. If it is not in
   `reference/` or the OpenAPI document, it does not exist.
2. Set `messageType` explicitly on every send.
3. Check `simulated` before reporting success.
4. Use E.164 everywhere.
5. Send an `Idempotency-Key` on every send, and reuse it across retries.
6. On 429, honour `Retry-After`.
7. On a quiet-hours block, schedule for `nextAllowedTime`. Do not loop.
8. On `contact_opted_out`, stop. That is the correct outcome.
9. Never commit a key. Never print one. `.env` only.
10. Build and test with `sk_test_v1_`. Going live is the human's step, not yours.

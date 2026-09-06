# Receive webhooks

Delivery outcomes, inbound replies and opt-outs, pushed to your server. This is the only way to
know what actually happened to a message: the send response tells you a message was accepted, not
that it arrived.

## 1. Register the endpoint

```bash
curl -X POST https://sendly.live/api/v1/webhooks \
  -H "Authorization: Bearer $SENDLY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://example.com/webhooks/sendly",
    "events": ["message.delivered", "message.failed", "message.received", "message.opt_out"],
    "mode": "all",
    "description": "production ingest"
  }'
```

`201 Created`:

```json
{
  "id": "whk_4c1f9e2b7a8d4e60b31f5c92d7a04e18",
  "url": "https://example.com/webhooks/sendly",
  "events": ["message.delivered", "message.failed", "message.received", "message.opt_out"],
  "description": "production ingest",
  "mode": "all",
  "is_active": true,
  "failure_count": 0,
  "circuit_state": "closed",
  "api_version": "2024-01",
  "metadata": {},
  "created_at": "2026-08-27T10:04:19.221Z",
  "updated_at": "2026-08-27T10:04:19.221Z",
  "total_deliveries": 0,
  "successful_deliveries": 0,
  "success_rate": 0,
  "last_delivery_at": null,
  "secret": "whsec_9f2c...e41a"
}
```

`secret` is `whsec_` followed by 64 hex characters. `last_failure_at` and `circuit_opened_at`
appear on this shape too, but only once they have a value; they are omitted, not `null`, on a
webhook that has never failed.

**`secret` is returned once and never again.** Store it before you do anything else. Without it you
cannot verify a single delivery, and your only recovery is rotation.

Needs `webhooks:write`. Reading needs `webhooks:read`.

Three constraints that will stop you:

- **A test key can only register test URLs**: localhost, 127.0.0.1, ngrok, loca.lt, webhook.site,
  requestbin, pipedream. Anything else is `400 test_key_restriction`. Your production endpoint has
  to be registered with a live key.
- **Production URLs must be HTTPS**, and must not resolve to a private or restricted network.
- **10 webhooks per account.** The eleventh is `400 limit_exceeded`.
- **`events` must be non-empty, and every name must be one of the types listed below.** An unknown
  or misspelled name fails the whole request with `400 validation_error`.

### `mode` decides which environment you hear about

| `mode` | Receives |
| --- | --- |
| `all` (default) | Every event, sandbox and production |
| `test` | Only sandbox events, where `livemode` is false |
| `live` | Only production events. Requires an approved sender, otherwise `403 verification_required` |

A webhook left on `test` goes silent the moment you go live. This is a common go-live failure. Use
`all` unless you have a reason not to.

## 2. Verify the signature

Every delivery carries these headers:

| Header | Value |
| --- | --- |
| `X-Sendly-Signature` | `sha256=<hex>` |
| `X-Sendly-Timestamp` | Unix seconds, part of the signed string |
| `X-Sendly-Delivery` | `del_<32 hex>`, unique per attempt |
| `X-Sendly-Event-Id` | Event id, stable across retries of the same event |
| `X-Sendly-Event-Type` | For example `message.delivered` |
| `X-Sendly-Api-Version` | `2024-01` |
| `User-Agent` | `Sendly-Webhooks/1.0` |

The signature is HMAC-SHA256 over the string `<timestamp>.<raw request body>`, keyed with your
webhook secret, hex encoded, prefixed with `sha256=`.

Two rules decide whether your verifier works: sign the **raw** body bytes exactly as received, not
a re-serialized copy of the parsed JSON, and compare in constant time.

Node, with Express:

```js
const crypto = require("crypto");
const express = require("express");

const app = express();
const SECRET = process.env.SENDLY_WEBHOOK_SECRET;

function verify(rawBody, signature, timestamp) {
  if (!signature || !timestamp) return false;
  if (Math.abs(Date.now() / 1000 - Number(timestamp)) > 300) return false;

  const expected =
    "sha256=" +
    crypto.createHmac("sha256", SECRET).update(`${timestamp}.${rawBody}`).digest("hex");

  const a = Buffer.from(signature);
  const b = Buffer.from(expected);
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}

app.post(
  "/webhooks/sendly",
  express.raw({ type: "application/json" }),
  (req, res) => {
    const raw = req.body.toString("utf8");
    if (!verify(raw, req.get("X-Sendly-Signature"), req.get("X-Sendly-Timestamp"))) {
      return res.sendStatus(401);
    }

    const event = JSON.parse(raw);
    // enqueue(event) and return immediately, do the work elsewhere
    res.sendStatus(200);
  },
);
```

Python, with Flask:

```python
import hmac, hashlib, os, time
from flask import Flask, request

app = Flask(__name__)
SECRET = os.environ["SENDLY_WEBHOOK_SECRET"]

def verify(raw_body: bytes, signature: str, timestamp: str) -> bool:
    if not signature or not timestamp:
        return False
    if abs(time.time() - int(timestamp)) > 300:
        return False
    expected = "sha256=" + hmac.new(
        SECRET.encode(), f"{timestamp}.".encode() + raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)

@app.post("/webhooks/sendly")
def sendly():
    raw = request.get_data()
    if not verify(raw, request.headers.get("X-Sendly-Signature"),
                  request.headers.get("X-Sendly-Timestamp")):
        return "", 401
    event = request.get_json()
    return "", 200
```

The 300 second tolerance is the recommended replay window. Nothing rejects an old timestamp on our
side, so this check is yours to enforce.

## 3. Answer fast, and dedupe

Return a 2xx as soon as you have stored the event. Any non-2xx, or no response within 30 seconds,
counts as a failure and starts the retry schedule.

Deliveries are at-least-once. Dedupe on `X-Sendly-Event-Id`, which is stable across retries of the
same event, rather than on `X-Sendly-Delivery`, which changes per attempt.

Event ids come in two shapes, and you should match on neither. Six event types get a deterministic
id, `evt_` plus 40 hex characters derived from the message id and the event type, so a second
dispatch of the same event reuses the same id: `message.sent`, `message.delivered`, `message.read`,
`message.failed`, `message.bounced` and `message.received`. **Every other event type, verification
and opt-out events included, carries a bare UUID with no prefix**, minted per dispatch. Compare the
whole value and store it.

## The payload

```json
{
  "id": "evt_9c31a7e40b2d5f8613ca94...",
  "type": "message.delivered",
  "api_version": "2024-01",
  "created": 1756289059,
  "livemode": true,
  "data": {
    "object": {
      "id": "0f1c9d2e-6b74-4c1a-9f0d-2b7c5e83a411",
      "organization_id": "8c41d0b7-25ae-4f39-b6d1-0e937a5c1284",
      "to": "+15551234567",
      "from": "SENDLY",
      "text": "Your order shipped",
      "status": "delivered",
      "direction": "outbound",
      "segments": 1,
      "credits_used": 2,
      "created_at": 1756289051,
      "delivered_at": 1756289059,
      "metadata": {},
      "message_format": "sms"
    }
  }
}
```

`created`, `created_at` and `delivered_at` are Unix seconds, not ISO strings. `livemode` is false
for anything simulated, including a live-key send that fell back to simulation. On a failure the
object adds `error` and `error_code`, where `error_code` is a classification such as `E001` or
`E018`. See [handle-errors.md](handle-errors.md).

An **inbound** message arrives as `message.received` with the same object shape and
`"direction": "inbound"`. `from` is the person who texted you, `to` is your number.

A **verification** event carries a different object:

```json
{
  "id": "d418a6f2-0c73-4b95-8e21-6a5b0937c4de",
  "type": "verification.verified",
  "api_version": "2024-01",
  "created": 1756289059,
  "livemode": true,
  "data": {
    "object": {
      "id": "ver_7c1f4b9a83d24e6fa0b512d7e93c4a18",
      "organization_id": "8c41d0b7-25ae-4f39-b6d1-0e937a5c1284",
      "phone": "+15551234567",
      "status": "verified",
      "delivery_status": "delivered",
      "attempts": 1,
      "max_attempts": 3,
      "expires_at": 1756289359,
      "verified_at": 1756289059,
      "created_at": 1756289051,
      "app_name": "Example",
      "template_id": "tpl_preset_otp"
    }
  }
}
```

An **opt-out** event carries the identity and the keyword, and nothing about a message:

```json
{
  "id": "5b90e7c1-2f48-4a6d-b013-8c72d4e5f9a6",
  "type": "message.opt_out",
  "api_version": "2024-01",
  "created": 1756289059,
  "livemode": true,
  "data": {
    "object": {
      "organization_id": "8c41d0b7-25ae-4f39-b6d1-0e937a5c1284",
      "phone_number": "+15551234567",
      "keyword": "STOP",
      "from_number": "+18885551234",
      "timestamp": "2026-08-27T10:04:19.221Z"
    }
  }
}
```

`organization_id` is present only when the account is bound to a workspace; it is omitted entirely
otherwise, rather than sent as `null`. On message and verification events it is always present and
is `null` when there is no workspace. `timestamp` here is an ISO string, unlike the Unix seconds
used by `created`, `created_at` and `delivered_at`.

Opt-out and opt-in events are always `livemode: true`, so a `test` mode webhook never sees them.

## Event types you can subscribe to

Messages: `message.sent`, `message.delivered`, `message.read` (WhatsApp and RCS only, SMS has no
read receipts), `message.failed`, `message.bounced`, `message.retrying`, `message.received`,
`message.opt_out`, `message.opt_in`.

Verification: `verification.created`, `verification.delivered`, `verification.verified`,
`verification.expired`, `verification.failed`, `verification.resent`,
`verification.delivery_failed`.

Conversations and drafts: `conversation.created`, `conversation.updated`, `draft.created`,
`draft.approved`, `draft.rejected`.

List health: `contact.auto_flagged`, `contact.marked_valid`, `contacts.lookup_completed`,
`contacts.bulk_marked_valid`.

10DLC lifecycle: `brand.verified`, `brand.failed`, `campaign.approved`, `campaign.rejected`,
`campaign.suspended`, `assignment.confirmed`, `assignment.failed`.

Numbers and porting: `port.completed`, `port_out.requested`, `port_out.completed`,
`port_out.rejected`, `port_out.cancelled`, `number.activated`, `number.failed`,
`number.requirements_required`, `number.released`.

WhatsApp: `whatsapp_account.connected`, `whatsapp_account.failed`, `whatsapp_template.approved`,
`whatsapp_template.rejected`, `whatsapp_template.paused`.

RCS registration (early access): `rcs_brand.verified` and `rcs_brand.failed` for the one-time
business check, then per agent `rcs_agent.testing` (branding approved, it can reach invited test
phones), `rcs_agent.live` (carrier launch done, it can reach everyone), `rcs_agent.rejected` (the
payload's `stage` is `basics` or `launch`, with a `reason`) and `rcs_agent.action_required` (Sendly
asked for changes before sending it on, with a `note`). Ids in these payloads are Sendly ids.

Calls (early access): `call.started` (answered), `call.completed` (ended — the payload's `status` is
`completed`, `cancelled`, `declined`, `no_answer`, `busy` or `failed`, with `duration_secs`), and
`call.recording.ready` (a recording can be fetched via `GET /api/calls/:id`).

Two honest caveats. `message.retrying`, `draft.created`, `draft.approved` and `draft.rejected` are
accepted on subscription but are not emitted by any current code path, so do not build a flow that
waits on them. And `GET /api/v1/webhooks/event-types` is shadowed by `GET /api/v1/webhooks/:id` in
the route table and answers `404 not_found`, so this list, not that endpoint, is the reference.

There is no `opt_out.created` event. The name is `message.opt_out`.

## Test it before you trust it

```bash
curl -X POST https://sendly.live/api/v1/webhooks/whk_4c1f.../test \
  -H "Authorization: Bearer $SENDLY_API_KEY"
```

This delivers a synthetic event of type `webhook.test` with `livemode: false`, signed exactly like a
real one, so it exercises your verifier end to end. Note that `webhook.test` is not a subscribable
event type: it only arrives through this endpoint.

Then inspect what happened:

```bash
curl "https://sendly.live/api/v1/webhooks/whk_4c1f.../deliveries?limit=20" \
  -H "Authorization: Bearer $SENDLY_API_KEY"
```

```json
{
  "deliveries": [
    {
      "id": "del_1a2b3c...",
      "event_type": "message.delivered",
      "status": "delivered",
      "response_status_code": 200,
      "response_time": 142,
      "response_body": "",
      "error_message": null,
      "error_code": null,
      "attempt_number": 1,
      "max_attempts": 6,
      "next_retry_at": null,
      "created_at": "2026-08-27T10:04:19.221Z",
      "delivered_at": "2026-08-27T10:04:19.363Z"
    }
  ],
  "pagination": { "limit": 20, "offset": 0 }
}
```

`error_code` on a failed delivery is one of `timeout`, `connection_error`, `http_error`,
`invalid_response`, `ssrf_blocked`, `circuit_open`, `max_retries_exceeded`, `webhook_disabled`,
`invalid_url`.

## Retries and the circuit breaker

A delivery gets up to 6 attempts. After each failure the next one is scheduled with a delay of, in
order, 0 seconds, 1 minute, 5 minutes, 30 minutes, 2 hours, 24 hours, measured from that failure.

**A 4xx response from your endpoint is not retried at all.** Only 5xx responses, timeouts and
connection failures are. This matters more than it looks: if your signature verifier returns 401
because you deployed the wrong secret, or 400 because you could not parse the body, the event is
gone. Return 5xx when your handler is broken and 2xx once you have the event stored, and never 4xx
for a problem on your side. Deliveries are also never retried after `webhook_disabled`,
`ssrf_blocked` or `invalid_url`.

Test deliveries (`POST /webhooks/{id}/test`) are sent once with no retry, so a failed test tells you
about your endpoint immediately.

If an endpoint fails 10 times within a 60 second window, its circuit opens and deliveries stop
being attempted for 5 minutes, after which it is probed again. `circuit_state` on the webhook shows
`closed`, `open` or `half_open`. The failure counter decays, so an endpoint that fails once an hour
never trips it.

To clear an open circuit after you have fixed the endpoint:

```bash
curl -X POST https://sendly.live/api/v1/webhooks/whk_4c1f.../reset-circuit \
  -H "Authorization: Bearer $SENDLY_API_KEY"
```

To replay deliveries you handled badly, give a time window rather than a single id:

```bash
curl -X POST https://sendly.live/api/v1/webhooks/whk_4c1f.../redeliver \
  -H "Authorization: Bearer $SENDLY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "since": "2026-08-27T00:00:00.000Z",
    "until": "2026-08-27T06:00:00.000Z",
    "statuses": ["failed"],
    "eventTypes": ["message.delivered"]
  }'
```

`since` defaults to 24 hours ago and `until` to now; the window cannot exceed 7 days. `statuses`
defaults to `["failed", "cancelled"]` and accepts `failed`, `cancelled`, `delivered`, `pending`.
`limit` defaults to 1000, capped at 10,000. The response reports `requeued`, `skipped`,
`truncated` and the `delivery_ids` it touched. Replay is refused with `409 circuit_open` while the
breaker is open, so reset the circuit first.

To recover events that were never delivered at all, for example because the endpoint was
unreachable during an outage, use `POST /api/v1/webhooks/{id}/backfill`. Preview what it would send
with `GET /api/v1/webhooks/{id}/recovery-preview` first.

## Rotating the secret

```bash
curl -X POST https://sendly.live/api/v1/webhooks/whk_4c1f.../rotate-secret \
  -H "Authorization: Bearer $SENDLY_API_KEY"
```

The response carries the new `secret`, once. **The new secret is used for the very next delivery.**
Despite the `grace_period_hours` field in the response, deliveries are signed with one secret only,
so any event in flight while you are deploying will fail verification. Deploy a verifier that
accepts both the old and the new secret first, then rotate, then remove the old one.

## Local development

A test key can register a webhook against a tunnel, so the loop is:

```bash
npx ngrok http 3000
curl -X POST https://sendly.live/api/v1/webhooks \
  -H "Authorization: Bearer $SENDLY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://<subdomain>.ngrok.io/webhooks/sendly", "events": ["message.delivered"], "mode": "all"}'
```

Then send to `+15005550000` with the test key. The simulated send fires a real, signed
`message.delivered` about half a second later, with `livemode: false`.

## Enterprise webhooks sign differently

If you also consume the account-level enterprise webhook (`/api/v1/enterprise/webhooks`), note that
it uses a different signature format: `X-Sendly-Signature: t=<timestamp>,v1=<hex>`, with
`X-Sendly-Enterprise: true` and no separate timestamp header. One verifier will not handle both.
The signed string is the same shape, `<timestamp>.<raw body>`.

## Next

- [handle-errors.md](handle-errors.md) for the `error_code` values on failed messages.
- [stay-compliant.md](stay-compliant.md) for what to do with `message.opt_out`.
- [go-live.md](go-live.md) for the webhook changes that going live requires.

# Send your first SMS

From an API key to a message you can look up by id. Do this with a test key first. The same
request works unchanged with a live key.

Base URL: `https://sendly.live`. Every request authenticates with a bearer token:
`Authorization: Bearer $SENDLY_API_KEY`. Keys are never accepted in a query string.

## 1. Confirm the key, and learn which kind it is

```bash
curl https://sendly.live/api/v1/account \
  -H "Authorization: Bearer $SENDLY_API_KEY"
```

```json
{
  "user": {
    "id": "3f9a2c74-8b16-4d5e-9a02-71c3e8f4d290",
    "email": "dev@example.com",
    "createdAt": "2026-08-01T09:12:44.000Z"
  },
  "organization": {
    "id": "8c41d0b7-25ae-4f39-b6d1-0e937a5c1284",
    "name": "Example Inc",
    "isPersonal": true
  },
  "credits": { "balance": 250, "reservedBalance": "0" },
  "verification": null,
  "apiKey": {
    "id": "a7e35190-4c2b-4d81-ae60-9f1b2c845d73",
    "name": "agent-integration",
    "type": "test",
    "scopes": ["sms:send", "sms:read"],
    "createdAt": "2026-08-20T14:03:51.000Z",
    "lastUsedAt": "2026-08-27T10:02:08.000Z"
  },
  "limits": { "messagesPerMinute": 60, "messagesPerDay": 100 }
}
```

`apiKey.type` is the fact that decides everything below: `test` or `live`.

Five things to know about this response.

`user.id`, `organization.id` and `apiKey.id` are bare UUIDs. None of them carries a prefix, so do
not pattern match on one. Prefixed ids do exist elsewhere in the API, on other resources:
verifications are `ver_<32 hex>`, webhooks `whk_<32 hex>`, webhook deliveries `del_<32 hex>`.

`organization` is `null` when the key is not bound to a workspace. Endpoints that need one, such as
numbers and 10DLC, then answer `400 workspace_required`.

`credits.balance` and `credits.reservedBalance` come back as numbers, except when the value is
zero, which is serialized as the string `"0"`. Coerce both before comparing.

`limits` is a static hint, not the enforced limit. `messagesPerMinute` is always 60 whatever the
key is, while the limiter actually enforces 60 a minute on a test key and 600 on a live one;
`messagesPerDay` reports 100 for a test key and 10000 for a live one and is not enforced anywhere.
Read `X-RateLimit-Limit` and `X-RateLimit-Remaining` for the real numbers.

`verification` is `null` until the account has an approved sender, which is what gates real
delivery, not the key by itself. When it is set it carries `status`, `type`, `region`,
`submittedAt` and `updatedAt`. See [go-live.md](go-live.md).

## 2. Send

`POST /api/v1/messages` needs `sms:send` scope, `to` in E.164, and `text`.

```bash
curl -X POST https://sendly.live/api/v1/messages \
  -H "Authorization: Bearer $SENDLY_API_KEY" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: first-send-2026-08-27-001" \
  -d '{
    "to": "+15005550000",
    "text": "Hello from Sendly",
    "messageType": "transactional"
  }'
```

The response is `201 Created`, not 200.

With a **test key** the send is simulated. No carrier traffic, no credits, no handset:

```json
{
  "id": "0f1c9d2e-6b74-4c1a-9f0d-2b7c5e83a411",
  "to": "+15005550000",
  "from": "SENDLY-TEST",
  "text": "Hello from Sendly",
  "status": "delivered",
  "metadata": {},
  "simulated": true
}
```

With a **live key on an authorized account**, the shape is wider and `status` is `queued`:

```json
{
  "id": "0f1c9d2e-6b74-4c1a-9f0d-2b7c5e83a411",
  "to": "+15551234567",
  "from": "SENDLY",
  "text": "Hello from Sendly",
  "status": "queued",
  "error": null,
  "segments": 1,
  "creditsUsed": 2,
  "senderType": "number_pool",
  "createdAt": "2026-08-27T10:04:19.221Z",
  "metadata": {},
  "senderNote": "Message will be sent from a toll-free number in your number pool."
}
```

`status` in the body is the status at the moment the row was created. A real send is handed to the
carrier after that, so the body says `queued` even when the handoff succeeded. Do not treat the
response `status` as the delivery outcome. Read it back, or subscribe to
[webhooks](webhooks.md).

### The one field that decides whether it was real

`simulated: true` means nothing reached a handset. A test key always gets it. A **live** key also
gets it when the account is not yet authorized to send to that destination, and then the response
carries a reason:

```json
{
  "status": "delivered",
  "simulated": true,
  "simulatedReason": "Verification pending or not approved",
  "actionUrl": "/verify"
}
```

That is a `201`, not an error. If your integration asserts on HTTP status alone it will report a
successful delivery that never happened. Assert on `simulated` too.

## 3. Read it back

```bash
curl https://sendly.live/api/v1/messages/0f1c9d2e-6b74-4c1a-9f0d-2b7c5e83a411 \
  -H "Authorization: Bearer $SENDLY_API_KEY"
```

```json
{
  "id": "0f1c9d2e-6b74-4c1a-9f0d-2b7c5e83a411",
  "to": "+15005550000",
  "from": "SENDLY-TEST",
  "text": "Hello from Sendly",
  "status": "delivered",
  "error": null,
  "errorCode": null,
  "retryCount": 0,
  "segments": 1,
  "creditsUsed": 0,
  "isSandbox": true,
  "createdAt": "2026-08-27T10:04:19.221Z",
  "deliveredAt": "2026-08-27T10:04:19.744Z",
  "message_format": "sms"
}
```

`status` moves through `queued`, `sent`, `delivered`, and can end at `failed`, `bounced`, or
`retrying`. A simulated send to a succeeding sandbox number is written `delivered` immediately, and
the matching `message.delivered` webhook fires about half a second after the call returns; a
simulated send to one of the failing sandbox numbers is written `failed` and fires nothing. On a
failure, `errorCode` carries a stable classification code (`E001` invalid number, `E003` landline,
`E018` number not registered for A2P sending, and so on) alongside a human readable `error`.

To list instead of fetching one: `GET /api/v1/messages?limit=50`. A test key sees only sandbox
messages there, whatever you pass.

## What a test key does differently

| | Test key `sk_test_v1_...` | Live key `sk_live_v1_...` |
| --- | --- | --- |
| Sends | Always simulated, `simulated: true` | Real, unless the account is not yet authorized |
| Cost | 0 credits, never charged | Charged per segment, per destination |
| Sender | `SENDLY-TEST` | Your approved sender or number pool |
| Rate limit | 60 requests per minute per key | 600 per minute per key |
| `GET /messages` | Sandbox messages only | Production, add `?sandbox=true` for sandbox |
| Webhook targets | Test URLs only (localhost, ngrok, webhook.site and similar) | Any HTTPS URL |
| OTP | Returns the code in `sandbox_code` | Delivers the code by SMS |
| Buying numbers, 10DLC registration, WhatsApp, RCS | Refused with `live_key_required` or a `*_requires_live_key` error | Allowed |

Sends to a test key are simulated for **any** destination number, not just the sandbox numbers
below.

## Sandbox numbers

These six destinations are simulated for any key, including a live one, so you can exercise failure
handling without spending credits:

| Number | Simulated result |
| --- | --- |
| `+15005550000` | Delivered |
| `+15005550001` | Failed, "Invalid phone number" |
| `+15005550002` | Failed, "Cannot route to destination" |
| `+15005550003` | Failed, "Queue full, try again later" |
| `+15005550004` | Failed, "Rate limit exceeded" |
| `+15005550006` | Failed, "Carrier violation" |

## Segments and credits

The send path counts one segment per 160 characters of `text`. Credits are charged per segment, at
the destination country's rate: 2 credits for US and Canada, 8, 12, 16, 24 or 48 for other tiers.
One credit is one US cent. A simulated send is charged 0.

## Field names that actually matter

- `to` must be a string in E.164, for example `+15551234567`. Anything else is `invalid_request`.
- `text` is the message body. Not `body`, not `message`.
- `from` is optional. Omit it and the account's default sender is used. Supply one your workspace
  does not own and the handling splits on the destination:
  - **US or Canada**: a hard `400 invalid_from_number`. Nothing is sent.
  - **International**: the `from` is ignored, the account's verified sender ID is used instead, and
    the send proceeds. The `201` comes back with `senderType: "alphanumeric"` and a `warning`
    reading "The 'from' field is ignored for international messages. Your verified sender ID
    '<id>' will be used."

  A `+1` number you *do* own is substituted the same way for a non-NANP destination, because a NANP
  long code cannot terminate there. That also arrives as a `warning` on a `201`, not an error. So
  read `warning` on every send that names a `from`: it is the only signal that the message went out
  from a different sender than you asked for.
- `messageType` is camelCase and takes `marketing` or `transactional`. **If you omit it, or send
  `message_type`, the message is treated as marketing**, which subjects it to quiet hours. Read
  [stay-compliant.md](stay-compliant.md) before choosing.
- `metadata` is a JSON object, maximum 4KB serialized. It is echoed back on the message and on
  webhook payloads.
- `channel` defaults to `sms`. Only `sms`, `whatsapp` and `rcs` are accepted; a typo such as
  `"SMS"` is refused with `channel_not_supported` rather than being sent as SMS.

## Retrying safely

Set `Idempotency-Key` (or `X-Idempotency-Key`) on the original request and reuse the same value on
the retry. The first completed response is replayed for 24 hours with `Idempotency-Replayed: true`
instead of sending and charging again. Keys are 1 to 255 printable ASCII characters, and are scoped
per account and per endpoint.

Two details worth knowing. The record is written when the first request *completes*, so two
identical requests genuinely in flight together can both send: space retries out. And 4xx responses
are recorded too, so use a fresh key when you want a failed request to genuinely run again.

Idempotency is honoured on `POST /api/v1/messages`, `/messages/batch`, `/messages/group`,
`/messages/schedule`, `/verify`, `/whatsapp/signup`, `/whatsapp/templates` and
`/enterprise/workspaces/provision`. It is not honoured anywhere else, including
`POST /api/v1/numbers/buy`.

## Sending to many recipients

`POST /api/v1/messages/batch` takes up to 10,000 recipients:

```bash
curl -X POST https://sendly.live/api/v1/messages/batch \
  -H "Authorization: Bearer $SENDLY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Your order shipped",
    "messageType": "transactional",
    "messages": [
      { "to": "+15005550000" },
      { "to": "+15005550001" }
    ]
  }'
```

A shared `text` applies to every entry; a per entry `text` overrides it. Recipients who have opted
out or are known undeliverable are filtered out before sending, and the batch is refused with
`all_opted_out` or `all_recipients_blocked` if that empties it. The response is `202` while
processing, `201` when complete. Poll `GET /api/v1/messages/batch/{id}`.

Batch requests dedupe on the payload even without an `Idempotency-Key` header, so an HTTP timeout
retry of the same intent will not double send.

## Sending an image (MMS)

MMS is US and Canada only and is enabled per account, so a `feature_disabled` response here means
the capability is off, not that the request was wrong. Upload first, then reference the returned
URL. Media URLs must come from this endpoint; arbitrary URLs are rejected. Maximum 10 per message.

```bash
curl -X POST https://sendly.live/api/v1/media \
  -H "Authorization: Bearer $SENDLY_API_KEY" \
  -F "file=@receipt.png"
```

The `201` response carries `id`, `url`, `contentType` and `sizeBytes`. Pass that `url` back
verbatim, and only that URL:

```bash
curl -X POST https://sendly.live/api/v1/messages \
  -H "Authorization: Bearer $SENDLY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "to": "+15005550000",
    "text": "Your receipt",
    "mediaUrls": ["<the url returned by POST /api/v1/media>"]
  }'
```

Accepted formats are JPEG, PNG and GIF, checked by content rather than by filename.

## Next

- Something came back that you did not expect: [handle-errors.md](handle-errors.md).
- Know before you send whether the content or the timing will be blocked:
  [stay-compliant.md](stay-compliant.md).
- Get delivery outcomes and replies pushed to you: [webhooks.md](webhooks.md).
- Make the sends real: [go-live.md](go-live.md).

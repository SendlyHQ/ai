# Go live

Moving from a test key to real delivery. The short version: swapping the key is the last step, not
the first, and a live key on an unapproved account does not fail loudly, it simulates.

## The failure this guide exists to prevent

Swap `SENDLY_API_KEY` to a live key before the account has an approved sender and your sends keep
returning `201`:

```json
{
  "id": "0f1c9d2e-6b74-4c1a-9f0d-2b7c5e83a411",
  "to": "+15551234567",
  "from": "SENDLY-TEST",
  "text": "Your order shipped",
  "status": "delivered",
  "simulated": true,
  "simulatedReason": "Verification pending or not approved",
  "actionUrl": "/verify"
}
```

`status` says `delivered`. Nothing reached a handset. The only field that tells you is `simulated`.

Make your integration assert on it before you ever go live:

```
if (response.simulated) throw new Error(response.simulatedReason ?? "simulated send");
```

There are two cases where a live send fails loudly instead of simulating, and they are worth
knowing because they mean the opposite of a silent problem: on an account that is not yet
authorized to send there, naming a `from` returns `400 invalid_from_number` when the workspace does
not own it and `403 sender_not_authorized` when it does, for any destination; and an account
authorized for one region texting outside it returns `403 destination_not_authorized`.

Both of those replace a simulation. Once the account *is* authorized, an unowned `from` is a hard
`400 invalid_from_number` only for US and Canada; for an international destination it is ignored,
the verified sender ID is used instead, and the substitution is reported in `warning` on a `201`.
See [send-first-sms.md](send-first-sms.md).

## What actually gates a live send

Three things, checked in this order on every send:

1. **A live key.** Creating one requires the account to already have an approved sender **and** a
   credit balance above zero. So the key is a consequence of being approved, not a route to it.
2. **An approved sender for that destination.** This is the real gate. What counts differs by
   region, see below.
3. **Credits to cover it.** 2 credits for US and Canada per segment, 8 to 48 elsewhere. Below the
   balance you get `402 insufficient_credits` before anything is sent.

Check where you stand:

```bash
curl https://sendly.live/api/v1/account \
  -H "Authorization: Bearer $SENDLY_API_KEY"
```

The three parts of that response that matter here, from a body that also carries `user`,
`organization` and `limits` (see [send-first-sms.md](send-first-sms.md) for the whole thing):

```json
{
  "credits": { "balance": 500, "reservedBalance": "0" },
  "verification": {
    "status": "verified",
    "type": "toll_free",
    "region": "US",
    "submittedAt": "2026-08-14T11:02:07.000Z",
    "updatedAt": "2026-08-21T08:44:19.000Z"
  },
  "apiKey": {
    "id": "a7e35190-4c2b-4d81-ae60-9f1b2c845d73",
    "name": "production",
    "type": "live",
    "scopes": ["sms:send", "sms:read"],
    "createdAt": "2026-08-21T09:15:02.000Z",
    "lastUsedAt": "2026-08-27T10:02:08.000Z"
  }
}
```

A non-zero credit balance is a number; a zero comes back as the string `"0"`, which is why
`reservedBalance` reads `"0"` above. Coerce before comparing.

`verification: null` means no sender has been approved yet. `verification.status` of `verified` or
`approved` is what unlocks live sending and live key creation. Anything else, including any
in-review state, is not yet live. Do not attempt to parse the intermediate states: treat "not
`verified` and not `approved`" as "not ready", and confirm readiness with a real send that comes
back without `simulated`.

## US and Canada need registration. This is not optional and not instant.

Sending to a US or Canadian number requires a registered sender. There are two routes, and neither
is fully self-serve.

**Route A, toll-free verification.** The account owner submits the business details in the
dashboard at `https://sendly.live/verify`. This cannot be done with an API key: the submission
endpoints are session authenticated by design. A carrier reviews the submission, which typically
takes a few business days and can come back with changes requested. Once it is approved, live
sending to US and Canada works with no code change.

**Route B, a US local number under 10DLC.** Register a brand, register a campaign under it, buy or
port a local number, and assign the number to the campaign. This route *is* API driven, but every
step has a gate:

```bash
curl -X POST https://sendly.live/api/v1/tendlc/brands \
  -H "Authorization: Bearer $SENDLY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "legalName": "Example Inc",
    "entityType": "PRIVATE_PROFIT",
    "ein": "12-3456789",
    "vertical": "TECHNOLOGY",
    "website": "https://example.com"
  }'
```

```bash
curl -X POST https://sendly.live/api/v1/tendlc/campaigns \
  -H "Authorization: Bearer $SENDLY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "brandId": "<brand id>",
    "useCase": "MIXED",
    "description": "Order updates and occasional offers for existing customers",
    "messageFlow": "Customers opt in at checkout by ticking a box that names message frequency and rates",
    "sampleMessages": [
      "Example Inc: your order #1042 shipped, track it at https://example.com/t/1042",
      "Example Inc: 10% off this week for members. Reply STOP to opt out"
    ]
  }'
```

```bash
curl -X POST https://sendly.live/api/v1/tendlc/campaigns/<campaign id>/assign \
  -H "Authorization: Bearer $SENDLY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"phoneNumber": "+15551234567"}'
```

What to expect from this route:

- Every write here requires a **live key**. A test key gets `403 live_key_required`. Registration
  is never simulated.
- The key must be bound to a workspace, otherwise `400 workspace_required`.
- A `404 not_found` from any `/api/v1/tendlc/*` path means 10DLC self serve is not enabled for that
  account, not that the path is wrong. That is an account level switch, so ask rather than retry.
- Brand registration charges a one-time setup fee at submission.
- Brand verification and campaign approval are **reviewed**, not instant. Poll
  `GET /api/v1/tendlc/brands/{id}` and `GET /api/v1/tendlc/campaigns/{id}`, or subscribe to
  `brand.verified`, `brand.failed`, `campaign.approved`, `campaign.rejected` and
  `assignment.confirmed`.
- A campaign can land in an internal review queue before it is submitted onward. In that state the
  campaign carries a `reviewNote` explaining what to change, and fixing and resubmitting from that
  state is free. Resubmitting after a carrier rejection is charged and capped.
- The number must already be in the workspace before you can assign it, otherwise
  `404 number_not_found`.

Until a number is assigned to an active campaign, it is owned but unregistered, and the send gate
keeps it closed. That is deliberate: an unregistered US local number is blocked by carriers, so
failing at our gate is better than paying for a message that dies downstream with `E018`.

## International sending

Outside US and Canada the sender is an alphanumeric sender ID or an owned number on the account's
messaging profile, and international activation is much faster than toll-free verification.

The one thing that will bite you: **73 destination countries require the sender ID to be registered
in that country before any message will go through.** Sending to one of them without registration
returns:

```json
{
  "error": "registration_required",
  "code": "SENDER_ID_NOT_REGISTERED",
  "message": "India requires sender ID registration before you can send messages. Your sender ID \"EXMPLE\" is not yet registered for this country. Registration typically takes 15+ business days.",
  "countryCode": "IN",
  "countryName": "India",
  "registrationUrl": "/contact?category=Registration&country=India&senderId=EXMPLE"
}
```

15 or more business days is not a number you can engineer around. Find out which of your
destinations need it before you promise a launch date.

`GET /api/v1/countries` needs no auth and returns `code`, `name`, `dial_code`, `tier`,
`credits_per_sms`, `price_usd`, `requires_registration` and `alpha_sender` per entry, plus a
`tiers` summary and `total_countries`. **It is a curated sample, not the full table**: it lists 51
countries, 19 of them flagged `requires_registration`, against 243 priced countries and 73 that
require registration. A destination missing from that response is not evidence that it needs no
registration, and its `tiers` counts describe only the 51 it returns. Treat it as a dropdown
source, and confirm a specific destination by attempting a send and reading the
`registration_required` response.

Two more international notes. A NANP number cannot terminate to a non-NANP destination, so if you
name a `+1` number as `from` for an international send, the sender ID is substituted and the
response tells you so in `warning`. And an international-only account texting US or Canada is
refused with `destination_not_authorized` rather than simulated, because that is a scope error on
an otherwise active account.

## What is self-serve and what is not

| Step | Self-serve? | Typical time |
| --- | --- | --- |
| Test key | Yes, created with the account | Instant |
| Buying credits | Yes | Instant |
| Creating a live key | Yes, once verification is approved and the balance is above zero | Instant |
| Toll-free verification (US and Canada) | No, dashboard submission plus carrier review | A few business days |
| 10DLC brand registration | API, but reviewed and charged | Not instant |
| 10DLC campaign approval | API, but reviewed, and may pass through an internal queue first | Not instant |
| Assigning a number to a campaign | API | Minutes after approval |
| International sender activation | Faster than toll-free | Varies |
| Sender ID registration (73 countries) | No | 15 or more business days |
| Buying a number | API with a live key, where the capability is enabled | Minutes, longer where documents are required |

## The switch itself

Once `verification.status` reads `verified` or `approved`:

1. Create the live key.

```bash
curl -X POST https://sendly.live/api/v1/account/keys \
  -H "Authorization: Bearer $SENDLY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name": "production", "type": "live"}'
```

The response's `key` field is the only time the raw key is shown. Write it to the environment
immediately. `403 verification_required` means the account is not approved yet;
`402 credits_required` means the balance is zero.

2. Swap `SENDLY_API_KEY`. No code changes: the request and response shapes are identical.

3. Send one real message to a number you control and confirm the response has **no** `simulated`
   field, then confirm `GET /api/v1/messages/{id}` reaches `delivered`.

## Things that do not carry over from test

- **Webhooks.** A webhook created with a test key can only point at a test URL (localhost, ngrok,
  webhook.site and similar). Your production endpoint must be registered with a live key. Also
  check `mode`: `test` only receives sandbox events, `live` only production events, `all` receives
  both. A webhook left on `test` mode goes quiet the moment you go live.
- **Sandbox message history.** Test keys read sandbox messages only, live keys read production. The
  old test messages do not appear in production listings.
- **The OTP `sandbox_code` field.** It disappears on a live key. Any code path that reads it will
  break.
- **Rate limits.** They rise from 60 to 600 requests per minute per key, so a throttle tuned to the
  test limit is now leaving throughput on the table. Read `X-RateLimit-Limit` rather than hardcoding.
- **Cost.** Every send now charges credits, and quiet hours now block marketing sends that were
  silently allowed in simulation.

## Pre-launch checklist

1. `GET /api/v1/account` shows `verification.status` of `verified` or `approved`.
2. Credit balance covers your expected volume plus headroom.
3. A live key exists and is in the environment, and the test key is out of production config.
4. One real send returned no `simulated` field and reached `delivered`.
5. Production webhooks are registered with the live key, on `all` or `live` mode, and signature
   verification is deployed. See [webhooks.md](webhooks.md).
6. `messageType` is set explicitly on every send. See [stay-compliant.md](stay-compliant.md).
7. A `message.opt_out` handler writes to your own suppression list.
8. Your retry policy distinguishes 429 and 5xx from the 4xx codes that will never succeed. See
   [handle-errors.md](handle-errors.md).
9. For US and Canada: the sender is toll-free verified, or the local number is assigned to an
   active campaign.
10. For international: none of your destinations are in the 73 registration countries, or the
    registration is already in flight.

# Verify a phone number

The full one-time-passcode round trip: send a code, check what the user typed, handle the four ways
a check can fail. Sendly generates, stores and compares the code, so your application never holds
it.

Two scopes are involved: `verify:send` for the send, resend and check calls, `verify:read` for
status lookups.

## 1. Send the code

```bash
curl -X POST https://sendly.live/api/v1/verify \
  -H "Authorization: Bearer $SENDLY_API_KEY" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: signup-4821-otp" \
  -d '{
    "to": "+15005550000",
    "app_name": "Example",
    "code_length": 6,
    "timeout_secs": 300
  }'
```

`201 Created`. With a **test key** the SMS is not sent and the code is handed straight back:

```json
{
  "id": "ver_7c1f4b9a83d24e6fa0b512d7e93c4a18",
  "status": "pending",
  "phone": "+15005550000",
  "expires_at": "2026-08-27T10:09:19.000Z",
  "sandbox": true,
  "sandbox_code": "418205",
  "message": "Sandbox mode: SMS not sent. Use the sandbox_code to verify."
}
```

With a **live key** the SMS goes out and there is no `sandbox_code`:

```json
{
  "id": "ver_7c1f4b9a83d24e6fa0b512d7e93c4a18",
  "status": "pending",
  "phone": "+15005550000",
  "expires_at": "2026-08-27T10:09:19.000Z",
  "sandbox": false
}
```

Store `id`. It is the handle for every later call, and it is the only thing you need to keep: never
store the code.

### Body fields

All optional except `to`.

| Field | Default | Range |
| --- | --- | --- |
| `to` | required | E.164, for example `+15551234567` |
| `code_length` | 6 | 4 to 10, clamped |
| `timeout_secs` | 300 | 60 to 3600, clamped |
| `app_name` | the account's brand name, else `App` | interpolated into the message body |
| `template_id` | `tpl_preset_otp` | must be a preset or a published template |
| `profile_id` | none | a saved verify profile supplying the defaults above |

Out of range numbers are clamped rather than rejected, so `code_length: 99` quietly becomes 10.
Send the value you want.

A verify profile (`/api/v1/verify/profiles`) is the way to fix `code_length`, `timeout_secs`,
`max_attempts`, an allowed country list and a webhook URL once, instead of repeating them on every
call. An explicit field in the request still wins over the profile.

## 2. Check the code the user typed

```bash
curl -X POST https://sendly.live/api/v1/verify/ver_7c1f4b9a83d24e6fa0b512d7e93c4a18/check \
  -H "Authorization: Bearer $SENDLY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"code": "418205"}'
```

Success is `200`:

```json
{
  "id": "ver_7c1f4b9a83d24e6fa0b512d7e93c4a18",
  "status": "verified",
  "phone": "+15005550000",
  "verified_at": "2026-08-27T10:05:41.882Z"
}
```

Only `status: "verified"` means the number is proven. Do not infer success from the HTTP code
alone.

### The four ways a check fails

| HTTP | `error` | What happened | What to do |
| --- | --- | --- | --- |
| 400 | `invalid_code` | Wrong code, budget left | Re-prompt. The body carries `remaining_attempts` |
| 429 | `max_attempts_exceeded` | Guess budget spent | Start a new verification, do not retry this one |
| 410 | `expired` | Past `expires_at` | Resend (step 3) or start a new verification |
| 404 | `not_found` | No such id for this account | Check the id, and that the key belongs to the same workspace |
| 400 | `invalid_request` | `code` missing or not a string | Send `{"code": "123456"}` |

The default budget is 3 attempts, and it counts wrong guesses only: a correct code refunds the
attempt it consumed. Concurrent checks cannot exceed the budget, so a double submitted correct code
verifies once rather than racing to a failure.

Checking an already verified id with the original correct code returns the same `verified` payload
again. Checking it with any other code returns `invalid_code`, so a replay can never read as a
fresh success.

## 3. Resend, but only when the API says you can

```bash
curl -X POST https://sendly.live/api/v1/verify/ver_7c1f4b9a83d24e6fa0b512d7e93c4a18/resend \
  -H "Authorization: Bearer $SENDLY_API_KEY"
```

Resend is allowed in exactly two states: the verification expired, or it is still pending and
delivery failed. Anything else returns `400 invalid_state`, and the message names the current
state. A resend issues a **new** code, resets the expiry and resets `attempts` to 0, so any code
the user already has stops working.

If the user simply did not receive it and the verification is still pending and delivered, the
correct move is a fresh `POST /api/v1/verify`, not a resend.

Resend returns `200`, with `sandbox_code` again on a test key.

## 4. Poll the status when you need it

```bash
curl https://sendly.live/api/v1/verify/ver_7c1f4b9a83d24e6fa0b512d7e93c4a18 \
  -H "Authorization: Bearer $SENDLY_API_KEY"
```

```json
{
  "id": "ver_7c1f4b9a83d24e6fa0b512d7e93c4a18",
  "status": "pending",
  "phone": "+15005550000",
  "delivery_status": "sent",
  "attempts": 1,
  "max_attempts": 3,
  "expires_at": "2026-08-27T10:09:19.000Z",
  "verified_at": null,
  "created_at": "2026-08-27T10:04:19.000Z",
  "sandbox": false,
  "app_name": "Example",
  "template_id": "tpl_preset_otp",
  "profile_id": null
}
```

`status` is `pending`, `verified`, `expired` or `failed`. `delivery_status` tracks the SMS
separately: `queued`, `sent`, `delivered` or `failed`. A pending verification that is past its
expiry is flipped to `expired` by this call.

Prefer webhooks over polling. Subscribe to `verification.created`, `verification.verified`,
`verification.expired`, `verification.failed`, `verification.resent` and
`verification.delivery_failed`. See [webhooks.md](webhooks.md).

## Rate limits specific to OTP

On top of the per key request limit, sends are throttled per destination and per account:

- 5 per phone number per 10 minutes
- 20 per phone number per day
- 100 per account per minute

Exceeding any of them returns `429 rate_limit_exceeded` with a `retryAfter` in seconds. This applies
to resends as well.

## What changes when you go live

- The code is delivered by SMS and `sandbox_code` disappears. Your code must not depend on it.
- The account needs an approved sender, otherwise the send is refused with
  `403 verification_required`. See [go-live.md](go-live.md).
- Each send costs credits at the destination country's rate, and is refused with
  `402 insufficient_credits` when the balance will not cover it.
- OTP messages are sent as transactional, so quiet hours do not apply to them.
- The code never appears in your message history: the stored copy of the body is redacted, and only
  a hash of the code is kept.

## Full round trip, end to end

```bash
ID=$(curl -sS -X POST https://sendly.live/api/v1/verify \
  -H "Authorization: Bearer $SENDLY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"to": "+15005550000"}' | jq -r .id)

curl -sS -X POST "https://sendly.live/api/v1/verify/$ID/check" \
  -H "Authorization: Bearer $SENDLY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"code": "418205"}'
```

## Next

- Error codes and their recovery: [handle-errors.md](handle-errors.md).
- Push verification outcomes to your server: [webhooks.md](webhooks.md).
- Turn on real delivery: [go-live.md](go-live.md).

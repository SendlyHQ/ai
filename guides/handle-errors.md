# Handle errors

The errors you will actually hit, and the correct recovery for each. Recovery matters more than
meaning here: several of these look retryable and are not, and one of them is not an error at all.

## The envelope

```json
{
  "error": "insufficient_credits",
  "message": "This message requires 8 credits (tier1 rate). Current balance: 3",
  "creditsNeeded": 8,
  "currentBalance": 3,
  "tier": "tier1",
  "pricePerSms": 0.08
}
```

`error` is the machine readable code. Branch on it, never on `message`. Some responses add a
`code` field carrying a SCREAMING_CASE constant that is more specific than `error`, and many add
context fields you can act on directly, such as `nextAllowedTime` or `remaining_attempts`.

Two exceptions to know about:

- The transactional/marketing mismatch response (below) has a `code` but **no** `error` field.
  Reading `error` blindly gives you `undefined` on a real 400.
- A `201` with `simulated: true` is a success envelope describing a message that never reached a
  handset. Treat it as a failure of your go live setup. See
  [send-first-sms.md](send-first-sms.md).

## Codes that do not exist

Some older Sendly material documents `quiet_hours_violation`, `content_violation`, `opted_out` and
an `opt_out.created` webhook event. None of them exist in the API. Matching on them means
your handler silently falls through to a default branch. The real ones are `compliance_blocked`
(with `code: "QUIET_HOURS_VIOLATION"` or `code: "SHAFT_CONTENT_DETECTED"`), `contact_opted_out`,
and the webhook event `message.opt_out`.

## Authentication and authorization

| HTTP | `error` | Recovery |
| --- | --- | --- |
| 401 | `unauthorized` | No `Authorization` header. Send `Authorization: Bearer $SENDLY_API_KEY` |
| 401 | `invalid_auth_format` | Header is not `Bearer <token>`. Fix the format, do not retry as is |
| 401 | `invalid_key_format` | Token does not start with `sk_test_` or `sk_live_`. You are probably holding a session token, not an API key |
| 401 | `invalid_api_key` | Unknown, inactive, expired or mistyped key. Deliberately indistinguishable between those cases. Mint a new key rather than guessing |
| 400 | `invalid_request` | You put the key in a query parameter. Keys are refused there so they cannot leak into logs. Move it to the header |
| 403 | `insufficient_permissions` | The key is valid but lacks a scope. The message names the missing scopes. Mint a key that has them: sends need `sms:send`, lookups `sms:read`, OTP `verify:send` and `verify:read`, webhooks `webhooks:read` and `webhooks:write` |

None of these are retryable. Fix the credential.

## Rate limits

| HTTP | `error` | Recovery |
| --- | --- | --- |
| 429 | `rate_limit_exceeded` | Wait, then retry the identical request |

Limits are per key per minute: 60 on a test key, 600 on a live key, 3000 on enterprise. Responses
carry `X-RateLimit-Limit`, `X-RateLimit-Remaining` and `X-RateLimit-Reset` (seconds).
Self throttle on `X-RateLimit-Remaining` rather than waiting to be rejected. On a 429, honour
`Retry-After`, or the `retryAfter` field in the body, instead of retrying immediately.

Do not trust `limits.messagesPerMinute` from `GET /api/v1/account` for this: it is a static hint
that does not vary by key type. The headers are the enforced value.

OTP sends have their own throttles on top: 5 per phone per 10 minutes, 20 per phone per day, 100
per account per minute, all returned as the same `rate_limit_exceeded` with a `retryAfter`.

## Sender and destination

These are the errors that mean "this account cannot send that message to that number yet". None of
them are fixed by retrying.

| HTTP | `error` | Meaning | Recovery |
| --- | --- | --- | --- |
| 403 | `destination_not_authorized` | The account's sender is not approved for that destination country, for example an international only setup texting US or Canada | Complete the sender approval for that region. See [go-live.md](go-live.md) |
| 403 | `sender_not_authorized` | You named a `from` the workspace owns, but it is not authorized for this destination yet | If the number was just assigned to a campaign, retry in a few minutes. Otherwise assign it |
| 400 | `invalid_from_number` | The `from` is not owned by this workspace, is inactive, or is not registered to send. Raised for a US or Canadian destination, and for any destination while the account is not yet authorized to send at all | Omit `from` to use the account default, or use a number from `GET /api/v1/numbers` |
| 403 | `registration_required` (`SENDER_ID_NOT_REGISTERED`) | 73 destination countries require the sender ID to be registered first | The account owner registers the sender for that country. The response carries `countryCode`, `countryName` and a `registrationUrl`. Registration takes 15 or more business days, so route around it meanwhile |
| 400 | `unsupported_destination` | The destination country is not supported at all | Do not retry. The body carries the detected `country` |
| 403 | `verification_required` | Live OTP send, or live key creation, on an account with no approved sender | Human step. See [go-live.md](go-live.md) |
| 403 | `live_key_required` | Buying a number, registering a 10DLC brand or campaign, or assigning a number, attempted with a test key | Use a live key. These actions are never simulated |

A live key that is not yet authorized does **not** produce any of these for a plain send with no
`from`. It returns `201` with `simulated: true` and a `simulatedReason` instead.

An unowned `from` on an **authorized** account sending **internationally** produces no error at
all: the `from` is dropped, the verified sender ID is used, and the substitution is reported in
`warning` on the `201`. Only US and Canada treat it as `invalid_from_number`. If you rely on
`from`, assert on `warning`, not just on the status code.

## Compliance blocks

| HTTP | `error` / `code` | Meaning | Recovery |
| --- | --- | --- | --- |
| 400 | `compliance_blocked` / `SHAFT_CONTENT_DETECTED` | Content matched the restricted content filter | Rewrite. The body names the `category` and the `matchedTerms`. Do not attempt to obfuscate the wording |
| 400 | `compliance_blocked` / `QUIET_HOURS_VIOLATION` | A marketing message into the recipient's quiet hours | The body carries `nextAllowedTime`. Schedule for it with `POST /api/v1/messages/schedule`, or correct `messageType` if the message really is transactional |
| 400 | no `error` field, `code: "TRANSACTIONAL_MARKETING_MISMATCH"` | Marked transactional, but the body reads as promotional, and it is currently quiet hours at the destination | Remove the promotional wording, or mark it `marketing` and schedule it. The body lists `matchedKeywords` |
| 400 | `contact_opted_out` | The recipient texted STOP | Never work around this. Remove them from the audience |
| 422 | `undeliverable_number` (`undeliverable_landline`, `undeliverable_invalid`, `undeliverable_non_sms`) | The number previously hard bounced and is flagged | Stop sending. If the flag is wrong, `POST /api/v1/contacts/{id}/mark-valid` clears it |

Relabelling a marketing message as transactional to get past quiet hours is exactly what the
mismatch check exists to catch. Read [stay-compliant.md](stay-compliant.md).

## Billing

| HTTP | `error` | Recovery |
| --- | --- | --- |
| 402 | `insufficient_credits` | The account tops up. The body carries `creditsNeeded` and `currentBalance`. Not retryable until the balance changes |
| 402 | `credits_required` | Creating a live key on an account with a zero balance. Buy credits first |

If a send is accepted and then fails at the carrier, the credits are refunded automatically and the
message ends `failed` with an `errorCode`.

## Batches

| HTTP | `error` | Recovery |
| --- | --- | --- |
| 400 | `batch_too_large` | Maximum 10,000 messages per batch. Split it |
| 400 | `all_opted_out` | Every recipient has opted out. `optedOutCount` tells you how many. Clean the list |
| 400 | `all_recipients_blocked` | Everyone is opted out or undeliverable. `optedOutCount` and `invalidCount` split it |
| 403 | `not_verified` | Batch sending on an unverified account with a live key |
| 403 | `workspace_suspended` | The workspace is suspended. Contact the workspace administrator |

A batch that is only partly blocked still sends. The blocked recipients are filtered out silently,
so reconcile against the batch result rather than assuming every input was sent.

## Idempotency

| HTTP | `error` | Recovery |
| --- | --- | --- |
| 400 | `invalid_idempotency_key` | The key is over 255 characters or contains non printable or non ASCII characters. Use a UUID. An empty header is ignored rather than rejected, and the request then runs undeduplicated |
| 422 | `idempotency_key_mismatch` | This key was already used with a different body. Keys bind to the body they first ran with. Use a fresh key for a different request, and reuse a key only to retry the identical one |

A replayed response arrives with `Idempotency-Replayed: true` and the original status code, which
may itself be a 4xx. Use a new key when you want a previously failed request to genuinely run
again.

## Not found, and the shape of a wrong id

| HTTP | `error` | Recovery |
| --- | --- | --- |
| 404 | `not_found` | The id does not exist **or** belongs to another workspace. The two are deliberately indistinguishable. Check the id, and check the key is for the right workspace |
| 400 | `workspace_required` | The key is not bound to a workspace. Numbers and 10DLC endpoints need one |

A 404 on `/api/v1/numbers/*`, `/api/v1/tendlc/*` or `/api/v1/rcs/*` usually means the capability is
not enabled for that account, not that the path is wrong. Those routes are hidden rather than
returning a 403 when the feature is off.

One known routing gap: `GET /api/v1/webhooks/event-types` is shadowed by `GET /api/v1/webhooks/:id`
and answers `404 not_found`. Use the event list in [webhooks.md](webhooks.md) instead.

## Server side and transient

| HTTP | `error` | Recovery |
| --- | --- | --- |
| 500 | `internal_error` | Retry with the same `Idempotency-Key`. 5xx responses are deliberately not recorded for idempotency, precisely so the retry can execute |
| 500 | `delivery_failed` | The OTP SMS could not be handed to the carrier. The verification is marked failed. Start a new one |
| 500 | `configuration_error` | The account is missing a messaging profile. Human setup step, not retryable |
| 502 | `search_failed` | Number search upstream failed. Retry |
| 503 | (number buy) | Transient pricing or search failure during a purchase. Retryable, unlike a `buy_failed` |

## Failure codes on a delivered message

A message that was accepted and then failed downstream carries a classification in `errorCode`,
visible on `GET /api/v1/messages/{id}` and as `error_code` in the `message.failed` webhook.

| Code | Meaning | Recovery |
| --- | --- | --- |
| `E001` | Invalid phone number | Permanent. The contact is auto flagged |
| `E002` | Phone unreachable | Permanent |
| `E003` | Landline | Permanent. The contact is auto flagged |
| `E004` | Carrier blocked | Permanent |
| `E005` | Carrier rejected | Permanent |
| `E006` | Opted out | Permanent. Remove from the audience |
| `E009` | Rate limited | Transient, retried automatically |
| `E010` | Network timeout | Transient, retried automatically |
| `E011` | Insufficient credits | Top up, then resend |
| `E012` | Compliance violation | Rewrite the content |
| `E013` | Flagged as spam | Rewrite the content |
| `E014` | Carrier queue full | Transient, retried automatically |
| `E015` | Daily spend limit reached | Raise the limit on the account |
| `E016` | Carrier temporarily unavailable | Transient, retried automatically |
| `E017` | No sender available for this destination | The account has no usable sender. See [go-live.md](go-live.md) |
| `E018` | Number not registered for A2P sending | Register the number under a 10DLC campaign |
| `E019` | 10DLC campaign or brand suspended | Account level problem, contact support |
| `E021` | 10DLC daily message limit reached | Wait for the daily reset |
| `E023` | URL blocked by carrier | Replace the link. Public shorteners are the usual cause |

Transient codes are retried for you when durable retry is enabled for the account, and the message
sits in `retrying` while that happens. Do not resend a message in `retrying`: you will duplicate it.

## A retry policy that is correct

```
401, 403, 400, 402, 404, 422  -> do not retry, fix the request or the account
429                           -> wait Retry-After, retry identical
5xx, 502, 503                 -> retry with the same Idempotency-Key, exponential backoff
201 with simulated: true      -> not a delivery, check go-live setup
```

## Next

- [stay-compliant.md](stay-compliant.md) for the blocks you can avoid before you send.
- [go-live.md](go-live.md) for the account level errors.
- [webhooks.md](webhooks.md) for failures that arrive after the response.

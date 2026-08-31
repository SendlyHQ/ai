# Stay compliant

Four things get messages blocked: the hour you send at, how you classify the message, what the
message says, and whether the recipient asked you to stop. All four are enforced server side before
anything reaches a carrier, so getting them right up front is cheaper than handling the rejection.

## The default that surprises people

`messageType` is optional on `POST /api/v1/messages`. Omitting it does **not** mean "unclassified".
Anything that is not exactly `transactional` is treated as **marketing**, which is the stricter
path, and marketing is subject to quiet hours.

Two ways to hit this by accident:

```json
{ "to": "+447700900123", "text": "Your code is 4821" }
```

```json
{ "to": "+447700900123", "text": "Your code is 4821", "message_type": "transactional" }
```

The first omits the field. The second uses snake_case, which the API does not read, so it is
ignored. Both send as marketing. The field is `messageType`, camelCase, and it takes exactly
`marketing` or `transactional`.

## Quiet hours are per country, not global

Quiet hours are evaluated against the **recipient's** local time, derived from their country, and
for US and Canada numbers from the area code's timezone. The window is different in different
countries. There is no single global window, and assuming the US window is the most common way to
get this wrong: a send that is legal at 05:44 in the United States is blocked in the United
Kingdom, whose window is wider at both ends.

Some countries add day restrictions on top: no marketing on Sundays, no marketing on Saturdays, or
an earlier Saturday cutoff.

For the per country table, including the countries that differ and the ones with day restrictions,
see [../reference/compliance.md](../reference/compliance.md). Do not hardcode a window in your
integration. If the destination country cannot be determined from the number, the send is blocked
under a conservative default rather than allowed.

A blocked marketing send returns:

```json
{
  "error": "compliance_blocked",
  "code": "QUIET_HOURS_VIOLATION",
  "message": "United Kingdom: Message would arrive at 5:00 local time (quiet hours: 20:00-09:00)",
  "recipientTimezone": "Europe/London",
  "recipientLocalTime": "2026-08-27T05:44:11.000Z",
  "quietHoursStart": "20:00",
  "quietHoursEnd": "09:00",
  "nextAllowedTime": "2026-08-27T09:00:00.000Z",
  "suggestion": "Schedule this message for 2026-08-27T09:00:00.000Z using POST /api/v1/messages/schedule, or mark as transactional if this is not promotional content."
}
```

The correct recovery is in the response. Take `nextAllowedTime` and schedule:

```bash
curl -X POST https://sendly.live/api/v1/messages/schedule \
  -H "Authorization: Bearer $SENDLY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "to": "+447700900123",
    "text": "Two seats left on the Saturday tour",
    "messageType": "marketing",
    "scheduledAt": "2026-08-27T09:00:00.000Z"
  }'
```

The field is `scheduledAt`, an ISO 8601 timestamp, at least 5 minutes and at most 5 days in the
future. Outside that range you get `invalid_scheduled_time`. Note that scheduling requires an
approved sender even on a test key: without one it returns `403 not_verified`.

Transactional messages skip the quiet hours check entirely, at any hour, in every country.

## Transactional is a claim about content, and it is checked

Marking a promotional message `transactional` to get past quiet hours is the abuse this rule
exists to prevent, so during quiet hours the content of a transactional message is checked against
a marketing language filter. If it reads as promotional, the send is refused:

```json
{
  "code": "TRANSACTIONAL_MARKETING_MISMATCH",
  "message": "Message contains marketing language but was marked as transactional. ...",
  "matchedKeywords": ["limited time", "% off"],
  "suggestion": "Change messageType to 'marketing' or remove promotional content. Transactional messages should only contain order confirmations, OTPs, appointment reminders, or account alerts.",
  "reason": "Message appears to be promotional but was marked as transactional during quiet hours. This may be an attempt to bypass quiet hours restrictions."
}
```

Note the shape: HTTP 400 with a `code` and **no** `error` field.

Outside quiet hours the check does not run, and your classification is trusted. That is deliberate,
not a loophole to lean on: the classification is still your legal claim.

Transactional means order confirmations, one-time passcodes, appointment reminders, account alerts,
delivery notifications. It does not mean "important to us". Anything advertising, promoting,
discounting or inviting is marketing.

## Restricted content

Message bodies are screened for SHAFT categories: sex, hate, alcohol, firearms, tobacco and
cannabis. These cannot be sent over toll-free or alphanumeric senders at all.

```json
{
  "error": "compliance_blocked",
  "code": "SHAFT_CONTENT_DETECTED",
  "message": "Content contains restricted alcohol reference: \"happy hour drinks\"",
  "category": "alcohol",
  "matchedTerms": ["happy hour drinks"],
  "suggestion": "Remove restricted content and try again. SHAFT content (Sex, Hate, Alcohol, Firearms, Tobacco/Cannabis) is prohibited on toll-free and alphanumeric sender IDs."
}
```

The response names the `category` and the exact `matchedTerms`. Rewrite around them. Do not
obfuscate the wording to slip past the filter: carriers run their own screening downstream, and a
message that gets past Sendly and is blocked there still costs you and can put the sender at risk.

The screen runs on the message body. For an OTP, the random digits are excluded before screening,
so a code can never accidentally trip a rule, but your `app_name` and template text are fully
checked.

## Opt-out handling

Opt-outs are enforced for you, and you cannot send through one.

**Inbound STOP is automatic.** When a recipient replies STOP, STOPALL, UNSUBSCRIBE, CANCEL, END or
QUIT, the carrier answers them, the contact is marked opted out on your account, and a
`message.opt_out` webhook fires. START, YES and UNSTOP opt them back in and fire `message.opt_in`.
You do not implement any of this, and you must not disable it.

**Sending to an opted-out contact is refused**, on live sends:

```json
{
  "error": "contact_opted_out",
  "message": "Recipient +15551234567 has opted out of messages. Cannot send."
}
```

There is no override. Remove them from the audience.

**Batches filter before sending.** Opted-out and known-undeliverable recipients are dropped from a
batch silently, and the batch is refused with `all_opted_out` or `all_recipients_blocked` if that
leaves nothing. Reconcile against the batch result rather than assuming every input was sent.

**Record an opt-out you collected elsewhere.** If someone unsubscribes through your own UI or
support desk, push it to Sendly so the suppression is enforced on every future send:

```bash
curl -X POST https://sendly.live/api/v1/contacts/opt-out \
  -H "Authorization: Bearer $SENDLY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"phone_number": "+15551234567"}'
```

```json
{
  "phone_number": "+15551234567",
  "opted_out": true,
  "contact_id": "b2d4f8a1-3c67-4e90-8f21-5a0c7d9e4b13"
}
```

Needs `contacts:write`. The number must be E.164.

**React to opt-outs in your own systems.** Subscribe to `message.opt_out` and `message.opt_in`. The
payload carries `phone_number`, the `keyword` they sent, and the `from_number` they sent it to. If
your CRM only learns about opt-outs from your own UI, it will drift out of sync with the carrier.
See [webhooks.md](webhooks.md).

Note the event name: `message.opt_out`. There is no `opt_out.created` event.

## Consent comes before all of this

None of the above is consent. Quiet hours, content screening and opt-out enforcement are the floor,
not the requirement. Marketing messages need prior express written consent from the recipient in
most jurisdictions, and every country entry in
[../reference/compliance.md](../reference/compliance.md) records which consent model applies and
which regulation governs it.

If you are building the collection flow, the practical requirements are: the recipient opts in
themselves, the disclosure they agree to states message frequency and that rates may apply, and the
opt-out instruction is present. That disclosure has to match what you actually send: a consent form
that promises order updates does not cover a promotion.

## A checklist before your first live marketing send

1. `messageType` is set explicitly on every request, camelCase.
2. Transactional is used only for order confirmations, passcodes, reminders and alerts.
3. Marketing sends either respect the destination's quiet hours or are scheduled to
   `nextAllowedTime`.
4. Nothing in your templates touches a SHAFT category.
5. A `message.opt_out` webhook handler writes back to your own suppression list.
6. Consent for every number in your audience is recorded, with the disclosure text they agreed to.

## Next

- [../reference/compliance.md](../reference/compliance.md) for the per country rules.
- [handle-errors.md](handle-errors.md) for the recovery on each block.
- [webhooks.md](webhooks.md) for opt-out and delivery events.

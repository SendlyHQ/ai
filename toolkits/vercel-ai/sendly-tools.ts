/**
 * Sendly tools for the Vercel AI SDK.
 *
 * Five tools built on the official `@sendly/node` package: send an SMS, send a
 * one-time password, check that code, read recent messages, read the credit
 * balance.
 *
 * Tool argument names are the Sendly wire names, so what the model emits is
 * what the API receives. That means the set is deliberately inconsistent:
 * `send_sms` takes `messageType` in camelCase while the verify tools take
 * `app_name`, `code_length` and `timeout_secs` in snake_case. The API is
 * inconsistent there, and hiding it would only mislead a model that has also
 * read the REST docs.
 *
 * Tools resolve to an object and never throw on an API error. A blocked send or
 * a wrong OTP is information the agent can act on, so it comes back as
 * `{ ok: false, error, message }` plus whatever recovery fields the API
 * supplied, such as `nextAllowedTime` or `remaining_attempts`.
 */

import { tool } from "ai";
import Sendly, { SendlyError } from "@sendly/node";
import { z } from "zod";

let client: Sendly | undefined;

/** Use a pre-built Sendly client for every tool in this module. */
export function setClient(next: Sendly): void {
  client = next;
}

/** Return the shared Sendly client, building one from SENDLY_API_KEY. */
export function getClient(): Sendly {
  if (!client) {
    const apiKey = process.env.SENDLY_API_KEY;
    if (!apiKey) {
      throw new Error(
        "Set SENDLY_API_KEY, or call setClient(new Sendly(...)) before using the Sendly tools.",
      );
    }
    client = new Sendly(apiKey);
  }
  return client;
}

type ToolFailure = { ok: false; error: string; message: string } & Record<
  string,
  unknown
>;

function failure(err: unknown): ToolFailure {
  if (err instanceof SendlyError) {
    const extra = (err.response ?? {}) as Record<string, unknown>;
    const { error: _error, message: _message, ...recovery } = extra;
    return {
      ...recovery,
      ok: false,
      error: err.code,
      message: err.message,
    };
  }
  return {
    ok: false,
    error: "internal_error",
    message: err instanceof Error ? err.message : String(err),
  };
}

const toSchema = z
  .string()
  .describe("Recipient phone number in E.164 format, e.g. +14155552671");

export const sendSms = tool({
  description:
    "Send an SMS message to a phone number. Use for notifications, alerts and outreach. Returns the message id and delivery status.",
  inputSchema: z.object({
    to: toSchema,
    text: z.string().describe("Message body to send"),
    from: z
      .string()
      .optional()
      .describe(
        "One of your own active Sendly numbers in E.164 format. Omit to use the account default sender. An unrecognised value is rejected for US and Canada destinations, and ignored for international destinations, where your verified sender ID is used instead.",
      ),
    messageType: z
      .enum(["marketing", "transactional"])
      .optional()
      .describe(
        "Defaults to marketing, which is blocked during the recipient country's quiet hours (9pm to 8am in the US, 8pm to 9am in the UK, a per-country window elsewhere). Use transactional only for one-time passwords, alerts and receipts, which send at any hour. Labelling promotional content as transactional is a compliance violation.",
      ),
    metadata: z
      .record(z.string(), z.any())
      .optional()
      .describe(
        "Key-value data stored on the message and echoed in webhook payloads. Must serialise to under 4KB.",
      ),
  }),
  execute: async ({ to, text, from, messageType, metadata }, { toolCallId }) => {
    try {
      const message = await getClient().messages.send(
        { to, text, from, messageType, metadata },
        // A retried tool call returns the original result instead of texting
        // the recipient twice.
        { idempotencyKey: toolCallId },
      );
      return {
        ok: true as const,
        id: message.id,
        to: message.to,
        status: message.status,
        segments: message.segments,
        creditsUsed: message.creditsUsed,
      };
    } catch (err) {
      return failure(err);
    }
  },
});

export const sendOtp = tool({
  description:
    "Send a one-time password to a phone number. Returns a verification id. Pass that id to check_otp along with whatever code the user types back.",
  inputSchema: z.object({
    to: toSchema,
    app_name: z
      .string()
      .optional()
      .describe(
        "Your app or service name, shown in the verification message. Defaults to the account's brand or business name.",
      ),
    code_length: z
      .number()
      .int()
      .min(4)
      .max(10)
      .optional()
      .describe("Number of digits in the code. Defaults to 6."),
    timeout_secs: z
      .number()
      .int()
      .min(60)
      .max(3600)
      .optional()
      .describe("Seconds until the code expires. Defaults to 300 (5 minutes)."),
  }),
  execute: async ({ to, app_name, code_length, timeout_secs }) => {
    try {
      const verification = await getClient().verify.send({
        to,
        appName: app_name,
        codeLength: code_length,
        timeoutSecs: timeout_secs,
      });
      return {
        ok: true as const,
        verification_id: verification.id,
        status: verification.status,
        phone: verification.phone,
        expiresAt: verification.expiresAt,
      };
    } catch (err) {
      return failure(err);
    }
  },
});

export const checkOtp = tool({
  description:
    "Check a one-time password entered by the user. A correct code returns status 'verified'. A wrong code returns error 'invalid_code' with the attempts remaining, an expired code returns 'expired', and an exhausted code returns 'max_attempts_exceeded'. In the last two cases, send a fresh code rather than asking the user to retype.",
  inputSchema: z.object({
    verification_id: z
      .string()
      .describe("The verification id returned by send_otp"),
    code: z.string().describe("The code entered by the user"),
  }),
  execute: async ({ verification_id, code }) => {
    try {
      const result = await getClient().verify.check(verification_id, { code });
      return {
        ok: true as const,
        verification_id: result.id,
        status: result.status,
        phone: result.phone,
        verifiedAt: result.verifiedAt,
      };
    } catch (err) {
      return failure(err);
    }
  },
});

export const listMessages = tool({
  description:
    "List recent outbound and inbound messages, newest first. Use to check whether something was already sent, or to read the delivery status of an earlier send. The endpoint returns no direction field, so a row is not labelled inbound or outbound.",
  inputSchema: z.object({
    limit: z
      .number()
      .int()
      .min(1)
      .max(100)
      .optional()
      .describe("Number of messages to return. Defaults to 50, capped at 100."),
    offset: z
      .number()
      .int()
      .min(0)
      .optional()
      .describe("Number of messages to skip for pagination. Defaults to 0."),
  }),
  execute: async ({ limit, offset }) => {
    try {
      const result = await getClient().messages.list({ limit, offset });
      return {
        ok: true as const,
        messages: result.data.map((message) => ({
          id: message.id,
          to: message.to,
          text: message.text,
          status: message.status,
          createdAt: message.createdAt,
        })),
      };
    } catch (err) {
      return failure(err);
    }
  },
});

export const getBalance = tool({
  description:
    "Check the Sendly credit balance. One credit is $0.01. An SMS to the US or Canada costs 2 credits per segment. Check this before a bulk send.",
  inputSchema: z.object({}),
  execute: async () => {
    try {
      const credits = await getClient().account.getCredits();
      return {
        ok: true as const,
        balance: credits.balance,
        reservedBalance: credits.reservedBalance,
        availableBalance: credits.availableBalance,
      };
    } catch (err) {
      return failure(err);
    }
  },
});

export const sendlyTools = {
  send_sms: sendSms,
  send_otp: sendOtp,
  check_otp: checkOtp,
  list_messages: listMessages,
  get_balance: getBalance,
};

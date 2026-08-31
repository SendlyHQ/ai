"""Sendly tools for LangChain agents.

Five tools built on the official ``sendly`` package: send an SMS, send a
one-time password, check that code, read recent messages, read the credit
balance.

Tool argument names are the Sendly wire names, so what the model emits is what
the API receives. That means the set is deliberately inconsistent: ``send_sms``
takes ``messageType`` in camelCase while the verify tools take ``app_name``,
``code_length`` and ``timeout_secs`` in snake_case. The API is inconsistent
there, and hiding it would only mislead a model that has also read the REST
docs. The one unavoidable exception is ``from_``: ``from`` is a reserved word in
Python, so the sender argument carries the same trailing underscore the Sendly
Python SDK uses, and is sent as ``from``.

Tools return a dict and never raise on an API error. A blocked send or a wrong
OTP is information the agent can act on, so it comes back as
``{"ok": False, "error": ..., "message": ...}`` plus whatever recovery fields
the API supplied, such as ``nextAllowedTime`` or ``remaining_attempts``.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field
from sendly import Sendly
from sendly.errors import SendlyError

_client: Optional[Sendly] = None


def set_client(client: Sendly) -> None:
    """Use a pre-built Sendly client for every tool in this module."""
    global _client
    _client = client


def get_client() -> Sendly:
    """Return the shared Sendly client, building one from SENDLY_API_KEY."""
    global _client
    if _client is None:
        api_key = os.environ.get("SENDLY_API_KEY")
        if not api_key:
            raise RuntimeError(
                "Set SENDLY_API_KEY, or call set_client(Sendly(...)) before "
                "using the Sendly tools."
            )
        _client = Sendly(api_key)
    return _client


def _failure(exc: SendlyError) -> Dict[str, Any]:
    """Turn an SDK exception into a result the model can reason about."""
    result: Dict[str, Any] = {
        "ok": False,
        "error": exc.code,
        "message": exc.message,
    }
    if exc.response is not None:
        try:
            extra = exc.response.model_dump(by_alias=True, exclude_none=True)
        except Exception:
            extra = {}
        for key, value in extra.items():
            if key not in ("error", "message"):
                result[key] = value
    return result


class SendSmsInput(BaseModel):
    """Arguments for send_sms."""

    to: str = Field(description="Recipient phone number in E.164 format, e.g. +14155552671")
    text: str = Field(description="Message body to send")
    from_: Optional[str] = Field(
        default=None,
        description=(
            "One of your own active Sendly numbers in E.164 format. Omit to use the "
            "account default sender. An unrecognised value is rejected for US and "
            "Canada destinations, and ignored for international destinations, where "
            "your verified sender ID is used instead."
        ),
    )
    messageType: Optional[str] = Field(  # noqa: N815 - wire name
        default=None,
        description=(
            "Either 'marketing' or 'transactional'. Defaults to marketing, which is "
            "blocked during the recipient country's quiet hours (9pm to 8am in the "
            "US, 8pm to 9am in the UK, a per-country window elsewhere). Use "
            "transactional only for one-time passwords, alerts and receipts, which "
            "send at any hour. Labelling promotional content as transactional is a "
            "compliance violation."
        ),
    )
    metadata: Optional[Dict[str, Any]] = Field(
        default=None,
        description=(
            "Key-value data stored on the message and echoed in webhook payloads. "
            "Must serialise to under 4KB."
        ),
    )


class SendOtpInput(BaseModel):
    """Arguments for send_otp."""

    to: str = Field(description="Recipient phone number in E.164 format, e.g. +14155552671")
    app_name: Optional[str] = Field(
        default=None,
        description=(
            "Your app or service name, shown in the verification message. Defaults "
            "to the account's brand or business name."
        ),
    )
    code_length: Optional[int] = Field(
        default=None,
        ge=4,
        le=10,
        description="Number of digits in the code. Defaults to 6.",
    )
    timeout_secs: Optional[int] = Field(
        default=None,
        ge=60,
        le=3600,
        description="Seconds until the code expires. Defaults to 300 (5 minutes).",
    )


class CheckOtpInput(BaseModel):
    """Arguments for check_otp."""

    verification_id: str = Field(description="The verification id returned by send_otp")
    code: str = Field(description="The code entered by the user")


class ListMessagesInput(BaseModel):
    """Arguments for list_messages."""

    limit: Optional[int] = Field(
        default=None,
        ge=1,
        le=100,
        description="Number of messages to return. Defaults to 50, capped at 100.",
    )


@tool(args_schema=SendSmsInput)
def send_sms(
    to: str,
    text: str,
    from_: Optional[str] = None,
    messageType: Optional[str] = None,  # noqa: N803 - wire name
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Send an SMS message to a phone number.

    Use this for notifications, alerts and outreach. Returns the message id and
    delivery status.
    """
    try:
        message = get_client().messages.send(
            to=to,
            text=text,
            from_=from_,
            message_type=messageType,
            metadata=metadata,
        )
    except SendlyError as exc:
        return _failure(exc)

    return {
        "ok": True,
        "id": message.id,
        "to": message.to,
        "status": message.status,
        "segments": message.segments,
        "credits_used": message.credits_used,
    }


@tool(args_schema=SendOtpInput)
def send_otp(
    to: str,
    app_name: Optional[str] = None,
    code_length: Optional[int] = None,
    timeout_secs: Optional[int] = None,
) -> Dict[str, Any]:
    """Send a one-time password to a phone number.

    Returns a verification id. Pass that id to check_otp along with whatever
    code the user types back.
    """
    try:
        verification = get_client().verify.send(
            to=to,
            app_name=app_name,
            code_length=code_length,
            timeout_secs=timeout_secs,
        )
    except SendlyError as exc:
        return _failure(exc)

    return {
        "ok": True,
        "verification_id": verification.id,
        "status": verification.status,
        "phone": verification.phone,
        "expires_at": verification.expires_at,
    }


@tool(args_schema=CheckOtpInput)
def check_otp(verification_id: str, code: str) -> Dict[str, Any]:
    """Check a one-time password entered by the user.

    A correct code returns status 'verified'. A wrong code returns error
    'invalid_code' with the attempts remaining, an expired code returns
    'expired', and an exhausted code returns 'max_attempts_exceeded'. In the
    last two cases, send a fresh code rather than asking the user to retype.
    """
    try:
        result = get_client().verify.check(verification_id, code)
    except SendlyError as exc:
        return _failure(exc)

    return {
        "ok": True,
        "verification_id": result.id,
        "status": result.status,
        "phone": result.phone,
        "verified_at": result.verified_at,
    }


@tool(args_schema=ListMessagesInput)
def list_messages(limit: Optional[int] = None) -> Dict[str, Any]:
    """List recent outbound and inbound messages, newest first.

    Use this to check whether something was already sent, or to read the
    delivery status of an earlier send. The endpoint returns no direction
    field, so a row is not labelled inbound or outbound.
    """
    try:
        result = get_client().messages.list(limit=limit)
    except SendlyError as exc:
        return _failure(exc)

    return {
        "ok": True,
        "messages": [
            {
                "id": message.id,
                "to": message.to,
                "text": message.text,
                "status": message.status,
                "created_at": message.created_at,
            }
            for message in result.data
        ],
    }


@tool
def get_balance() -> Dict[str, Any]:
    """Check the Sendly credit balance.

    One credit is $0.01. An SMS to the US or Canada costs 2 credits per
    segment. Check this before a bulk send.
    """
    try:
        credits = get_client().account.get_credits()
    except SendlyError as exc:
        return _failure(exc)

    return {
        "ok": True,
        "balance": credits.balance,
        "reserved_balance": credits.reserved_balance,
        "available_balance": credits.available_balance,
    }


SENDLY_TOOLS = [send_sms, send_otp, check_otp, list_messages, get_balance]

"""What every provider shares: the failure type and provider-neutral content parts."""

from __future__ import annotations

import base64


class LLMUnavailable(Exception):
    """No cached answer and no way to get one: no key, provider error, or unusable output."""


def provider_failure(exc: Exception) -> LLMUnavailable:
    # Only the error type and HTTP status are kept: provider messages can echo the key.
    status = getattr(exc, "status_code", None)
    return LLMUnavailable(f"provider call failed: {type(exc).__name__} (status {status})")


# Content parts in a provider-neutral shape. Each provider turns them into its own format.
def text_part(text: str) -> dict:
    return {"kind": "text", "text": text}


def image_part(data: bytes, mime: str) -> dict:
    return {"kind": "image", "mime": mime, "data": data}


def pdf_part(data: bytes) -> dict:
    return {"kind": "pdf", "data": data}


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")

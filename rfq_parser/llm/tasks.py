"""The two things the model is asked to do. Each is one cached call through LLMClient."""

from __future__ import annotations

from .base import image_part, pdf_part, text_part
from .client import LLMClient
from .prompts import EMAIL_INSTRUCTIONS, TRANSCRIBE_INSTRUCTIONS
from .schemas import EMAIL_SCHEMA, TRANSCRIPT_SCHEMA


def transcribe(client: LLMClient, *, label: str, mime: str, data: bytes) -> dict:
    """Read one image or PDF into text. Returns {kind, legible, note, text}.

    The request holds the file's bytes and nothing else about the email, so the same file
    arriving on two emails is read once.
    """
    part = pdf_part(data) if mime == "application/pdf" else image_part(data, mime)
    return client.complete(
        task="transcribe",
        label=label,
        instructions=TRANSCRIBE_INSTRUCTIONS,
        content=[part],
        schema=TRANSCRIPT_SCHEMA,
    )


def read_email(client: LLMClient, *, label: str, email_text: str) -> dict:
    """Classify one email and extract its line items.

    `email_text` is the whole email as text: headers, body, and each attachment's contents.
    Returns {summary, classification, needs_review, review_reason, line_items}.
    """
    return client.complete(
        task="read_email",
        label=label,
        instructions=EMAIL_INSTRUCTIONS,
        content=[text_part(email_text)],
        schema=EMAIL_SCHEMA,
    )

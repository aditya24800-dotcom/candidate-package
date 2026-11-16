"""The two things the model is asked to do. Each is one cached call through LLMClient."""

from __future__ import annotations

from .base import image_part, pdf_part, text_part
from .client import LLMClient
from .prompts import EMAIL_INSTRUCTIONS, TRANSCRIBE_INSTRUCTIONS
from .schemas import EMAIL_SCHEMA, TRANSCRIPT_SCHEMA


def transcribe(client: LLMClient, *, label: str, mime: str, data: bytes, page_of: tuple | None = None) -> dict:
    """Read one image or PDF into text. Returns {kind, legible, note, text}.

    The request holds the file's bytes and nothing else about the email, so the same file
    arriving on two emails is read once. For one page cut out of a longer PDF, pass
    `page_of` = (hash of the whole file, page number, page count): the model is told it has
    one page of several, and the cache names the page by that instead of by its bytes.
    """
    content = [pdf_part(data) if mime == "application/pdf" else image_part(data, mime)]
    identity = None
    if page_of:
        file_hash, number, count = page_of
        note = (
            f"This is page {number} of {count} of one scanned document. The other pages are transcribed "
            'separately, so a mark such as "Page 1 of 2" does not mean anything is missing.'
        )
        content.insert(0, text_part(note))
        identity = f"{note} File sha256 {file_hash}."
    return client.complete(
        task="transcribe",
        label=label,
        instructions=TRANSCRIBE_INSTRUCTIONS,
        content=content,
        schema=TRANSCRIPT_SCHEMA,
        cache_identity=identity,
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

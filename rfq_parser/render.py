"""Turn a parsed email into the text the model reads: headers, labelled body parts, attachments.

A normal email is one text and one model call. An email with a long attachment is read in
several calls, because the model has to write every line item back and one answer cannot
hold hundreds of them: the first call carries the email and the first part of the
attachment, and each later call carries the email and one further part.
"""

from __future__ import annotations

import re

from .preparser import body_text, preparse

_HEADERS = (("From", "from"), ("To", "to"), ("Cc", "cc"), ("Date", "date"), ("Subject", "subject"))
_PART_TAGS = {"current": "current_message", "quoted": "quoted_message", "forwarded": "forwarded_message"}

MAX_LINES_PER_CALL = 120  # an attachment with more lines than this is read in parts
PART_LINES = 60  # the size of a part: one page, or several short pages, or a block of a file with no pages
CONTEXT_START_LINES = 30  # how far into a document its title, notes and column header are looked for
CONTEXT_RECENT_HEADINGS = 10  # how many of the latest heading lines are repeated to a later part
_BREAK = re.compile(r"^--- page \d+ ---$|^\[sheet ")  # where a page or a sheet starts

_LATER_PART_NOTE = (
    "[This email has a long attachment, which is read in several calls. The message above and the "
    "other parts of the attachment are handled in other calls. In this call, return line items only "
    'from the lines under "this part" below, and use the message and the context lines to understand '
    "them. Do not ask for review because the rest of the document is not shown here.]"
)


def render_email(email: dict) -> str:
    """The whole email as one text."""
    blocks = _message_blocks(email)
    blocks += [_render_attachment(a["extraction"]) for a in email["attachments"] if "extraction" in a]
    return "\n\n".join(blocks)


def render_calls(email: dict) -> list[str]:
    """The texts to send to the model, in order: one for a normal email, several for a long attachment."""
    extractions = [a["extraction"] for a in email["attachments"] if "extraction" in a]
    parts = {id(x): _split(x["text"]) for x in extractions if x["status"] == "ok" and x["text"].count("\n") >= MAX_LINES_PER_CALL}
    if not parts:
        return [render_email(email)]
    message = _message_blocks(email)
    first = list(message)
    for x in extractions:
        first.append(_render_attachment(x, part=(parts[id(x)], 0)) if id(x) in parts else _render_attachment(x))
    calls = ["\n\n".join(first)]
    for x in extractions:
        for index in range(1, len(parts.get(id(x), []))):
            calls.append("\n\n".join(message + [_LATER_PART_NOTE, _render_attachment(x, part=(parts[id(x)], index))]))
    return calls


def _message_blocks(email: dict) -> list[str]:
    headers = email["headers"]
    head = "\n".join(f"{label}: {headers[key]}" for label, key in _HEADERS if headers.get(key))
    return [head] + [_render_part(part) for part in preparse(body_text(email), headers.get("subject") or "")]


def _render_part(part: dict) -> str:
    tag = _PART_TAGS[part["kind"]]
    inner = "\n".join(piece for piece in (part["intro"], part["text"]) if piece)
    return f"<{tag}>\n{inner}\n</{tag}>"


def _render_attachment(extraction: dict, part: tuple | None = None) -> str:
    """One attachment as a block. `part` is (all parts, which one) for a long attachment."""
    name = extraction["filename"]
    if extraction["status"] != "ok":
        return f'<attachment filename="{name}">\n[COULD NOT BE READ: {extraction["note"]}]\n</attachment>'
    lines = []
    if extraction["kind"] == "logo_or_signature":
        lines.append("[a logo or signature graphic, not something the sender attached to be read]")
    elif extraction["method"] == "llm":
        lines.append("[transcription of a picture or scan]")
    if not extraction["legible"]:
        lines.append(f"[the reader was unsure of part of this: {extraction['note']}]")
    if part is None:
        if extraction["text"]:
            lines.append(extraction["text"])
        lines += [_render_attachment(child) for child in extraction["children"]]
        return f'<attachment filename="{name}">\n' + "\n".join(lines) + "\n</attachment>"

    parts, index = part
    total = len(parts)
    if index == 0:
        lines += parts[0]
        lines.append(
            f"[this document continues: this is part 1 of {total}. The other parts are read in separate calls, "
            "so return the line items shown here and do not ask for review because the rest is not shown.]"
        )
    else:
        earlier = [line for chunk in parts[:index] for line in chunk]
        lines.append("[earlier in this document, for context only: its line items are returned by other calls]")
        lines += _context(earlier)
        lines.append("[this part]")
        lines += parts[index]
    return f'<attachment filename="{name}" part="{index + 1} of {total}">\n' + "\n".join(lines) + "\n</attachment>"


def _split(text: str) -> list[list[str]]:
    """Cut a long attachment into parts of about PART_LINES lines, at page or sheet starts where it has them."""
    pages, current = [], []
    for line in text.split("\n"):
        if _BREAK.match(line) and current:
            pages.append(current)
            current = []
        current.append(line)
    pages.append(current)
    parts, current = [], []
    for page in pages:
        if current and len(current) + len(page) > PART_LINES:
            parts.append(current)
            current = []
        current = current + page
        while len(current) > 2 * PART_LINES:  # one very long page, or a file with no pages at all
            parts.append(current[:PART_LINES])
            current = current[PART_LINES:]
    if current:
        parts.append(current)
    return parts


def _context(earlier: list[str]) -> list[str]:
    """What a later part needs from the lines before it: the document's opening lines up to its
    column header (title, notes, form fields), and the latest heading lines (the section it is in).

    A line with no " | " in it is not a table row, so it is a title, a note or a heading.
    """
    earlier = [line for line in earlier if line.strip() and not _BREAK.match(line)]
    start = []
    for line in earlier[:CONTEXT_START_LINES]:
        start.append(line)
        if " | " in line and not any(ch.isdigit() for ch in line):  # the column header row
            break
    else:
        start = [line for line in start if " | " not in line]
    headings = [line for line in earlier if " | " not in line]
    recent = []
    for line in reversed(headings):  # the latest ones, each once, oldest first
        if line not in start and line not in recent:
            recent.insert(0, line)
        if len(recent) == CONTEXT_RECENT_HEADINGS:
            break
    return start + recent

"""Turn a parsed email into the one text the model reads: headers, labelled body parts, attachments."""

from __future__ import annotations

from .preparser import body_text, preparse

_HEADERS = (("From", "from"), ("To", "to"), ("Cc", "cc"), ("Date", "date"), ("Subject", "subject"))
_PART_TAGS = {"current": "current_message", "quoted": "quoted_message", "forwarded": "forwarded_message"}


def render_email(email: dict) -> str:
    headers = email["headers"]
    head = "\n".join(f"{label}: {headers[key]}" for label, key in _HEADERS if headers.get(key))
    blocks = [head]
    blocks += [_render_part(part) for part in preparse(body_text(email), headers.get("subject") or "")]
    blocks += [_render_attachment(a["extraction"]) for a in email["attachments"] if "extraction" in a]
    return "\n\n".join(blocks)


def _render_part(part: dict) -> str:
    tag = _PART_TAGS[part["kind"]]
    inner = "\n".join(piece for piece in (part["intro"], part["text"]) if piece)
    return f"<{tag}>\n{inner}\n</{tag}>"


def _render_attachment(extraction: dict) -> str:
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
    if extraction["text"]:
        lines.append(extraction["text"])
    lines += [_render_attachment(child) for child in extraction["children"]]
    return f'<attachment filename="{name}">\n' + "\n".join(lines) + "\n</attachment>"

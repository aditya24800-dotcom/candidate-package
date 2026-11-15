"""Pre-parser: what code works out about an email before the model reads it.

1. preparse() splits one email's body into labelled parts, in the order they appear:

    current    what the sender wrote in this message
    quoted     an earlier message quoted below (or inside) a reply
    forwarded  a message that is being passed on

   Nothing is dropped: every body line lands in exactly one part. The labels are facts read
   from quote marks and header lines. What a part means for the request is the model's call.

2. find_duplicates() compares all emails of an inbox and names the ones that are the same
   request sent again. It runs before the model, so a duplicate costs no model call and its
   lines are never extracted a second time.
"""

from __future__ import annotations

import re
from email.utils import parseaddr, parsedate_to_datetime

from .attachments.readers import html_to_text

# "> text", "> > text": one mark per level of quoting.
_QUOTE_MARKS = re.compile(r"^((?:>\s?)+)")
# "-----Original Message-----", "---------- Forwarded message ---------", "Begin forwarded message:"
_SEPARATOR = re.compile(r"^\s*(-{2,}\s*(original message|forwarded message)\s*-{2,}|begin forwarded message:)\s*$", re.I)
# A header line as mail programs print it above a quoted message: "From: ...", "*Sent:* ..."
_HEADER = re.compile(r"^\*?(from|sent|date|to|cc|subject)\*?:\s*\S", re.I)
_FORWARD_SUBJECT = re.compile(r"^\s*(fw|fwd)\s*:", re.I)


def preparse(body: str, subject: str = "") -> list[dict]:
    """Return the body as a list of {"kind", "intro", "text"} parts.

    `intro` holds the lines that introduce an earlier message ("On ... wrote:", or a
    "From: / Sent: / To: / Subject:" block), or None.
    """
    lines = [_unquote(line) for line in body.splitlines()]
    forward_subject = bool(_FORWARD_SUBJECT.match(subject or ""))
    parts: list[dict] = []
    part = _open(parts, "current", 0)
    base_kind = "current"  # what unquoted text belongs to; changes after an unquoted header block
    first_boundary = True
    i = 0
    while i < len(lines):
        depth, text = lines[i]
        if not text.strip():
            part["lines"].append("")
            i += 1
            continue

        # 1. A separator line or a header block starts an earlier message that runs on from here.
        separator = _SEPARATOR.match(text)
        header_lines = _header_block(lines, i, depth) if not separator else 0
        if separator or header_lines:
            start = i
            if separator:
                i += 1
                while i < len(lines) and not lines[i][1].strip():
                    i += 1
                header_lines = _header_run(lines, i, depth)
            i += header_lines
            explicit_forward = bool(separator) and "forward" in text.lower()
            kind = "forwarded" if explicit_forward or (first_boundary and forward_subject) else "quoted"
            intro = "\n".join(t for _, t in lines[start:i] if t.strip())
            part = _open(parts, kind, depth, intro)
            if depth == 0:
                base_kind = kind
            first_boundary = False
            continue

        # 2. "On <date> <name> wrote:" introduces the quoted lines that follow it.
        attribution = _attribution(lines, i, depth)
        if attribution:
            intro = " ".join(t.strip() for _, t in lines[i : i + attribution])
            part = _open(parts, "quoted", depth + 1, intro)
            first_boundary = False
            i += attribution
            continue

        # 3. A change in quote depth starts a new part: deeper is an older message,
        #    back to no marks is the surrounding message again.
        if depth != part["depth"]:
            part = _open(parts, base_kind if depth == 0 else "quoted", depth)
        part["lines"].append(text)
        i += 1

    out = []
    for part in parts:
        text = "\n".join(part["lines"]).strip("\n")
        if text.strip() or part["intro"]:
            out.append({"kind": part["kind"], "intro": part["intro"], "text": text})
    return out


def _open(parts: list[dict], kind: str, depth: int, intro: str | None = None) -> dict:
    part = {"kind": kind, "depth": depth, "intro": intro, "lines": []}
    parts.append(part)
    return part


def _unquote(line: str) -> tuple[int, str]:
    """Return (quote depth, the line without its quote marks)."""
    marks = _QUOTE_MARKS.match(line)
    if not marks:
        return 0, line
    return marks.group(1).count(">"), line[marks.end() :]


def _header_run(lines: list, start: int, depth: int) -> int:
    """How many header lines ("From: ...", "Subject: ...") run on from `start` at this depth."""
    count = 0
    while start + count < len(lines):
        line_depth, text = lines[start + count]
        if line_depth != depth or not _HEADER.match(text):
            break
        count += 1
    return count


def _header_block(lines: list, start: int, depth: int) -> int:
    """Length of a header block that opens an earlier message here, or 0.

    It must start with From: and also carry a date line and a To: or Subject: line, so that
    one stray "From: the Houston yard" in a message is not taken for a boundary.
    """
    if not lines[start][1].lower().lstrip("*").startswith("from"):
        return 0
    count = _header_run(lines, start, depth)
    names = {_HEADER.match(text).group(1).lower() for _, text in lines[start : start + count]}
    if count >= 3 and names & {"sent", "date"} and names & {"to", "subject"}:
        return count
    return 0


def _attribution(lines: list, start: int, depth: int) -> int:
    """Number of lines (1 or 2) of an "On ... wrote:" line that introduces quoted text, or 0."""
    text = lines[start][1].strip()
    if not text.startswith("On "):
        return 0
    length = 0
    if text.endswith("wrote:"):
        length = 1
    elif start + 1 < len(lines) and lines[start + 1][0] == depth and lines[start + 1][1].strip().endswith("wrote:"):
        length = 2  # mail programs wrap this line when the address is long
    if not length:
        return 0
    following = next(((d, t) for d, t in lines[start + length :] if t.strip()), None)
    # Only a real introduction is followed by quoted lines; "On Monday we wrote: ..." is not.
    return length if following and following[0] > depth else 0


# --- duplicates ---------------------------------------------------------------------------

# The later email says it is a repeat.
_RESEND = re.compile(
    r"\bre-?sen[dt]\w*|\bsending (?:this |it )?again\b|\b(?:second|2nd|third|3rd) (?:request|attempt|time)\b"
    r"|did(?:n'?t| not) (?:come|go) through|did you (?:get|receive)",
    re.I,
)
# Words that mean the later email asks for something different, even with no number in it
# ("double all quantities", "drop the valves", "two more").
_CHANGE = re.compile(
    r"\b(?:add|adding|plus|more|double|triple|twice|half|chang\w+|revis\w+|updat\w+|instead|replac\w+|remov\w+|drop"
    r"|delet\w+|cancel\w*|except|less|fewer|extra|another|additional|increas\w+|decreas\w+|reduc\w+|correct\w*"
    r"|wrong|different|new|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|dozen|couple|few|several)\b",
    re.I,
)


def body_text(email: dict) -> str:
    """The body as text. The HTML part keeps tables and the place of each pasted picture;
    plain text is the fallback, also when the HTML part is an empty shell."""
    body = (html_to_text(email["html"]) if email.get("html") else "") or (email.get("text") or "")
    for attachment in email["attachments"]:  # name pasted pictures by filename, as their blocks do
        if attachment.get("content_id"):
            body = body.replace(f"cid:{attachment['content_id']}", attachment["filename"])
    return body


def fingerprint(email: dict) -> dict:
    """The facts about one email that decide whether another email repeats it."""
    headers = email["headers"]
    subject = headers.get("subject") or ""
    lines = []
    for part in preparse(body_text(email), subject):
        if part["kind"] != "quoted":  # history under a reply is not part of what is being asked
            lines += [" ".join(line.lower().split()) for line in part["text"].splitlines() if line.strip()]
    try:
        sent = parsedate_to_datetime(headers.get("date") or "").timestamp()
    except (TypeError, ValueError):
        sent = None
    hashes = [a.get("sha256") for a in email["attachments"]]
    return {
        "sender": parseaddr(headers.get("from") or "")[1].lower(),
        "sent": sent,
        "message_id": (headers.get("message_id") or "").strip() or None,
        # None when a file's bytes were not available: then attachments cannot be compared.
        "attachments": None if None in hashes else frozenset(hashes),
        "has_file": any(not a.get("inline") for a in email["attachments"]),
        "lines": frozenset(lines),
        "number_lines": frozenset(line for line in lines if any(ch.isdigit() for ch in line)),
        "says_resend": bool(_RESEND.search(subject) or any(_RESEND.search(line) for line in lines)),
    }


def find_duplicates(emails: dict[str, dict]) -> dict[str, str]:
    """Return {duplicate email id: original email id} for an inbox of parsed emails.

    The email with the later Date is the duplicate. A wrong "duplicate" would empty a real
    order, so every rule below needs positive evidence, and doubt means "not a duplicate".
    """
    prints = {eid: fingerprint(email) for eid, email in emails.items() if not email.get("error")}
    # Oldest first; emails with no usable date go last, in id order.
    order = sorted(prints, key=lambda eid: (prints[eid]["sent"] is None, prints[eid]["sent"] or 0, eid))
    duplicates: dict[str, str] = {}
    originals: list[str] = []
    for eid in order:
        match = next((o for o in originals if _same_request(prints[o], prints[eid])), None)
        if match:
            duplicates[eid] = match
        else:
            originals.append(eid)
    return duplicates


def _same_request(first: dict, later: dict) -> bool:
    if first["message_id"] and first["message_id"] == later["message_id"]:
        return True  # the very same email, delivered twice
    if first["sent"] is None or later["sent"] is None:
        return False  # cannot tell which one came later
    if not first["sender"] or first["sender"] != later["sender"]:
        return False
    if first["attachments"] is None or first["attachments"] != later["attachments"]:
        return False
    # Every line that carries a number (sizes, quantities, dates) must be the same in both.
    if first["number_lines"] != later["number_lines"]:
        return False
    # The words only the later email has must not ask for a change.
    if any(_CHANGE.search(line) for line in later["lines"] - first["lines"]):
        return False
    # Evidence that it is a repeat: the same attached file, the same text line for line,
    # or the later email saying so.
    return (first["has_file"] and later["has_file"]) or first["lines"] == later["lines"] or later["says_resend"]

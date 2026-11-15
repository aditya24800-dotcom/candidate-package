"""The whole run: every .eml in a folder in, one record per email out.

Order of work:
  1. unpack each email and read its attachments        (mime_parser, attachments)
  2. find emails that repeat an earlier one             (preparser.find_duplicates)
  3. for every other email: build the model's input,
     ask the model, interpret the answer                (render, llm, interpreter)
  4. link each revision to the email it revises         (thread headers)
  5. give each duplicate its record

No email can stop the run. Whatever goes wrong with one email, it still gets a record, and
that record asks for a person to look.
"""

from __future__ import annotations

import re
from pathlib import Path

from .attachments import AttachmentExtractor
from .interpreter import Interpreter
from .llm import LLMClient, LLMUnavailable, read_email
from .mime_parser import MimeParser
from .preparser import find_duplicates
from .render import render_email

# Without the model, only these are safe to call "not a request": a bounce or an out-of-office.
_AUTO_SENDER = re.compile(r"mailer-daemon|postmaster", re.I)
_AUTO_SUBJECT = re.compile(
    r"delivery status notification|undeliverable|delivery (?:has )?failed|out of (?:the )?office|automatic reply", re.I
)


def parse_inbox(
    emails_dir: Path,
    llm: LLMClient | None = None,
    log=None,
    trace: dict | None = None,
    files_dir: Path | None = None,
) -> tuple[dict, list]:
    """Return ({email id: record}, the model call ledger). `log` is called with one line per email.

    Pass a dict as `trace` to also get the working data of each email (the unpacked email, the
    text the model was sent, the model's answer), and `files_dir` to keep the attachment files.
    The web app uses both; the command line uses neither.
    """
    llm = llm or LLMClient()
    log = log or (lambda line: None)
    interpreter = Interpreter()
    emails = MimeParser(files_dir=files_dir, extractor=AttachmentExtractor(llm)).parse_dir(emails_dir)
    duplicates = find_duplicates(emails)

    records: dict[str, dict] = {}
    for email_id, email in emails.items():
        notes = {"email": email, "model_input": None, "answer": None}
        if trace is not None:
            trace[email_id] = notes
        if email_id in duplicates:
            continue
        records[email_id] = _read_one(email_id, email, llm, interpreter, notes)
        record = records[email_id]
        flag = "  NEEDS REVIEW: " + record["review_reason"] if record["needs_review"] else ""
        log(f"{email_id}  {record['classification']:<15} {len(record['line_items']):>3} lines{flag}")

    _link_revisions(emails, records)
    for email_id, original_id in duplicates.items():
        records[email_id] = Interpreter.duplicate_record(records[original_id], original_id)
        log(f"{email_id}  duplicate of {original_id}")
    return {email_id: records[email_id] for email_id in sorted(records)}, llm.calls


def _read_one(email_id: str, email: dict, llm: LLMClient, interpreter: Interpreter, notes: dict) -> dict:
    if email.get("error"):
        return fallback_record(email, f"The email file could not be parsed ({email['error']}).")
    try:
        notes["model_input"] = render_email(email)
        notes["answer"] = read_email(llm, label=email_id, email_text=notes["model_input"])
        return interpreter.interpret(notes["answer"])
    except LLMUnavailable as exc:
        return fallback_record(email, f"The model was not available to read this email ({exc}).")
    except Exception as exc:  # a fault in our own code must not cost the other emails
        return fallback_record(email, f"Processing this email failed ({type(exc).__name__}).")


def fallback_record(email: dict, reason: str) -> dict:
    """The record for an email nobody could read. Failing toward "a person must look" costs
    a false alarm at worst; failing toward "nothing to do" would lose a customer's request."""
    headers = email.get("headers") or {}
    automatic = _AUTO_SENDER.search(headers.get("from") or "") or _AUTO_SUBJECT.search(headers.get("subject") or "")
    return {
        "classification": "auto_reply" if automatic else "rfq",
        "revises": None,
        "duplicate_of": None,
        "needs_review": not automatic,
        "review_reason": None if automatic else reason,
        "line_items": [],
    }


def _link_revisions(emails: dict, records: dict) -> None:
    """Set `revises` on each revision whose thread headers point at an email in this inbox."""
    by_message_id = {}
    for email_id, email in emails.items():
        message_id = str(email["headers"].get("message_id") or "").strip()
        if message_id:
            by_message_id[message_id] = email_id
    for email_id, record in records.items():
        if record["classification"] != "rfq_revision":
            continue
        headers = emails[email_id]["headers"]
        # In-Reply-To is the direct parent; References lists the thread oldest first.
        references = re.findall(r"<[^>]+>", str(headers.get("in_reply_to") or ""))
        references += reversed(re.findall(r"<[^>]+>", str(headers.get("references") or "")))
        for reference in references:
            target = by_message_id.get(reference)
            if target and target != email_id:
                record["revises"] = target
                break


def cost_report(calls: list[dict], email_count: int) -> dict:
    """Model calls, tokens and dollars: in total and per email.

    Dollars are what each answer cost when it was first computed, so the report is the same
    on a replay from cache. `live_calls` says how many calls this run actually made.
    """

    def empty() -> dict:
        return {"calls": 0, "live_calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}

    def add(bucket: dict, call: dict) -> None:
        bucket["calls"] += 1
        bucket["live_calls"] += not call["from_cache"]
        bucket["input_tokens"] += call["usage"]["input_tokens"]
        bucket["output_tokens"] += call["usage"]["output_tokens"]
        if call["cost_usd"] is None or bucket["cost_usd"] is None:
            bucket["cost_usd"] = None  # a model with no price on file: tokens only
        else:
            bucket["cost_usd"] = round(bucket["cost_usd"] + call["cost_usd"], 6)

    total, per_email = empty(), {}
    for call in calls:
        add(total, call)
        add(per_email.setdefault(call["label"], empty()), call)
    return {
        "emails": email_count,
        "models": sorted({call["model"] for call in calls}),
        "total": total,
        "per_email": dict(sorted(per_email.items())),
    }

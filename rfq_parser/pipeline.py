"""The whole run: every .eml in a folder in, one record per email out.

Order of work:
  1. unpack each email and read its attachments        (mime_parser, attachments)
  2. find emails that repeat an earlier one, in this
     inbox or in an earlier run                         (preparser.find_duplicates)
  3. for every other email: build the model's input,
     ask the model, interpret the answer                (render, llm, interpreter)
  4. link each revision to the email it revises         (thread headers)
  5. give each duplicate its record

No email can stop the run. Whatever goes wrong with one email, it still gets a record, and
that record asks for a person to look.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from data.check_format import CLASSES

from .attachments import AttachmentExtractor
from .interpreter import Interpreter
from .interpreter.interpreter import REQUEST_CLASSES
from .llm import LLMClient, LLMUnavailable, read_email
from .mime_parser import MimeParser
from .preparser import find_duplicates
from .render import render_calls

_CALL_BREAK = "\n\n" + "=" * 30 + " next model call " + "=" * 30 + "\n\n"

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
    seen_file: Path | None = None,
) -> tuple[dict, list]:
    """Return ({email id: record}, the model call ledger). `log` is called with one line per email.

    Pass a dict as `trace` to also get the working data of each email (the unpacked email, the
    text the model was sent, the model's answer), and `files_dir` to keep the attachment files.
    The web app uses both; the command line uses neither.

    Pass `seen_file` to remember between runs which emails were read. An email that arrives
    again in a later run, under another file name, is then a duplicate of the first delivery
    and is not sent to the model. The command line passes it; the web app does not.
    """
    llm = llm or LLMClient()
    log = log or (lambda line: None)
    interpreter = Interpreter()
    emails = MimeParser(files_dir=files_dir, extractor=AttachmentExtractor(llm)).parse_dir(emails_dir)
    seen = _load_seen(seen_file)
    duplicates = find_duplicates(emails, {entry["message_id"]: email_id for email_id, entry in seen.items()})

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
        message_id = str(email["headers"].get("message_id") or "").strip()
        if message_id and notes["answer"] is not None:  # an email the model could not read is not remembered: its next delivery must be read
            seen[email_id] = {"message_id": message_id, "classification": record["classification"]}

    _link_revisions(emails, records)
    for email_id, original_id in duplicates.items():
        in_this_run = original_id in records
        original = records[original_id] if in_this_run else seen[original_id]
        records[email_id] = Interpreter.duplicate_record(original, original_id)
        log(f"{email_id}  duplicate of {original_id}" + ("" if in_this_run else " (read in an earlier run)"))
    if seen_file is not None:
        _save_seen(seen_file, seen, log)
    return {email_id: records[email_id] for email_id in sorted(records)}, llm.calls


def _load_seen(path: Path | None) -> dict:
    """The emails earlier runs read: {email id: {"message_id", "classification"}}.
    No file, or a damaged one, is an empty memory.

    shortcut: one JSON file, read whole, one run at a time; a real inbox needs a database with
    a unique key on Message-ID, and entries that expire.
    """
    if path is None:
        return {}
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
        return {
            email_id: entry
            for email_id, entry in stored.items()
            if isinstance(entry["message_id"], str) and entry["classification"] in CLASSES
        }
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {}


def _save_seen(path: Path, seen: dict, log) -> None:
    try:
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(json.dumps(seen, indent=1) + "\n", encoding="utf-8")
        os.replace(temporary, path)  # all or nothing: a crash never leaves half a file
    except OSError as exc:  # the records of this run matter more than the memory of it
        log(f"warning: this run's emails were not remembered for later runs ({type(exc).__name__})")


def _read_one(email_id: str, email: dict, llm: LLMClient, interpreter: Interpreter, notes: dict) -> dict:
    if email.get("error"):
        return fallback_record(email, f"The email file could not be parsed ({email['error']}).")
    try:
        texts = render_calls(email)  # one text, or several when an attachment is long
        notes["model_input"] = _CALL_BREAK.join(texts)
        answer = read_email(llm, label=email_id, email_text=texts[0])
        answer = {**answer, "line_items": list(answer["line_items"])}
        failed = 0
        if answer["classification"] in REQUEST_CLASSES:  # only a request has lines worth the further calls
            for text in texts[1:]:
                try:
                    answer["line_items"] += read_email(llm, label=email_id, email_text=text)["line_items"]
                except LLMUnavailable:
                    failed += 1  # the other parts still count; this one is reported below
        if len(texts) > 1 and answer["line_items"]:
            # A call sees one part of a long attachment and may ask for review because the rest is
            # not in front of it. Only code knows whether every part was read, so code decides.
            read_in_full = all(a["extraction"]["status"] == "ok" and a["extraction"]["legible"] for a in email["attachments"])
            if read_in_full:
                answer["needs_review"], answer["review_reason"] = False, None
        if failed:
            problem = f"{failed} of {len(texts)} parts of a long attachment could not be read by the model."
            answer["review_reason"] = " ".join(filter(None, (answer["review_reason"], problem)))
            answer["needs_review"] = True
        notes["answer"] = answer
        return interpreter.interpret(answer)
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

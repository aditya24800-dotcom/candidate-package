"""Long-document test: one email with a text PDF of many pages, read live.

    python diagnostics/long_pdf.py          300 pages (6,300 item rows)
    python diagnostics/long_pdf.py 6        a quick run: 6 pages, 3 model calls

The PDF is the two-page RFQ form of dev email E006, repeated. Each copy holds item rows 1 to
42, and the form's header on page 1 gives the material and rating for every row. The test
passes when every row of every copy comes back exactly once, each line carries the header's
A105, 150# and RF, and the email is not flagged for review.

It makes live model calls, so it needs OPENAI_API_KEY, and it is not part of run.py. It uses
its own empty cache in a temporary folder: nothing in the repository is read or changed.
Measured at 300 pages: 150 calls, $2.21, 77 minutes, 6,300 of 6,300 lines.
"""

from __future__ import annotations

import collections
import io
import json
import os
import subprocess
import sys
import tempfile
import time
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path

from pypdf import PdfReader, PdfWriter

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.stdout.reconfigure(encoding="utf-8")
ROWS_PER_COPY = 42


def squash(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum())


def main() -> int:
    pages = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    copies = max(1, pages // 2)
    if not os.environ.get("OPENAI_API_KEY"):
        print("error: set OPENAI_API_KEY first; this test makes live model calls")
        return 2

    source = BytesParser(policy=policy.default).parsebytes(Path("data/dev/emails/E006.eml").read_bytes())
    form = next(part.get_payload(decode=True) for part in source.walk() if part.get_content_type() == "application/pdf")
    writer = PdfWriter()
    for _ in range(copies):
        for page in PdfReader(io.BytesIO(form)).pages:
            writer.add_page(page)
    buffer = io.BytesIO()
    writer.write(buffer)

    work = Path(tempfile.mkdtemp(prefix="rfq_long_pdf_"))
    inbox = work / "inbox"
    inbox.mkdir()
    message = EmailMessage()
    for name in ("From", "To", "Subject", "Date"):
        message[name] = str(source[name])
    message.set_content("Hi team,\n\nRFQ attached, need it back by Friday.\n\nThanks,\nMarguerite Dubois\n")
    message.add_attachment(buffer.getvalue(), maintype="application", subtype="pdf", filename="big_rfq.pdf")
    (inbox / "L1.eml").write_bytes(message.as_bytes())
    print(f"PDF: {copies * 2} pages, {copies * ROWS_PER_COPY:,} item rows. Working folder: {work}", flush=True)

    out = work / "out.json"
    started = time.perf_counter()
    command = [sys.executable, "-m", "rfq_parser", "parse", "--emails", str(inbox), "--out", str(out)]
    run = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", env={**os.environ, "RFQ_LLM_CACHE_DIR": str(work / "cache")})
    minutes = (time.perf_counter() - started) / 60
    if run.returncode != 0:
        print("FAIL: the parse command stopped:", run.stderr.strip()[-300:])
        return 1

    record = json.loads(out.read_text(encoding="utf-8"))["L1"]
    total = json.loads(out.with_suffix(".cost.json").read_text(encoding="utf-8"))["total"]
    lines = record["line_items"]
    seen = collections.Counter(line["raw"].split(" | ")[0].strip() for line in lines)
    every_row_once = len(lines) == copies * ROWS_PER_COPY and all(seen[str(item)] == copies for item in range(1, ROWS_PER_COPY + 1))
    header = (("a105",), ("150#", "150lb", "class150"), ("rf", "raisedface"))
    carried = sum(all(any(squash(option) in squash(line["description"]) for option in group) for group in header) for line in lines)

    print(f"class {record['classification']} | needs_review {record['needs_review']} {record['review_reason'] or ''}")
    print(f"lines {len(lines):,} of {copies * ROWS_PER_COPY:,} | every row of every copy exactly once: {every_row_once}")
    print(f"lines carrying A105, 150# and RF from the header on page 1: {carried:,} of {len(lines):,}")
    print(f"catalog: {dict(collections.Counter(line['match'] for line in lines))}")
    print(f"model calls {total['calls']} ({total['live_calls']} live) | tokens in {total['input_tokens']:,} out {total['output_tokens']:,} | ${total['cost_usd']:.2f} | {minutes:.1f} minutes")
    passed = every_row_once and carried == len(lines) and not record["needs_review"]
    print("PASS" if passed else "FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())

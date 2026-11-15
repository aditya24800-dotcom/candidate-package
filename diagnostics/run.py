"""Full diagnostic of the RFQ parser. Run from anywhere:

    python diagnostics/run.py            everything that needs no key and costs nothing
    python diagnostics/run.py --live     also one live model call (needs OPENAI_API_KEY)

It checks the environment, the `parse` command on the dev inbox, accuracy against the answer
key, what happens with broken input and a failing provider, each component on its own, the
web app's endpoints, and that no API key is stored in the repository.

Every check prints PASS or FAIL. Nothing here changes the repository: all output goes to a
temporary folder.
"""

from __future__ import annotations

import ast
import importlib
import io
import json
import os
import pkgutil
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

PY = sys.executable
TMP = Path(tempfile.mkdtemp(prefix="rfq_diag_"))
SETTINGS = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "RFQ_LLM_PROVIDER", "RFQ_LLM_MODEL", "RFQ_LLM_CACHE_DIR")
LIVE_KEY = os.environ.get("OPENAI_API_KEY")
for name in SETTINGS:  # the diagnostic itself runs with no key and default settings
    os.environ.pop(name, None)
ENV = dict(os.environ)
DEV = Path("data/dev/emails")
KEY = json.loads(Path("data/dev/answer_key.json").read_text(encoding="utf-8"))["emails"]
QUOTE = chr(34)

results: list[tuple[str, str, bool]] = []


def check(group: str, name: str, ok, detail: str = "") -> None:
    results.append((group, name, bool(ok)))
    print(f"[{'PASS' if ok else 'FAIL'}] {group}: {name}" + (f"  ({detail})" if detail else ""))


def note(text: str) -> None:
    print(f"[note] {text}")


def run_parse(emails, out, extra_env=None, cwd=ROOT):
    command = [PY, "-m", "rfq_parser", "parse", "--emails", str(emails), "--out", str(out)]
    return subprocess.run(command, cwd=cwd, env={**ENV, **(extra_env or {})}, capture_output=True, text=True, encoding="utf-8")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def mail(subject, body="Please quote attached.", files=(), html=None, date="Mon, 05 Oct 2026 09:12:00 -0500", sender="buyer@customer.example"):
    message = EmailMessage()
    message["From"], message["To"], message["Date"], message["Subject"] = sender, "rfq@ridgeline-supply.example", date, subject
    message.set_content(body)
    if html:
        message.add_alternative(html, subtype="html")
    for filename, data in files:
        message.add_attachment(data, maintype="application", subtype="octet-stream", filename=filename)
    return message.as_bytes()


def dev_attachment(email_id, content_type):
    message = BytesParser(policy=policy.default).parsebytes((DEV / f"{email_id}.eml").read_bytes())
    return next(part.get_payload(decode=True) for part in message.walk() if part.get_content_type() == content_type)


# --- A. environment -----------------------------------------------------------------------


def environment():
    check("env", "Python is 3.11 or newer", sys.version_info >= (3, 11), sys.version.split()[0])
    import openai
    import openpyxl
    import pypdf

    pins = dict(line.split("#")[0].strip().split("==") for line in Path("requirements.txt").read_text().splitlines() if "==" in line)
    have = {"openai": openai.__version__, "openpyxl": openpyxl.__version__, "pypdf": pypdf.__version__}
    check("env", "installed packages match requirements.txt", have == pins, str(have))
    sources = list(Path("rfq_parser").rglob("*.py")) + [Path("data/__init__.py")]
    bad = []
    for path in sources:
        try:
            ast.parse(path.read_text(encoding="utf-8"), feature_version=(3, 11))
        except SyntaxError as exc:
            bad.append(f"{path}: {exc}")
    check("env", f"all {len(sources)} source files parse under Python 3.11 grammar", not bad, "; ".join(bad))
    import rfq_parser

    failed = []
    for module in pkgutil.walk_packages(rfq_parser.__path__, "rfq_parser."):
        if module.name.endswith("__main__"):
            continue
        try:
            importlib.import_module(module.name)
        except Exception as exc:
            failed.append(f"{module.name}: {type(exc).__name__}")
    check("env", "every module imports", not failed, "; ".join(failed))
    from rfq_parser.llm import LLMClient

    client = LLMClient()  # no settings in the environment: this is what a plain run uses
    check("env", "the default provider is OpenAI, with its key read from OPENAI_API_KEY",
          client.provider.name == "openai" and client.provider.key_env == "OPENAI_API_KEY", f"model {client.model}")


# --- B. the parse command on the dev inbox ------------------------------------------------


def parse_command() -> dict:
    out1, out2 = TMP / "run1.json", TMP / "run2.json"
    started = time.perf_counter()
    first = run_parse(DEV, out1)
    seconds = time.perf_counter() - started
    check("parse", "command exits 0 on the dev inbox with no key", first.returncode == 0, f"{seconds:.1f}s")
    parsed = read_json(out1) if out1.exists() else {}
    check("parse", "all 24 emails are in the output", len(parsed) == 24)
    check("parse", "no live model calls (answers come from llm_cache)", "(0 live" in first.stdout)
    checker = subprocess.run([PY, "data/check_format.py", str(out1), "--emails", str(DEV)], capture_output=True, text=True)
    check("parse", "data/check_format.py accepts the output", checker.returncode == 0, checker.stdout.strip().splitlines()[-1])
    check("parse", "output is identical to parsed.json in the repository", out1.read_bytes() == Path("parsed.json").read_bytes())
    second = run_parse(DEV, out2)
    check("parse", "second run: identical output and 0 live calls", out2.read_bytes() == out1.read_bytes() and "(0 live" in second.stdout)
    cost = read_json(out1.with_suffix(".cost.json"))
    read_by_model = [email_id for email_id, record in parsed.items() if not record["duplicate_of"]]
    complete = all(email_id in cost["per_email"] and cost["per_email"][email_id]["cost_usd"] is not None for email_id in read_by_model)
    total = cost["total"]
    check("parse", "cost file has calls, tokens and dollars for every email the model read", complete,
          f"{total['calls']} calls, {total['input_tokens']:,} in, {total['output_tokens']:,} out, ${total['cost_usd']:.4f}, {cost['models']}")
    added = sum(entry["cost_usd"] for entry in cost["per_email"].values())
    check("parse", "per-email costs add up to the total", abs(added - total["cost_usd"]) < 1e-6)
    return parsed


# --- C. accuracy against the answer key ---------------------------------------------------

SYNONYMS = [(r"schedule", "sch"), (r"class\s*(\d+)", r"\1lb"), (r"(\d+)\s*(?:#|lbs?\b)", r"\1lb"), (r"butt[\s-]*weld(?:ed)?", "bw"),
            (r"\b(?:npt|thd|thrd)\b", "threaded"), (r"seamless", "smls"), (r"galvani[sz]ed", "galv"), (r"elbow", "ell"),
            (r"valve", "vlv"), (r"flange", "flg"), (r"gasket", "gskt"), (r"spiral[\s-]*wound", "spw"), (r"malleable iron", "mi"),
            (r"carbon steel", "cs"), (r"full port", "fp"), (r"reducer", "red"), (r"coupling", "cplg"),
            (r"threaded and coupled|thd and cpld|t/c", "t&c")]  # fmt: skip


def squash(text):
    return re.sub(r"[^a-z0-9&]+", "", text.lower())


def canonical(text):
    text = text.lower()
    for pattern, replacement in SYNONYMS:
        text = re.sub(pattern, replacement, text)
    return squash(text)


def mentions(description, phrase):
    return any(squash(option) in squash(description) or canonical(option) in canonical(description) for option in phrase.split("|"))


def tokens(raw):
    return set(re.findall(r"[a-z0-9][a-z0-9/#.\-]*", raw.lower()))


def accuracy(parsed: dict) -> None:
    """A scorer written from "What we grade" in data/README.md. It is not the graders' own code."""
    count = dict(want=0, got=0, paired=0, qty=0, uom=0, source=0, context=0, catalog=0, silent=0, wrong=0)
    differences = []
    for email_id, want in KEY.items():
        record = parsed[email_id]
        got, expected, used = record["line_items"], want["line_items"], set()
        count["want"] += len(expected)
        count["got"] += len(got)
        count["silent"] += bool((expected or want["needs_review"]) and not got and not record["needs_review"])
        for wanted in expected:
            best, score = None, 0.5
            for index, line in enumerate(got):
                if index in used:
                    continue
                ours, theirs = tokens(line["raw"]), tokens(wanted["raw"])
                similarity = len(ours & theirs) / max(1, len(ours | theirs))
                if similarity > score:
                    best, score = index, similarity
            if best is None:
                differences.append(f"{wanted['id']}: line not paired, the raw text differs from the key's")
                continue
            used.add(best)
            line = got[best]
            count["paired"] += 1
            missing = [p for p in wanted["must_mention"] if not mentions(line["description"], p)]
            forbidden = [p for p in wanted["must_not_mention"] if mentions(line["description"], p)]
            count["qty"] += line["qty"] == wanted["qty"]
            count["uom"] += (line["uom"] or "EA") == (wanted["uom"] or "EA")
            count["source"] += line["source"] == wanted["source"]
            count["context"] += not missing and not forbidden
            same_part = line["match"] == wanted["match"] and line["sku"] == wanted["sku"]
            count["catalog"] += same_part
            count["wrong"] += line["match"] == "matched" and not same_part
            if not same_part:
                differences.append(f"{wanted['id']}: catalog answer is {line['match']}, the key says {wanted['match']}")
    paired = count["paired"]
    for field in ("classification", "needs_review", "duplicate_of", "revises"):
        right = sum(parsed[email_id][field] == KEY[email_id][field] for email_id in KEY)
        check("accuracy", f"{field} matches the key", right == 24, f"{right}/24")
    check("accuracy", "no silent losses", count["silent"] == 0)
    check("accuracy", "lines found", paired >= 209, f"{paired}/{count['want']}")
    check("accuracy", "lines returned are real requests", count["got"] - paired <= 1, f"{paired}/{count['got']}, {count['got'] - paired} unpaired")
    fields_right = count["qty"] == count["uom"] == count["source"] == count["context"] == paired
    check("accuracy", "quantity, unit, source and context on paired lines", fields_right,
          f"qty {count['qty']}, unit {count['uom']}, source {count['source']}, context {count['context']} of {paired}")
    check("accuracy", "catalog: no wrong confident match", count["wrong"] == 0, f"{count['catalog']}/{paired} agree with the key")
    for difference in differences:
        note("known difference from the key - " + difference)


# --- D. broken input and a failing provider -----------------------------------------------


def robustness() -> None:
    import openpyxl

    from rfq_parser.attachments import AttachmentExtractor
    from rfq_parser.llm import LLMClient, ResponseCache
    from rfq_parser.mime_parser import MimeParser
    from rfq_parser.pipeline import parse_inbox

    workbook = openpyxl.Workbook()
    workbook.active.append(["QTY", "ITEM"])
    workbook.active.append([3, "valve"])
    buffer = io.BytesIO()
    workbook.save(buffer)
    xlsx = buffer.getvalue()
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("bom.csv", "Qty,Item\n3,valve\n")
    cases = {
        "B01_good": (DEV / "E001.eml").read_bytes(),
        "B02_garbage": b"\x00\x01\x02 not an email at all \xff\xfe",
        "B03_empty": b"",
        "B04_truncated_xlsx": mail("truncated", files=[("bom.xlsx", xlsx[:700])]),
        "B05_encrypted_pdf": mail("secure", files=[("rfq.pdf", dev_attachment("E022", "application/pdf"))]),
        "B06_unknown_file": mail("unknown", files=[("drawing.dwg", bytes(range(256)) * 8)]),
        "B07_zip": mail("zipped", files=[("package.zip", archive.getvalue())]),
        "B08_zero_byte": mail("zero", files=[("list.csv", b"")]),
        "B09_html_only": mail("html", body="", html="<html><body><table><tr><td>10</td><td>2in elbow</td></tr></table></body></html>"),
        "B10_legacy_xls": mail("legacy", files=[("old.xls", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 200)]),
        "B11_bounce": mail("Delivery Status Notification (Failure)", sender="MAILER-DAEMON@mx.example", body="Delivery failed."),
    }
    odd = TMP / "odd_inbox"
    odd.mkdir()
    for name, data in cases.items():
        (odd / f"{name}.eml").write_bytes(data)

    seen = {}
    for email_id, email in MimeParser(extractor=AttachmentExtractor()).parse_dir(odd).items():
        for attachment in email["attachments"]:
            extraction = attachment["extraction"]
            seen[email_id] = (extraction["status"], extraction["note"] or f"{len(extraction['children'])} file inside, read")
    expected = {"B04_truncated_xlsx": "unreadable", "B05_encrypted_pdf": "unreadable", "B06_unknown_file": "unreadable",
                "B07_zip": "ok", "B08_zero_byte": "unreadable", "B10_legacy_xls": "unreadable"}  # fmt: skip
    check("robust", "each damaged or unsupported attachment is reported, not raised",
          all(seen.get(name, ("",))[0] == status for name, status in expected.items()),
          "; ".join(f"{name[4:]}: {seen[name][1]}" for name in expected if name in seen))

    out = TMP / "odd.json"
    degraded = run_parse(odd, out, {"RFQ_LLM_CACHE_DIR": str(TMP / "empty_cache")})
    records = read_json(out) if out.exists() else {}
    check("robust", "command exits 0 on 11 odd emails with no key and no saved answers", degraded.returncode == 0)
    check("robust", "every one of the 11 files has a record", sorted(records) == sorted(cases))
    flagged = sum(record["needs_review"] for record in records.values())
    check("robust", "unreadable requests are flagged for review, never left silently empty",
          all(record["needs_review"] or record["classification"] == "auto_reply" for record in records.values()), f"{flagged} flagged")
    check("robust", "a bounce is recognised without the model", records.get("B11_bounce", {}).get("classification") == "auto_reply")
    checker = subprocess.run([PY, "data/check_format.py", str(out), "--emails", str(odd)], capture_output=True, text=True)
    check("robust", "the degraded output still passes check_format", checker.returncode == 0, checker.stdout.strip().splitlines()[-1])
    run_parse(odd, TMP / "odd2.json")
    good = read_json(TMP / "odd2.json")["B01_good"]
    check("robust", "with the saved answers, the good email among them is still read in full", len(good["line_items"]) == 5 and not good["needs_review"])
    check("robust", "a wrong --emails folder gives exit code 2", run_parse(TMP / "nowhere", TMP / "x.json").returncode == 2)
    no_arguments = subprocess.run([PY, "-m", "rfq_parser", "parse"], capture_output=True, text=True)
    check("robust", "missing arguments give exit code 2", no_arguments.returncode == 2)

    class ProviderDown:
        def __init__(self):
            self.calls = 0
            self.responses = self

        def create(self, **request):
            self.calls += 1
            raise ConnectionError("provider is down")

    llm = LLMClient(cache=ResponseCache(TMP / "down_cache"), api_key="sk-test-not-real")
    down = ProviderDown()
    llm.provider._client = down
    small = TMP / "small"
    small.mkdir()
    for email_id in ("E001", "E003", "E013"):
        shutil.copy(DEV / f"{email_id}.eml", small)
    records, _ = parse_inbox(small, llm=llm)
    check("robust", "provider down: every email still gets a record, and requests are flagged",
          len(records) == 3 and records["E001"]["needs_review"] and records["E013"]["classification"] == "auto_reply",
          f"{down.calls} failed calls, no exception")

    corrupt = TMP / "corrupt_cache"
    shutil.copytree("llm_cache", corrupt)
    for path in corrupt.glob("*.json"):
        path.write_text("{ this is not json", encoding="utf-8")
    result = run_parse(small, TMP / "corrupt.json", {"RFQ_LLM_CACHE_DIR": str(corrupt)})
    check("robust", "corrupt cache files are treated as missing, not as a crash", result.returncode == 0 and len(read_json(TMP / "corrupt.json")) == 3)

    elsewhere = run_parse(small, TMP / "elsewhere.json", cwd=TMP)
    check("limits", "the command runs when started from another folder", elsewhere.returncode == 0,
          "" if elsewhere.returncode == 0 else "it must be started from the repository root")


# --- E. each component on its own ---------------------------------------------------------


def components() -> None:
    from rfq_parser.attachments import AttachmentExtractor
    from rfq_parser.interpreter import Interpreter
    from rfq_parser.llm import LLMClient
    from rfq_parser.mime_parser import MimeParser
    from rfq_parser.preparser import body_text, find_duplicates, preparse

    dev = MimeParser(extractor=AttachmentExtractor(LLMClient())).parse_dir(DEV)
    how = {}
    for email in dev.values():
        for attachment in email["attachments"]:
            extraction = attachment["extraction"]
            kind = extraction["method"] if extraction["status"] == "ok" else "unreadable"
            how[kind] = how.get(kind, 0) + 1
    check("parts", "dev attachments: 8 read by code, 6 by the model, 1 unreadable", how == {"code": 8, "llm": 6, "unreadable": 1}, str(how))

    kinds = {email_id: [part["kind"] for part in preparse(body_text(email), str(email["headers"].get("subject") or ""))] for email_id, email in dev.items()}
    check("parts", "body labels: 4 replies are current + quoted, 1 forward is current + forwarded",
          all(kinds[e] == ["current", "quoted"] for e in ("E004", "E011", "E012", "E020")) and kinds["E016"] == ["current", "forwarded"])
    lost = []
    for email_id, email in dev.items():
        kept = "\n".join((part["intro"] or "") + "\n" + part["text"] for part in preparse(email["text"] or "", ""))
        lost += [f"{email_id}: {line}" for line in (email["text"] or "").splitlines() if line.lstrip("> ").strip() and line.lstrip("> ").strip() not in kept]
    check("parts", "body labelling drops no line in any of the 24 emails", not lost, "; ".join(lost[:2]))

    check("parts", "duplicates: the dev inbox gives exactly E008 -> E006", find_duplicates(dev) == {"E008": "E006"})
    later, csv_file, body_list = "Tue, 06 Oct 2026 08:00:00 -0500", b"Qty,Item\n10,2in elbow\n", "Please quote:\n10 ea 2in elbow\n5 ea 2in tee\n"
    tests = [
        ("same file resent with a new note", [mail("RFQ", files=[("bom.csv", csv_file)]), mail("RFQ", body="Did you get this? Sending again.", files=[("bom.csv", csv_file)], date=later)], {"M2": "M1"}),
        ("a quantity changed", [mail("RFQ", body=body_list, files=[("bom.csv", csv_file)]), mail("RFQ", body=body_list.replace("10 ea", "12 ea"), files=[("bom.csv", csv_file)], date=later)], {}),
        ("double all quantities", [mail("RFQ", files=[("bom.csv", csv_file)]), mail("RFQ", body="Please double all quantities on the attached.", files=[("bom.csv", csv_file)], date=later)], {}),
        ("a different file", [mail("RFQ", files=[("bom.csv", csv_file)]), mail("RFQ", files=[("bom.csv", csv_file + b"3,valve\n")], date=later)], {}),
        ("a different sender", [mail("RFQ", files=[("bom.csv", csv_file)]), mail("RFQ", files=[("bom.csv", csv_file)], date=later, sender="other@elsewhere.example")], {}),
        ("identical text twice", [mail("RFQ", body=body_list), mail("RFQ", body=body_list, date=later)], {"M2": "M1"}),
        ("a status question, then a new request", [mail("q", body="Any update on my quote?"), mail("q", body="Please quote the same valve as last time.", date=later)], {}),
    ]  # fmt: skip
    wrong = []
    for name, messages, want in tests:
        folder = Path(tempfile.mkdtemp(dir=TMP))
        for index, message in enumerate(messages, 1):
            (folder / f"M{index}.eml").write_bytes(message)
        if find_duplicates(MimeParser().parse_dir(folder)) != want:
            wrong.append(name)
    check("parts", f"duplicates: {len(tests)} made-up cases decided correctly", not wrong, "; ".join(wrong))

    interpreter = Interpreter()
    description = {part.sku: part.description for part in interpreter.parts}
    q = QUOTE
    wording = [
        (f"3{q} 150 lb RFWN flange A105", "matched", f"FLG WN 3{q} 150# A105 RF"),
        (f"elbow 2{q} 3000# thd A105", "ambiguous", None),
        (f"2{q} PVC ball valve", "not_in_catalog", None),
        ("4 x 6 conc reducer sch 40 wpb", "matched", f"CONC RED 6 X 4{q} SCH 40 A234 WPB BW"),
        ("stud bolt 5/8-11 x 3.5 B7", "matched", f"STUD 5/8 X 3-1/2{q} B7/2H"),
        (f"1/2{q} needle valve", "not_in_catalog", None),
        ("safety gloves, large", "not_in_catalog", None),
        (f"2{q} ball valve", "ambiguous", None),
        (f"3{q} wafer butterfly valve cast iron", "not_in_catalog", None),
        (f"1 1/2{q} sch 40 A53B ERW pipe T&C", "matched", f"PIPE 1-1/2{q} SCH 40 A53B ERW T&C"),
    ]
    wrong = []
    for text, want, part in wording:
        answer = interpreter.match(text)
        if answer["match"] != want or (part and description.get(answer["sku"]) != part):
            wrong.append(f"{text} -> {answer['match']} {description.get(answer['sku'])}")
    check("parts", f"catalog matcher: {len(wording)} descriptions in wording the dev inbox does not use", not wrong, "; ".join(wrong))

    line = {"raw": "r", "description": "d", "qty": 1.0, "uom": None, "source": "body"}
    quote = interpreter.interpret({"classification": "vendor_quote", "needs_review": True, "review_reason": "x", "line_items": [line]})
    empty = interpreter.interpret({"classification": "rfq", "needs_review": False, "review_reason": None, "line_items": []})
    check("parts", "record rules: no lines on a non-request, and an empty request is flagged",
          quote["line_items"] == [] and not quote["needs_review"] and empty["needs_review"] and bool(empty["review_reason"]))


# --- F. the web app -----------------------------------------------------------------------


def web_app() -> None:
    port, decisions = 8791, TMP / "decisions.json"
    before = hashlib_file("parsed.json")
    server = subprocess.Popen([PY, "-m", "rfq_parser", "web", "--emails", str(DEV), "--port", str(port), "--decisions", str(decisions)],
                              cwd=ROOT, env=ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)  # fmt: skip
    base = f"http://127.0.0.1:{port}"

    def get(path):
        try:
            with urllib.request.urlopen(base + path, timeout=10) as response:
                return response.status, response.read(), response.headers.get("Content-Type", "")
        except urllib.error.HTTPError as error:
            return error.code, error.read(), ""

    def post(payload):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        request = urllib.request.Request(base + "/api/decision", data=body, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    try:
        for _ in range(120):
            try:
                urllib.request.urlopen(base + "/api/inbox", timeout=1).close()
                break
            except OSError:
                time.sleep(0.25)
        status, page, kind = get("/")
        check("web", "the page is served", status == 200 and b"<script>" in page and "text/html" in kind, f"{len(page):,} bytes")
        script = re.search(rb"<script>(.*)</script>", page, re.S).group(1)
        (TMP / "page.js").write_bytes(script)
        node = shutil.which("node")
        if node:
            syntax = subprocess.run([node, "--check", str(TMP / "page.js")], capture_output=True, text=True)
            check("web", "the page's script has no syntax error", syntax.returncode == 0, syntax.stderr.strip()[:120])
        else:
            note("node is not installed, so the page's script was not syntax-checked")
        inbox = json.loads(get("/api/inbox")[1])
        check("web", "the inbox lists all 24 emails", len(inbox) == 24)
        problems, files, lines = [], 0, 0
        for row in inbox:
            status, body, _ = get("/api/email/" + row["id"])
            email = json.loads(body)
            if status != 200 or not {"headers", "parts", "attachments", "record", "decisions"} <= set(email):
                problems.append(row["id"])
                continue
            lines += len(email["record"]["line_items"])
            for attachment in email["attachments"]:
                file_status, data, _ = get(attachment["url"])
                files += 1
                if file_status != 200 or len(data) != attachment["size"]:
                    problems.append(f"{row['id']} {attachment['filename']}")
        check("web", "every email opens, with its parts, attachments and lines", not problems, f"{lines} lines, {files} attachment files served")
        found = json.loads(get("/api/catalog?q=gate+2+800")[1])
        check("web", "catalog search returns parts", any("VLV GATE 2" in part["description"] for part in found), f"{len(found)} results")
        refused = [get("/api/email/NOPE")[0], get("/file/E007/99")[0], get("/file/E007/..%2f..%2fparsed.json")[0], get("/nothing")[0]]
        check("web", "unknown email, bad file number, path trick and unknown address are refused", refused == [404, 404, 404, 404], str(refused))
        good = [post({"email": "E002", "line": 2, "action": "override", "sku": "RL-17820"}), post({"email": "E002", "line": 0, "action": "accept"}),
                post({"email": "E004", "line": 7, "action": "not_in_catalog"}), post({"email": "E002", "line": 0, "action": "clear"})]  # fmt: skip
        saved = read_json(decisions) if decisions.exists() else {}
        check("web", "accept, change part, not stocked and undo are saved", all(status == 200 for status, _ in good)
              and saved.get("E002", {}).get("2", {}).get("sku") == "RL-17820" and "0" not in saved.get("E002", {}) and "7" in saved.get("E004", {}))
        bad = [post({"email": "E002", "line": 99, "action": "accept"})[0], post({"email": "E002", "line": 1, "action": "override", "sku": "RL-00000"})[0],
               post({"email": "E002", "line": 1, "action": "delete"})[0], post(b"not json")[0]]  # fmt: skip
        check("web", "a bad line, an unknown part, an unknown action and a non-JSON body are refused", bad == [400, 400, 400, 400], str(bad))
        check("web", "parsed.json is not changed by the app", hashlib_file("parsed.json") == before)
    finally:
        server.terminate()
        server.wait(timeout=10)


def hashlib_file(path) -> str:
    import hashlib

    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# --- G. secrets and hand-in files ---------------------------------------------------------


def hygiene() -> None:
    hits = []
    for path in Path(".").rglob("*"):
        if not path.is_file() or ".git" in path.parts or "__pycache__" in path.parts:
            continue
        if re.search(r"sk-[A-Za-z0-9_\-]{20,}", path.read_text(encoding="utf-8", errors="ignore")):
            hits.append(str(path))
    check("hygiene", "no API key is stored in any file of the repository", not hits, "; ".join(hits))
    providers = {}
    for path in Path("llm_cache").glob("*.json"):
        provider = read_json(path)["provider"]
        providers[provider] = providers.get(provider, 0) + 1
    check("hygiene", "llm_cache holds the default provider's answers for the dev inbox", providers.get("openai", 0) >= 29, str(providers))
    untracked = subprocess.run(["git", "status", "--short"], capture_output=True, text=True).stdout.strip().splitlines()
    commits = subprocess.run(["git", "rev-list", "--count", "HEAD"], capture_output=True, text=True).stdout.strip()
    check("hand-in", "the work is committed", not untracked, f"{commits} commit so far, {len(untracked)} paths not committed")
    for name in ("RUNNING.md", "parsed.json", "DECISIONS.md", "AI_USAGE.md", ".gitignore"):
        check("hand-in", f"{name} exists", Path(name).exists())


# --- H. one live model call ---------------------------------------------------------------


def live_call() -> None:
    if not LIVE_KEY:
        note("--live was asked for, but OPENAI_API_KEY is not set: live call skipped")
        return
    one = TMP / "live"
    one.mkdir()
    shutil.copy(DEV / "E001.eml", one)
    started = time.perf_counter()
    result = run_parse(one, TMP / "live.json", {"OPENAI_API_KEY": LIVE_KEY, "RFQ_LLM_CACHE_DIR": str(TMP / "live_cache")})
    record = read_json(TMP / "live.json")["E001"] if (TMP / "live.json").exists() else {}
    cost = read_json(TMP / "live.cost.json")["total"] if (TMP / "live.cost.json").exists() else {}
    check("live", "a live call to the default provider reads E001 correctly",
          result.returncode == 0 and record.get("classification") == "rfq" and len(record.get("line_items", [])) == 5 and cost.get("live_calls") == 1,
          f"{time.perf_counter() - started:.0f}s, ${cost.get('cost_usd', 0):.4f}")


def main() -> int:
    environment()
    parsed = parse_command()
    if parsed:
        accuracy(parsed)
    robustness()
    components()
    web_app()
    hygiene()
    if "--live" in sys.argv:
        live_call()
    failed = [(group, name) for group, name, ok in results if not ok]
    print(f"\n=== {len(results) - len(failed)} passed, {len(failed)} failed ===")
    for group, name in failed:
        print(f"  FAILED  {group}: {name}")
    shutil.rmtree(TMP, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

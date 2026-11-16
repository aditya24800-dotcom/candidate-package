"""Command line. Run it from the repository root.

    python -m rfq_parser parse --emails <dir> --out parsed.json
    python -m rfq_parser web   --emails <dir> [--port 8000]

`parse` writes the records to --out, and the cost of the run (model calls, tokens and dollars
per email) to a file next to it ending in .cost.json. A third file next to it, ending in
.seen.json, remembers which emails were read, so that an email delivered again in a later run
with the same --out is recognised as a duplicate.
`web` starts the local walk-through app on the same inbox.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .pipeline import cost_report, parse_inbox


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rfq_parser", description="Read an RFQ inbox into parsed.json.")
    commands = parser.add_subparsers(dest="command", required=True)
    parse = commands.add_parser("parse", help="read every .eml in a folder and write one JSON file")
    parse.add_argument("--emails", type=Path, required=True, help="folder of .eml files")
    parse.add_argument("--out", type=Path, required=True, help="where to write the JSON, e.g. parsed.json")
    web = commands.add_parser("web", help="start the local app that walks one email through the system")
    web.add_argument("--emails", type=Path, required=True, help="folder of .eml files")
    web.add_argument("--port", type=int, default=8000)
    web.add_argument("--decisions", type=Path, default=Path("decisions.json"), help="where the rep's choices are saved")
    args = parser.parse_args(argv)

    if not args.emails.is_dir():
        print(f"error: --emails {args.emails} is not a folder", file=sys.stderr)
        return 2
    if args.command == "web":
        from .web import serve  # imported here: `parse` does not need the web app

        serve(args.emails, args.port, args.decisions)
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    seen_file = args.out.with_suffix(".seen.json")
    records, calls = parse_inbox(args.emails, log=lambda line: print(line, file=sys.stderr), seen_file=seen_file)
    args.out.write_text(json.dumps(records, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    cost = cost_report(calls, len(records))
    cost_path = args.out.with_suffix(".cost.json")
    cost_path.write_text(json.dumps(cost, indent=2) + "\n", encoding="utf-8")
    total = cost["total"]
    dollars = "unknown" if total["cost_usd"] is None else f"${total['cost_usd']:.2f}"
    print(f"{len(records)} emails -> {args.out}")
    print(
        f"model calls {total['calls']} ({total['live_calls']} live, {total['calls'] - total['live_calls']} from cache)"
        f" | tokens in {total['input_tokens']:,} out {total['output_tokens']:,} | cost {dollars} | details in {cost_path}"
    )
    return 0

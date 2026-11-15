"""A small local web app that walks one email through the system.

    python -m rfq_parser web --emails data/dev/emails

It runs the same pipeline as `parse`, keeps the working data of every email, and serves one
page: the email as received, how each attachment was read, the lines that were extracted and
where each came from, and each line's catalog decision, which a rep can accept or overturn.

The rep's decisions are saved to their own file. The system's records are never changed, so
"what the system said" and "what the rep said" stay apart.

It listens on 127.0.0.1 only and uses nothing outside the standard library.
"""

from __future__ import annotations

import json
import mimetypes
import os
import sys
import tempfile
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from ..interpreter import Interpreter
from ..pipeline import cost_report, parse_inbox
from ..preparser import body_text, preparse

PAGE = Path(__file__).with_name("page.html")
ACTIONS = {"accept", "override", "not_in_catalog", "clear"}


class App:
    """Everything the page needs, computed once at start."""

    def __init__(self, emails_dir: Path, decisions_path: Path):
        self._files = tempfile.TemporaryDirectory(prefix="rfq_web_")  # attachment files, for the page to show
        self.trace: dict[str, dict] = {}
        self.records, calls = parse_inbox(
            emails_dir, log=lambda line: print(line, file=sys.stderr), trace=self.trace, files_dir=Path(self._files.name)
        )
        self.cost = cost_report(calls, len(self.records))["per_email"]
        self.parts = {part.sku: part for part in Interpreter().parts}
        self.decisions_path = decisions_path
        try:
            self.decisions = json.loads(decisions_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.decisions = {}

    # --- what the page asks for -----------------------------------------------------------

    def inbox(self) -> list[dict]:
        rows = []
        for email_id, record in self.records.items():
            headers = self.trace[email_id]["email"]["headers"]
            rows.append(
                {
                    "id": email_id,
                    "from": str(headers.get("from") or ""),
                    "subject": str(headers.get("subject") or ""),
                    "classification": record["classification"],
                    "lines": len(record["line_items"]),
                    "needs_review": record["needs_review"],
                    "duplicate_of": record["duplicate_of"],
                    "revises": record["revises"],
                }
            )
        return rows

    def email(self, email_id: str) -> dict | None:
        if email_id not in self.records:
            return None
        notes = self.trace[email_id]
        email, record = notes["email"], self.records[email_id]
        headers = {name: str(value) for name, value in email["headers"].items() if value}
        attachments = [
            {
                "filename": a["filename"],
                "mime": a["mime"],
                "size": a["size"],
                "inline": a["inline"],
                "url": f"/file/{email_id}/{index}",
                "extraction": a.get("extraction"),
            }
            for index, a in enumerate(email["attachments"])
        ]
        lines = []
        for line in record["line_items"]:
            part = self.parts.get(line.get("sku") or "")
            candidates = [{"sku": sku, "description": self.parts[sku].description} for sku in line.get("candidates", [])]
            lines.append({**line, "sku_description": part.description if part else None, "candidates": candidates})
        return {
            "id": email_id,
            "error": email.get("error"),
            "headers": headers,
            "parts": preparse(body_text(email), headers.get("subject", "")) if not email.get("error") else [],
            "attachments": attachments,
            "model_input": notes["model_input"],
            "summary": (notes["answer"] or {}).get("summary"),
            "record": {**record, "line_items": lines},
            "cost": self.cost.get(email_id),
            "decisions": self.decisions.get(email_id, {}),
        }

    def attachment_file(self, email_id: str, index: str) -> tuple[bytes, str] | None:
        """Bytes and content type of one attachment. The file is looked up by position, so no
        path from the request ever reaches the file system."""
        try:
            attachment = self.trace[email_id]["email"]["attachments"][int(index)]
            data = Path(attachment["path"]).read_bytes()
        except (KeyError, IndexError, ValueError, OSError, TypeError):
            return None
        kind = mimetypes.guess_type(attachment["filename"])[0] or attachment["mime"] or "application/octet-stream"
        return data, kind

    def search_catalog(self, query: str) -> list[dict]:
        words = query.lower().split()
        if not words:
            return []
        found = [p for p in self.parts.values() if all(w in f"{p.sku} {p.description}".lower() for w in words)]
        return [{"sku": p.sku, "description": p.description} for p in found[:25]]

    def decide(self, payload: dict) -> dict:
        """Record what the rep decided about one line. Returns the email's decisions, or an error."""
        email_id, action, sku = payload.get("email"), payload.get("action"), payload.get("sku")
        record = self.records.get(email_id)
        try:
            index = int(payload.get("line"))
        except (TypeError, ValueError):
            return {"error": "line must be a number"}
        if record is None or not 0 <= index < len(record["line_items"]):
            return {"error": "no such email or line"}
        if action not in ACTIONS:
            return {"error": f"action must be one of {sorted(ACTIONS)}"}
        if action == "override" and sku not in self.parts:
            return {"error": "override needs a sku that is in the catalog"}
        mine = self.decisions.setdefault(email_id, {})
        if action == "clear":
            mine.pop(str(index), None)
        else:
            mine[str(index)] = {
                "action": action,
                "sku": sku if action == "override" else None,
                "sku_description": self.parts[sku].description if action == "override" else None,
                "system_match": record["line_items"][index]["match"],
                "system_sku": record["line_items"][index]["sku"],
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
        if not mine:
            self.decisions.pop(email_id, None)
        temporary = self.decisions_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.decisions, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, self.decisions_path)
        return {"decisions": self.decisions.get(email_id, {})}


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data, status: int = 200) -> None:
            self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def do_GET(self) -> None:
            url = urlparse(self.path)
            pieces = [unquote(p) for p in url.path.split("/") if p]
            if not pieces:
                return self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            if pieces == ["api", "inbox"]:
                return self._json(app.inbox())
            if len(pieces) == 3 and pieces[:2] == ["api", "email"]:
                email = app.email(pieces[2])
                return self._json(email) if email else self._json({"error": "no such email"}, 404)
            if pieces == ["api", "catalog"]:
                return self._json(app.search_catalog(parse_qs(url.query).get("q", [""])[0]))
            if len(pieces) == 3 and pieces[0] == "file":
                found = app.attachment_file(pieces[1], pieces[2])
                return self._send(200, found[0], found[1]) if found else self._json({"error": "no such file"}, 404)
            self._json({"error": "not found"}, 404)

        def do_POST(self) -> None:
            if urlparse(self.path).path != "/api/decision":
                return self._json({"error": "not found"}, 404)
            try:
                length = min(int(self.headers.get("Content-Length") or 0), 10_000)
                payload = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                return self._json({"error": "body must be JSON"}, 400)
            result = app.decide(payload if isinstance(payload, dict) else {})
            self._json(result, 400 if "error" in result else 200)

        def log_message(self, *args) -> None:  # keep the terminal for the pipeline's own lines
            pass

    return Handler


def serve(emails_dir: Path, port: int = 8000, decisions_path: Path | None = None) -> None:
    app = App(emails_dir, decisions_path or Path("decisions.json"))
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))
    print(f"{len(app.records)} emails ready. Open http://127.0.0.1:{port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

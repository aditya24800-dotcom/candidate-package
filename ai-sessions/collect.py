"""Copy this project's Claude Code session logs into this folder, with credentials redacted.

    python ai-sessions/collect.py

What it copies:
  - every .jsonl session log under ~/.claude/projects/E--candidate-package/
  - the instruction files that were in force: ~/.claude/CLAUDE.md and ~/.claude/RTK.md

The logs are copied as they are. The only change is that API keys and other credentials are
replaced by a marker such as [REDACTED-OPENAI-KEY]. Nothing else is edited or removed.

Run it again as the last step before handing in: the log of a session that is still open
keeps growing, and anything said after the last run is not in the copy.
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CLAUDE_HOME = Path.home() / ".claude"
SOURCE = Path(sys.argv[1]) if len(sys.argv) > 1 else CLAUDE_HOME / "projects" / "E--candidate-package"
INSTRUCTION_FILES = ("CLAUDE.md", "RTK.md")

# (what it is, pattern, what replaces it)
SECRETS = (
    ("Anthropic API key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"), "[REDACTED-ANTHROPIC-KEY]"),
    ("OpenAI API key", re.compile(r"sk-proj-[A-Za-z0-9_\-]{20,}"), "[REDACTED-OPENAI-KEY]"),
    ("other sk- key", re.compile(r"sk-(?!ant-|proj-)[A-Za-z0-9]{32,}"), "[REDACTED-API-KEY]"),
    ("GitHub token", re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}"), "[REDACTED-GITHUB-TOKEN]"),
    ("AWS access key", re.compile(r"AKIA[0-9A-Z]{16}"), "[REDACTED-AWS-KEY]"),
    ("Google API key", re.compile(r"AIza[0-9A-Za-z_\-]{35}"), "[REDACTED-GOOGLE-KEY]"),
    ("Slack token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"), "[REDACTED-SLACK-TOKEN]"),
    ("bearer token", re.compile(r"Bearer [A-Za-z0-9_\-\.]{30,}"), "Bearer [REDACTED-TOKEN]"),
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), "[REDACTED-PRIVATE-KEY]"),
)
# Anything that still looks like a key after redaction. A hit here means a pattern above is too narrow.
LEFTOVER = re.compile(r"sk-(?:ant-|proj-)[A-Za-z0-9_\-]{8,}|sk-[A-Za-z0-9]{32,}")


def redact(text: str) -> tuple[str, dict]:
    counts = {}
    for name, pattern, marker in SECRETS:
        text, found = pattern.subn(marker, text)
        if found:
            counts[name] = found
    return text, counts


def main() -> int:
    if not SOURCE.is_dir():
        print(f"error: no session folder at {SOURCE}")
        return 2
    logs = sorted(SOURCE.rglob("*.jsonl"))
    total, leftovers = 0, 0
    for stale in HERE.rglob("*.jsonl"):  # a log that no longer exists at the source
        stale.unlink()
    for log in logs:
        text, counts = redact(log.read_text(encoding="utf-8", errors="surrogateescape"))
        target = HERE / log.relative_to(SOURCE)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", errors="surrogateescape", newline="")
        remaining = len(LEFTOVER.findall(text))
        leftovers += remaining
        total += sum(counts.values())
        print(f"{log.name}  {len(text) / 1e6:6.1f} MB  redacted: {counts or 'nothing'}" + (f"  STILL LOOKS LIKE A KEY: {remaining}" if remaining else ""))

    instructions = HERE / "instructions"
    instructions.mkdir(exist_ok=True)
    for name in INSTRUCTION_FILES:
        source = CLAUDE_HOME / name
        if source.is_file():
            text, counts = redact(source.read_text(encoding="utf-8"))
            (instructions / name).write_text(text, encoding="utf-8")
            total += sum(counts.values())
            print(f"instructions/{name}  redacted: {counts or 'nothing'}")

    print(f"\n{len(logs)} session logs copied to {HERE}; {total} credentials redacted; {leftovers} key-like strings left.")
    return 1 if leftovers else 0


if __name__ == "__main__":
    sys.exit(main())

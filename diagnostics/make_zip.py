"""Build the hand-in zip: the .git folder plus every file git tracks.

    python diagnostics/make_zip.py              writes ../<this folder>-submission.zip
    python diagnostics/make_zip.py out.zip

Windows' "Send to > Compressed folder" and PowerShell's Compress-Archive leave hidden folders
out, and git marks .git as hidden, so a zip made that way arrives with no history. This script
puts .git in on purpose, and leaves out everything git ignores (caches, the virtual
environment, the web app's decisions.json).

Order for the final hand-in:
    python ai-sessions/collect.py       copy the latest session logs, keys redacted
    git add -A  and  git commit         so the zip matches the last commit
    python diagnostics/make_zip.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8").stdout


def main() -> int:
    out = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT.parent / f"{ROOT.name}-submission.zip"
    uncommitted = [line for line in git("status", "--porcelain").splitlines() if line.strip()]
    if uncommitted:
        print(f"warning: {len(uncommitted)} uncommitted change(s). The zip will hold files that are not in the last commit:")
        for line in uncommitted[:10]:
            print("   ", line)
    tracked = [name for name in git("ls-files", "-z").split("\0") if name and (ROOT / name).is_file()]
    history = [path for path in (ROOT / ".git").rglob("*") if path.is_file()]
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name in tracked:
            archive.write(ROOT / name, f"{ROOT.name}/{name}")
        for path in history:
            archive.write(path, f"{ROOT.name}/{path.relative_to(ROOT).as_posix()}")
    megabytes = os.path.getsize(out) / 1e6
    print(f"{out}")
    print(f"{len(tracked)} tracked files and {len(history)} files of .git, {megabytes:.1f} MB")
    print(f"commits: {git('rev-list', '--count', 'HEAD').strip()}, last: {git('log', '-1', '--format=%h %s').strip()}")
    if megabytes > 50:
        print("warning: over the 50 MB limit in the brief")
    elif megabytes > 25:
        print("note: over 25 MB, which many mail servers refuse as an attachment; send a link instead")
    return 0


if __name__ == "__main__":
    sys.exit(main())

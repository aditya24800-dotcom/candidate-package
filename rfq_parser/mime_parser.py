"""MIME layer: turn .eml files into plain dicts, using data/eml_to_json.py.

This layer unpacks the email and hands each attachment to an AttachmentExtractor.
It does not decide anything about the email's meaning.
"""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

from data.eml_to_json import eml_to_json

from .attachments import AttachmentExtractor, unreadable


class MimeParser:
    """Parse every .eml in a directory. One bad email never stops the others."""

    def __init__(self, files_dir: Path | None = None, extractor: AttachmentExtractor | None = None):
        # Attachment bytes are written under files_dir/<email id>/<filename>.
        # With no files_dir, a temporary directory is used and removed afterwards.
        self.extractor = extractor
        self._temp_dir = None
        if files_dir is None:
            self._temp_dir = tempfile.TemporaryDirectory(prefix="rfq_attachments_")
            files_dir = self._temp_dir.name
        self.files_dir = Path(files_dir)

    def parse_file(self, path: Path) -> dict:
        """Return one email as a dict. `error` is None unless the email could not be read.

        Each attachment gains `sha256` (a hash of its bytes, used to spot the same file sent
        twice) and, with an extractor, an `extraction` dict (see attachments/extractor.py).
        """
        path = Path(path)
        try:
            email = {**eml_to_json(path, self.files_dir), "error": None}
        except Exception as exc:  # a broken .eml is recorded, not raised
            return {
                "id": path.stem,
                "headers": {},
                "text": None,
                "html": None,
                "attachments": [],
                "error": f"{type(exc).__name__}: {exc}",
            }
        for attachment in email["attachments"]:
            try:
                data = Path(attachment["path"]).read_bytes()
            except OSError as exc:
                data = None
                problem = f"attachment bytes could not be read ({type(exc).__name__})"
            attachment["sha256"] = hashlib.sha256(data).hexdigest() if data is not None else None
            if self.extractor is not None:
                attachment["extraction"] = (
                    unreadable(attachment["filename"], problem)
                    if data is None
                    else self.extractor.extract(
                        label=email["id"], filename=attachment["filename"], mime=attachment["mime"], data=data
                    )
                )
        return email

    def parse_dir(self, emails_dir: Path) -> dict[str, dict]:
        """Return every .eml in the directory, keyed by filename stem, in name order."""
        return {p.stem: self.parse_file(p) for p in sorted(Path(emails_dir).glob("*.eml"))}

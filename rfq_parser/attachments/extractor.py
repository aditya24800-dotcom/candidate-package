"""Turn one attachment into text: with code when the format allows, with the model when it does not.

extract() never raises. Every attachment comes back as a dict:

    filename, file_type   what it is called and what it really is
    status                "ok" or "unreadable"
    method                "code", "llm", or None when nothing could read it
    kind                  "document", "logo_or_signature" or "other" (the model's call for pictures)
    legible               False when the reader was unsure of part of the content
    text                  the extracted text
    note                  why it is unreadable, or what the reader was unsure of
    children              the same dict for each file inside an archive or an attached email
"""

from __future__ import annotations

import io
import zipfile
from email import policy
from email.parser import BytesParser
from pathlib import PurePosixPath

from ..llm import LLMClient, LLMUnavailable, transcribe
from . import readers
from .readers import Unreadable
from .sniff import IMAGE_MEDIA_TYPES, sniff

MAX_SCANNED_PAGES = 30  # a longer scan is sent to a person, not to the model
MAX_DEPTH = 2  # archive inside archive inside email, and no further
MAX_ARCHIVE_FILES = 25
MAX_ARCHIVE_BYTES = 50 * 1024 * 1024

# Readers that need nothing but the file's bytes.
_CODE_READERS = {
    "xlsx": readers.read_xlsx,
    "csv": readers.read_delimited,
    "docx": readers.read_docx,
    "html": readers.read_html,
    "text": readers.read_text,
}

# Types nothing here can read. A person has to open these.
_UNSUPPORTED = {
    "empty": "file is empty",
    "ole": "legacy Office file (.xls, .doc or .msg) is not supported",
    "rtf": "RTF file is not supported",
    "pptx": "PowerPoint file is not supported",
    "odf": "OpenDocument file is not supported",
    "tiff": "TIFF image is not supported",
    "bmp": "BMP image is not supported",
    "heic": "HEIC image is not supported",
    "unknown": "unrecognised file type",
}


def result(filename, file_type, *, status, method=None, kind=None, legible=True, text="", note=None, children=()):
    return {
        "filename": filename,
        "file_type": file_type,
        "status": status,
        "method": method,
        "kind": kind,
        "legible": legible and status == "ok",
        "text": text,
        "note": note,
        "children": list(children),
    }


def unreadable(filename: str, note: str, file_type: str = "unknown") -> dict:
    return result(filename, file_type, status="unreadable", note=note)


class AttachmentExtractor:
    def __init__(self, llm: LLMClient | None = None):
        self.llm = llm

    def extract(self, *, label: str, filename: str, mime: str, data: bytes, depth: int = 0) -> dict:
        """`label` is the email id, used for the model call ledger."""
        file_type = "unknown"
        try:
            file_type = sniff(data, filename, mime)
            return self._extract(file_type, label, filename, data, depth)
        except Unreadable as exc:
            return unreadable(filename, str(exc), file_type)
        except Exception as exc:  # damaged file: report it, never raise
            return unreadable(filename, f"file could not be opened ({type(exc).__name__})", file_type)

    def _extract(self, file_type: str, label: str, filename: str, data: bytes, depth: int) -> dict:
        if file_type in _UNSUPPORTED:
            raise Unreadable(_UNSUPPORTED[file_type])
        if file_type in _CODE_READERS:
            return self._from_code(filename, file_type, _CODE_READERS[file_type](data))
        if file_type == "pdf":
            text, pages = readers.read_pdf(data)
            if text is not None:
                return self._from_code(filename, file_type, text)
            if pages > MAX_SCANNED_PAGES:
                raise Unreadable(f"scanned PDF has {pages} pages, more than the {MAX_SCANNED_PAGES} sent to the model")
            return self._from_model(label, filename, file_type, "application/pdf", data)
        if file_type in IMAGE_MEDIA_TYPES:
            return self._from_model(label, filename, file_type, IMAGE_MEDIA_TYPES[file_type], data)
        if file_type == "eml":
            return self._from_email(label, filename, data, depth)
        return self._from_zip(label, filename, data, depth)

    @staticmethod
    def _from_code(filename: str, file_type: str, text: str) -> dict:
        if not text.strip():
            raise Unreadable("no readable text in the file")
        return result(filename, file_type, status="ok", method="code", kind="document", text=text)

    def _from_model(self, label: str, filename: str, file_type: str, media_type: str, data: bytes) -> dict:
        if self.llm is None:
            raise Unreadable("needs the model to read it, and no model client was given")
        try:
            answer = transcribe(self.llm, label=label, mime=media_type, data=data)
        except LLMUnavailable as exc:
            raise Unreadable(f"needs the model to read it: {exc}") from exc
        return result(
            filename,
            file_type,
            status="ok",
            method="llm",
            kind=answer["kind"],
            legible=answer["legible"],
            text=answer["text"],
            note=answer["note"],
        )

    def _from_email(self, label: str, filename: str, data: bytes, depth: int) -> dict:
        """An attached message: its headers and body as text, its own attachments as children."""
        message = BytesParser(policy=policy.default).parsebytes(data)
        header = "\n".join(f"{name}: {message[name]}" for name in ("From", "To", "Date", "Subject") if message[name])
        body = message.get_body(preferencelist=("plain", "html"))
        text = body.get_content() if body is not None else ""
        if body is not None and body.get_content_type() == "text/html":
            text = readers.html_to_text(text)
        children = []
        if depth < MAX_DEPTH:
            for part in message.iter_attachments():
                nested = part.get_content_type() == "message/rfc822"
                payload = part.get_payload(0).as_bytes() if nested else part.get_payload(decode=True)
                children.append(
                    self.extract(
                        label=label,
                        filename=part.get_filename() or "unnamed",
                        mime=part.get_content_type(),
                        data=payload or b"",
                        depth=depth + 1,
                    )
                )
        return result(
            filename, "eml", status="ok", method="code", kind="document", text=f"{header}\n\n{text}".strip(), children=children
        )

    def _from_zip(self, label: str, filename: str, data: bytes, depth: int) -> dict:
        if depth >= MAX_DEPTH:
            raise Unreadable("archive is nested too deeply to unpack")
        children = []
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = [m for m in archive.infolist() if not m.is_dir() and not m.filename.startswith("__MACOSX/")]
            if not members:
                raise Unreadable("archive is empty")
            if len(members) > MAX_ARCHIVE_FILES or sum(m.file_size for m in members) > MAX_ARCHIVE_BYTES:
                raise Unreadable("archive is too large to unpack")
            for member in members:
                name = PurePosixPath(member.filename).name
                try:
                    payload = archive.read(member)
                except Exception as exc:  # a password-protected or damaged member
                    children.append(unreadable(name, f"could not be unpacked from the archive ({type(exc).__name__})"))
                    continue
                children.append(self.extract(label=label, filename=name, mime="", data=payload, depth=depth + 1))
        return result(filename, "zip", status="ok", method="code", kind="document", children=children)

"""Work out what a file really is. Content wins over the name, because attachments get mislabelled."""

from __future__ import annotations

import io
import zipfile
from pathlib import PurePath

# Image types both model providers accept, with the media type to send.
IMAGE_MEDIA_TYPES = {"png": "image/png", "jpeg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}

_HEIC_BRANDS = (b"heic", b"heix", b"heif", b"mif1", b"msf1")


def sniff(data: bytes, filename: str = "", mime: str = "") -> str:
    """Return a short type name: pdf, xlsx, docx, csv, text, html, eml, zip, png, ... or unknown."""
    if not data.strip():
        return "empty"
    head = data[:16]
    if data.lstrip()[:5] == b"%PDF-":
        return "pdf"
    if head.startswith(b"PK\x03\x04"):
        return _zip_kind(data)
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"
    if head[4:8] == b"ftyp" and head[8:12] in _HEIC_BRANDS:
        return "heic"
    if head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return "ole"  # legacy Office container: .xls, .doc, .msg
    if head.startswith(b"{\\rtf"):
        return "rtf"

    # No binary signature, so the name and the declared type decide.
    extension = PurePath(filename).suffix.lower()
    mime = (mime or "").lower()
    if extension == ".bmp" and head[:2] == b"BM":
        return "bmp"
    if mime == "message/rfc822" or extension == ".eml":
        return "eml"
    start = data[:2048].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if extension in (".html", ".htm") or mime == "text/html" or start.startswith((b"<!doctype html", b"<html")):
        return "html"
    if extension in (".csv", ".tsv") or mime in ("text/csv", "text/tab-separated-values"):
        return "csv"
    if mime.startswith("text/") or _looks_like_text(data):
        return "text"
    return "unknown"


def _zip_kind(data: bytes) -> str:
    """Office files are zip archives; tell them apart by what is inside."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = set(archive.namelist())
    except zipfile.BadZipFile:
        return "zip"  # the zip reader will report the damage
    if "xl/workbook.xml" in names:
        return "xlsx"
    if "word/document.xml" in names:
        return "docx"
    if "ppt/presentation.xml" in names:
        return "pptx"
    if "mimetype" in names and "content.xml" in names:
        return "odf"
    return "zip"


def _looks_like_text(data: bytes) -> bool:
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):  # UTF-16 byte order mark
        return True
    sample = data[:4096]
    if b"\x00" in sample:
        return False
    control = sum(1 for byte in sample if byte < 32 and byte not in (9, 10, 13))
    return control <= len(sample) // 100

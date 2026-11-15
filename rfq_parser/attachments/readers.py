"""Readers that turn a file into text with code alone, no model.

Shared conventions, which the model's instructions rely on:
- a table row is one line, its cells separated by " | ";
- crossed-out text is wrapped as ~~text~~;
- a hidden sheet or hidden row is marked as hidden, not dropped: code reports the fact and
  the model decides what it means.

A reader raises Unreadable when it knows why the file cannot be read. Any other exception
means the file is damaged; the extractor turns both into an "unreadable" result.
"""

from __future__ import annotations

import csv
import io
import itertools
import re
import zipfile
from datetime import datetime
from html.parser import HTMLParser
from xml.etree import ElementTree

import openpyxl
from pypdf import PdfReader

# A page with less text than this and an image on it is treated as a scan.
MIN_PAGE_TEXT = 20


class Unreadable(Exception):
    """The file cannot be read, for a reason worth showing to a person."""


def decode_text(data: bytes) -> str:
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", "replace")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", "replace")


def _strike(text: str) -> str:
    return f"~~{text}~~"


# --- spreadsheets -------------------------------------------------------------------------


def read_xlsx(data: bytes) -> str:
    workbook = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
    blocks = []
    for sheet in workbook.worksheets:
        hidden = " - HIDDEN SHEET" if sheet.sheet_state != "visible" else ""
        lines = [f'[sheet "{sheet.title}"{hidden}]']
        fill = _vertical_merge_values(sheet)
        for row in sheet.iter_rows():
            cells = [_cell_text(cell) or fill.get((cell.row, cell.column), "") for cell in row]
            while cells and not cells[-1]:
                cells.pop()
            if not cells:
                continue
            line = " | ".join(cells)
            if sheet.row_dimensions[row[0].row].hidden:
                line = "[hidden row] " + line
            lines.append(line)
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _cell_text(cell) -> str:
    value = cell.value
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, datetime) and value.time() == value.min.time():
        value = value.date()
    text = " ".join(str(value).split())
    if text and cell.font is not None and cell.font.strike:
        text = _strike(text)
    return text


def _vertical_merge_values(sheet) -> dict:
    """A cell merged down several rows applies to each of them, so repeat its text on each row."""
    fill = {}
    for merged in sheet.merged_cells.ranges:
        if merged.max_row == merged.min_row:
            continue
        text = _cell_text(sheet.cell(merged.min_row, merged.min_col))
        for row in range(merged.min_row + 1, merged.max_row + 1):
            fill[(row, merged.min_col)] = text
    return fill


def read_delimited(data: bytes) -> str:
    text = decode_text(data)
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = csv.reader(io.StringIO(text), dialect)
    return "\n".join(" | ".join(cell.strip() for cell in row) for row in rows if any(cell.strip() for cell in row))


# --- Word ---------------------------------------------------------------------------------

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def read_docx(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        root = ElementTree.fromstring(archive.read("word/document.xml"))
    lines = []
    for block in _docx_blocks(root.find(f"{_W}body")):
        if block.tag == f"{_W}p":
            text = _docx_paragraph(block)
            if text:
                lines.append(("# " if _docx_is_heading(block) else "") + text)
        else:
            for row in block.iter(f"{_W}tr"):
                cells = [
                    " ".join(filter(None, (_docx_paragraph(p) for p in cell.iter(f"{_W}p"))))
                    for cell in row.findall(f"{_W}tc")
                ]
                if any(cells):
                    lines.append(" | ".join(cells))
    return "\n".join(lines)


def _docx_blocks(parent):
    """Paragraphs and tables in document order, looking inside content controls."""
    for child in parent if parent is not None else ():
        if child.tag in (f"{_W}p", f"{_W}tbl"):
            yield child
        elif child.tag == f"{_W}sdt":
            yield from _docx_blocks(child.find(f"{_W}sdtContent"))


def _docx_is_heading(paragraph) -> bool:
    style = paragraph.find(f"{_W}pPr/{_W}pStyle")
    name = (style.get(f"{_W}val") or "").lower() if style is not None else ""
    return name.startswith("heading") or name == "title"


def _docx_paragraph(paragraph) -> str:
    pieces = []  # (text, crossed out?)

    def walk(node, deleted):
        for child in node:
            if child.tag == f"{_W}r":
                text = "".join(_docx_run_piece(part) for part in child)
                pieces.append((text, deleted or _docx_run_is_struck(child)))
            elif child.tag in (f"{_W}del", f"{_W}moveFrom"):
                walk(child, True)  # a tracked deletion shows as crossed out in Word
            else:
                walk(child, deleted)

    walk(paragraph, False)
    out = []
    for struck, group in itertools.groupby(pieces, key=lambda piece: piece[1]):
        text = "".join(piece[0] for piece in group)
        out.append(_strike(text.strip()) if struck and text.strip() else text)
    return " ".join("".join(out).split())


def _docx_run_piece(part) -> str:
    if part.tag in (f"{_W}t", f"{_W}delText"):
        return part.text or ""
    if part.tag in (f"{_W}tab", f"{_W}br"):
        return " "
    return ""


def _docx_run_is_struck(run) -> bool:
    for tag in ("strike", "dstrike"):
        mark = run.find(f"{_W}rPr/{_W}{tag}")
        if mark is not None and mark.get(f"{_W}val") not in ("0", "false"):
            return True
    return False


# --- PDF ----------------------------------------------------------------------------------


def read_pdf(data: bytes) -> tuple[str | None, int]:
    """Return (text, page count). The text is None when a page is a scan and needs a model."""
    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        try:
            opened = reader.decrypt("")  # some PDFs are "protected" with an empty password
        except Exception:
            opened = 0
        if not opened:
            raise Unreadable("PDF is password-protected")
    pages = []
    for page in reader.pages:
        text = _pdf_page_text(page)
        if len(text) < MIN_PAGE_TEXT and _pdf_page_has_image(page):
            return None, len(reader.pages)
        pages.append(text)
    out = [f"--- page {number} ---\n{text}" for number, text in enumerate(pages, 1) if text]
    try:
        fields = {name: field.get("/V") for name, field in (reader.get_fields() or {}).items()}
    except Exception:  # a broken form dictionary must not cost us the page text
        fields = {}
    filled = [f"{name}: {value}" for name, value in fields.items() if value not in (None, "")]
    if filled:  # a fillable form keeps its answers in fields, not in the page text
        out.append("--- form fields ---\n" + "\n".join(filled))
    return "\n".join(out), len(reader.pages)


def _pdf_page_text(page) -> str:
    """Layout mode keeps a table row on one line; the gaps between columns become " | "."""
    try:
        text = page.extract_text(extraction_mode="layout")
    except Exception:
        text = page.extract_text()
    lines = (re.sub(r" {2,}", " | ", line.strip()) for line in (text or "").splitlines())
    return "\n".join(line for line in lines if line)


def _pdf_page_has_image(page) -> bool:
    try:
        xobjects = page["/Resources"]["/XObject"]
        return any(xobjects[name].get("/Subtype") == "/Image" for name in xobjects)
    except Exception:
        return False


# --- HTML and plain text ------------------------------------------------------------------

_HTML_SKIP = {"script", "style", "head", "title"}
_HTML_BLOCK = {"p", "div", "br", "li", "ul", "ol", "table", "tr", "blockquote", "hr", "pre"}
_HTML_HEADING = {"h1", "h2", "h3", "h4", "h5", "h6"}
_HTML_STRIKE = {"s", "strike", "del"}
# Lines the converter writes by itself to mark where a <blockquote> opens and closes.
_QUOTE_OPEN, _QUOTE_CLOSE = "\x01", "\x02"


class _HtmlToText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []
        self.skipping = 0
        self.first_cell = True
        self.struck_tags = []  # open tags that started crossed-out text

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in _HTML_SKIP:
            self.skipping += 1
        elif tag == "tr":
            self.out.append("\n")
            self.first_cell = True
        elif tag in ("td", "th"):
            if not self.first_cell:
                self.out.append(" | ")
            self.first_cell = False
        elif tag == "img":
            self.out.append(f" [image: {attrs.get('src') or attrs.get('alt') or ''}] ")
        elif tag in _HTML_HEADING:
            self.out.append("\n# ")
        elif tag == "blockquote":
            self.out.append(f"\n{_QUOTE_OPEN}\n")
        elif tag in _HTML_BLOCK:
            self.out.append("\n")
        if tag in _HTML_STRIKE or "line-through" in (attrs.get("style") or ""):
            self.struck_tags.append(tag)
            self.out.append("~~")

    def handle_endtag(self, tag):
        if tag in _HTML_SKIP:
            self.skipping = max(0, self.skipping - 1)
        if self.struck_tags and self.struck_tags[-1] == tag:
            self.struck_tags.pop()
            self.out.append("~~")
        if tag == "blockquote":
            self.out.append(f"\n{_QUOTE_CLOSE}\n")
        elif tag in _HTML_BLOCK or tag in _HTML_HEADING:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skipping:
            self.out.append(data)


def html_to_text(html: str) -> str:
    """Text of an HTML document. Lines inside <blockquote> get "> " marks, as plain-text mail has."""
    parser = _HtmlToText()
    parser.feed(html)
    parser.close()
    depth, lines = 0, []
    for raw in "".join(parser.out).splitlines():
        if raw == _QUOTE_OPEN:
            depth += 1
        elif raw == _QUOTE_CLOSE:
            depth = max(0, depth - 1)
        else:
            line = " ".join(raw.split())
            if line:
                lines.append("> " * depth + line)
    return "\n".join(lines)


def read_html(data: bytes) -> str:
    return html_to_text(decode_text(data))


def read_text(data: bytes) -> str:
    return decode_text(data).strip()

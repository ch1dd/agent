from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import re
from textwrap import wrap

from scans import IMAGE_TYPES, image_sections, pdf_sections, recognize

SUPPORTED = {".txt", ".md", ".pdf", ".doc", ".docx"} | IMAGE_TYPES
CHUNK_SIZE = 1300
CHUNK_OVERLAP = 200


def published_faq(name, path):
    """Read a knowledge card published by the PHP ticket handler."""
    if not re.fullmatch(r"faq_ticket_[0-9]+\.txt", name):
        return None
    text = path.read_text(encoding="utf-8-sig").strip()
    question, sep, answer = text.removeprefix("Вопрос:").partition("\n\nОтвет:")
    if text.startswith("Вопрос:") and sep and question.strip() and answer.strip():
        return question.strip(), answer.strip()
    return None


@dataclass(frozen=True)
class Passage:
    source: str
    location: str
    body: str
    topic: str = ""

    def search_text(self):
        return "\n".join((self.source, self.location, self.topic, self.body))


def outline_title(outline, depth, title):
    while outline and outline[-1][0] >= depth:
        outline.pop()
    outline.append((depth, title))
    return " / ".join(text for _, text in outline)


def embedded_scans(element, part, location):
    from docx.oxml.ns import qn
    from PIL import Image

    for blip in element.xpath(".//a:blip[@r:embed]"):
        image = part.related_parts[blip.get(qn("r:embed"))]
        if image.content_type not in {"image/png", "image/jpeg", "image/tiff", "image/bmp", "image/webp"}:
            continue
        with Image.open(BytesIO(image.blob)) as bitmap:
            text = recognize(bitmap, location, illustration=True)
        if text:
            yield text


def sections(path):
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        yield from pdf_sections(path)
    elif suffix in IMAGE_TYPES:
        yield from image_sections(path)
    elif suffix == ".doc":
        from legacy_doc import extract_text
        yield "", extract_text(path.read_bytes()).text
    elif suffix == ".docx":
        from docx import Document
        from docx.text.paragraph import Paragraph
        heading, lines, outline = "", [], []
        for item in Document(path).iter_inner_content():
            if isinstance(item, Paragraph):
                level = re.fullmatch(r"Heading (\d+)", item.style.name or "") if item.style else None
                if level:
                    yield heading, "\n".join(lines)
                    heading = outline_title(outline, int(level[1]), item.text)
                    lines = []
                else:
                    if (item._element.xpath("./w:pPr/w:numPr") or
                            (item.style and item.style.name.startswith("List Number"))):
                        yield heading, "\n".join(lines)
                        lines = []
                    lines.append(item.text)
            else:
                yield heading, "\n".join(lines)
                lines = []
                headers = ""
                for i, row in enumerate(item.rows):
                    values = " | ".join(c.text.strip().replace("\n", " ") for c in row.cells)
                    location = f"таблица · строка {i+1}"
                    if i and headers:
                        location += " · первая строка: " + headers
                    yield " · ".join(filter(None, (heading, location))), values
                    if i == 0:
                        headers = values
            for text in embedded_scans(item._element, item.part, path.name):
                yield heading, "\n".join(lines)
                lines = []
                label = " · ".join(filter(None, (heading, "вложенный скан", "OCR")))
                yield label, text
        yield heading, "\n".join(lines)
    else:
        heading, lines, outline = "", [], []
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            level = re.match(r"^(#{1,6})\s+(.+)", line) if suffix == ".md" else None
            if level:
                yield heading, "\n".join(lines)
                heading = outline_title(outline, len(level[1]), level[2].strip())
                lines = []
            else:
                lines.append(line)
        yield heading, "\n".join(lines)


def evidence_units(body):
    """Units are copied into replies by index, including from older indexed documents."""
    units = []
    for line in body.splitlines():
        number = re.match(r"^\s*\d+(?:\.\d+)*[.)]\s+", line)
        start = number.end() if number else 0
        parts = [line[start:]] if "|" in line or "\t" in line else re.split(
            r"(?<!\b[А-ЯA-Z]\.)(?<=[.!?])\s+(?=[А-ЯЁA-Z0-9«])", line[start:])
        parts[0] = line[:start] + parts[0]
        for part in parts:
            part = part.strip()
            if len(re.findall(r"\w", part)) >= 2 or re.search(r"\d", part):
                units.append(part)
    return units


def blocks(body):
    current = []
    for line in body.splitlines():
        if current and re.match(r"^\s*\d+(?:\.\d+)*(?:[.)]|\s*[-–])\s+", line):
            yield current
            current = []
        current.extend(evidence_units(line))
    if current:
        yield current


def chunks(units):
    current = []
    for unit in units:
        if len(unit) > CHUNK_SIZE:
            if current:
                yield "\n".join(current)
                current = []
            # A single very long sentence has no sentence boundary to overlap.
            yield from wrap(unit, width=CHUNK_SIZE, break_on_hyphens=False)
            continue
        if len("\n".join([*current, unit])) > CHUNK_SIZE:
            yield "\n".join(current)
            while current and (len("\n".join(current)) > CHUNK_OVERLAP or
                               len("\n".join([*current, unit])) > CHUNK_SIZE):
                current.pop(0)
        current.append(unit)
    if current:
        yield "\n".join(current)


def passages(path: Path, source: str):
    number = 0
    for heading, body in sections(path):
        for block in blocks(body):
            for chunk in chunks(block):
                if not evidence_units(chunk):
                    continue
                number += 1
                location = f"{heading} · фрагмент {number}" if heading else f"фрагмент {number}"
                yield Passage(source, location, chunk)

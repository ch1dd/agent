from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import re

from scans import IMAGE_TYPES, image_sections, pdf_sections, recognize

SUPPORTED = {".txt", ".md", ".pdf", ".doc", ".docx"} | IMAGE_TYPES
CHUNK_SIZE = 1300


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
                    lines.append(item.text)
            else:
                # Preserve the order and contents of the cells.
                for row in item.rows:
                    lines.append("\t".join(c.text for c in row.cells))
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


def passages(path: Path, source: str):
    number = 0
    for heading, body in sections(path):
        rest = body.strip()
        while rest:
            end = len(rest)
            if end > CHUNK_SIZE:
                window = rest[:CHUNK_SIZE]
                for delimiter in ("\n", ". ", " "):
                    split = window.rfind(delimiter)
                    if split >= CHUNK_SIZE // 2:
                        end = split + len(delimiter)
                        break
                else:
                    end = CHUNK_SIZE
            number += 1
            location = f"{heading} · фрагмент {number}" if heading else f"фрагмент {number}"
            yield Passage(source, location, rest[:end].strip())
            rest = rest[end:].strip()

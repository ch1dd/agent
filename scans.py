from contextlib import closing
from math import sqrt
from threading import Lock

import pypdfium2 as pdfium
import pypdfium2.raw as pdfraw
from PIL import Image, ImageOps, ImageSequence
from tesserocr import PyTessBaseAPI

from config import TESSDATA

IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
PDF_LOCK = Lock()  # PDFium requires serialized access even across different files.


def recognize(image, location, *, illustration=False):
    image = ImageOps.exif_transpose(image).convert("RGBA")
    background = Image.new("RGB", image.size, "white")
    background.paste(image, mask=image.getchannel("A"))
    gray = ImageOps.grayscale(background)
    if gray.getextrema()[0] >= 248:
        return ""
    if not all((TESSDATA / f"{lang}.traineddata").is_file() for lang in ("rus", "eng")):
        raise RuntimeError("Для распознавания сканов запустите python setup_ocr.py.")
    with PyTessBaseAPI(path=str(TESSDATA), lang="rus+eng") as engine:
        engine.SetImage(gray)
        text = engine.GetUTF8Text().strip()
        confidence = engine.MeanTextConf()
    if text and confidence >= 60:
        return text
    if illustration and not text:
        return ""
    raise ValueError(f"{location}: OCR не смог уверенно прочитать текст. Проверьте качество скана.")


def image_sections(path):
    with Image.open(path) as image:
        for i, frame in enumerate(ImageSequence.Iterator(image), 1):
            label = f"стр. {i} · OCR"
            yield label, recognize(frame, f"{path.name}, {label}")


def pdf_page(page, location):
    image_bounds = []
    with closing(page.get_textpage()) as text_page:
        native = text_page.get_text_bounded().replace("\r\n", "\n").strip()
        # A page number or letterhead can coexist with a scan of the regulation.
        # Inspect substantial image regions for an existing searchable layer.
        width, height = page.get_size()
        if native:
            for obj in page.get_objects(filter=[pdfraw.FPDF_PAGEOBJ_IMAGE]):
                left, bottom, right, top = obj.get_bounds()
                if (right - left) * (top - bottom) < width * height * 0.08:
                    continue
                overlay = text_page.get_text_bounded(left, bottom, right, top)
                if sum(c.isalpha() for c in overlay) < 20:
                    image_bounds.append((left, bottom, right, top))
    sections = [(location, native)] if native else []
    if native and not image_bounds:
        return sections
    # Render the visible page, including rotation and cropping. Limit bitmap
    # allocation on oversized sheets while targeting 300 dpi on ordinary pages.
    scale = min(300 / 72, sqrt(16_000_000 / max(1, width * height)))
    with closing(page.render(scale=scale, grayscale=True)) as bitmap:
        image = bitmap.to_pil()
        regions = []
        if image_bounds:
            converter = bitmap.get_posconv(page)
            for left, bottom, right, top in image_bounds:
                x1, y1 = converter.to_bitmap(left, bottom)
                x2, y2 = converter.to_bitmap(right, top)
                x1, x2 = max(0, min(x1, x2)), min(image.width, max(x1, x2))
                y1, y2 = max(0, min(y1, y2)), min(image.height, max(y1, y2))
                if x1 < x2 and y1 < y2:
                    regions.append((x1, y1, x2, y2))
        else:
            regions.append((0, 0, image.width, image.height))
        for box in dict.fromkeys(regions):
            text = recognize(image.crop(box), location, illustration=bool(native))
            if text and text not in native:
                sections.append((f"{location} · OCR", text))
    return sections


def pdf_sections(path):
    with PDF_LOCK, closing(pdfium.PdfDocument(path)) as document:
        for i in range(len(document)):
            label = f"стр. {i + 1}"
            with closing(document[i]) as page:
                try:
                    sections = pdf_page(page, label)
                except ValueError as exc:
                    raise ValueError(f"{path.name}: {exc}") from exc
            yield from sections

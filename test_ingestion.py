"""Проверить форматы и настоящее распознавание без Ollama: python -m unittest test_ingestion."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from docx import Document
from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from documents import passages


def scan(text):
    fonts = [Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/arial.ttf", "DejaVuSans.ttf"]
    for font_path in fonts:
        try:
            font = ImageFont.truetype(str(font_path), 44)
            break
        except OSError:
            continue
    else:
        raise RuntimeError("Для тестового скана нужен шрифт Arial или DejaVu Sans.")
    image = Image.new("RGB", (1550, 360), "white")
    ImageDraw.Draw(image).text((60, 120), text, fill="black", font=font)
    return image


class IngestionTest(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.line = "Договор подписывает директор."
        self.image = scan(self.line)

    def read(self, path):
        return list(passages(path, path.name))

    def test_images_and_tiff_pages(self):
        for ext in ("png", "jpg", "bmp", "webp"):
            with self.subTest(ext=ext):
                path = self.root / f"скан.{ext}"
                self.image.save(path)
                p, = self.read(path)
                self.assertEqual(p.body, self.line)
                self.assertIn("OCR", p.location)
        path = self.root / "страницы.tiff"
        second = "Срок регистрации три дня."
        self.image.save(path, save_all=True, append_images=[scan(second)])
        p1, p2 = self.read(path)
        self.assertEqual((p1.body, p2.body), (self.line, second))
        self.assertIn("стр. 2", p2.location)

    def test_native_and_scanned_pdf(self):
        writer = PdfWriter()
        native = writer.add_blank_page(600, 400)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                                 NameObject("/Subtype"): NameObject("/Type1"),
                                 NameObject("/BaseFont"): NameObject("/Helvetica")})
        native["/Resources"][NameObject("/Font")] = DictionaryObject({NameObject("/F1"): font})
        stream = DecodedStreamObject()
        stream.set_data(b"BT /F1 14 Tf 30 330 Td (Registration policy) Tj ET")
        native[NameObject("/Contents")] = writer._add_object(stream)
        img_pdf = self.root / "scan.pdf"
        self.image.save(img_pdf, resolution=150)
        reader = PdfReader(img_pdf)
        writer.add_page(reader.pages[0])
        hybrid = writer.add_page(reader.pages[0])
        hybrid.merge_page(native, expand=True)
        path = self.root / "mixed.pdf"
        writer.write(path)
        p1, p2, heading, p3 = self.read(path)
        self.assertEqual(p1.body, "Registration policy")
        self.assertNotIn("OCR", p1.location)
        self.assertEqual(p2.body, self.line)
        self.assertIn("стр. 2 · OCR", p2.location)
        self.assertIn(self.line, p3.body)
        self.assertEqual(heading.body, "Registration policy")
        self.assertNotIn("OCR", heading.location)

    def test_word_table_and_embedded_scan(self):
        img = self.root / "scan.png"
        self.image.save(img)
        word = Document()
        word.add_heading("Регистрация", level=1)
        word.add_paragraph("Текст из Word.")
        word.add_table(rows=1, cols=1).cell(0, 0).text = "Ячейка таблицы"
        word.add_picture(str(img))
        path = self.root / "регламент.docx"
        word.save(path)
        native, ocr = self.read(path)
        self.assertIn("Ячейка таблицы", native.body)
        self.assertIn("Текст из Word.", native.body)
        self.assertEqual(ocr.body, self.line)
        self.assertIn("Регистрация · вложенный скан · OCR", ocr.location)

"""Загрузить языковые модели Tesseract для локального OCR."""
import hashlib
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.request import urlopen
from shutil import copyfileobj

from config import TESSDATA

MODELS = {
    "rus": "e16e5e036cce1d9ec2b00063cf8b54472625b9e14d893a169e2b0dedeb4df225",
    "eng": "7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2",
}


def install():
    TESSDATA.mkdir(parents=True, exist_ok=True)
    for lang, checksum in MODELS.items():
        target = TESSDATA / f"{lang}.traineddata"
        if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == checksum:
            continue
        url = f"https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/4.1.0/{target.name}"
        with NamedTemporaryFile(dir=TESSDATA, delete=False) as tmp:
            partial = Path(tmp.name)
        try:
            with urlopen(url, timeout=90) as response, partial.open("wb") as output:
                copyfileobj(response, output)
            if hashlib.sha256(partial.read_bytes()).hexdigest() != checksum:
                raise ValueError(f"Не совпала контрольная сумма модели {lang}. Повторите загрузку.")
            partial.replace(target)
        finally:
            partial.unlink(missing_ok=True)
    print(f"Модели OCR: {TESSDATA.resolve()}")


if __name__ == "__main__":
    install()

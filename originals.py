"""Immutable source bytes for ingestion and historical citations."""
import hashlib
import os
from pathlib import Path
from tempfile import NamedTemporaryFile


def snapshot(data, stream, suffix):
    folder = data / "originals"
    folder.mkdir(parents=True, exist_ok=True)
    checksum = hashlib.sha256()
    with NamedTemporaryFile(dir=folder, delete=False) as tmp:
        path = Path(tmp.name)
    try:
        with path.open("wb") as out:
            while block := stream.read(1024 * 1024):
                checksum.update(block)
                out.write(block)
        blob = checksum.hexdigest() + suffix.lower()
        if not (folder / blob).exists():
            os.replace(path, folder / blob)
        return blob
    finally:
        path.unlink(missing_ok=True)

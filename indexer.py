"""python indexer.py [каталог_с_действующими_регламентами]"""
import argparse
import hashlib
from pathlib import Path

from config import Config
from documents import SUPPORTED, passages
from ollama_api import ModelService
from storage import Store, digest
from qdrant_client import models as qm


def sync(store, directory):
    directory = directory.resolve()
    if not directory.is_dir():
        raise ValueError(f"Каталог не найден: {directory}")
    files = sorted(p for p in directory.rglob("*") if p.is_file() and
                   p.suffix.lower() in SUPPORTED and not p.name.startswith("~$"))
    if not files:
        raise ValueError("В каталоге нет поддерживаемых документов. Предыдущий индекс сохранён.")
    updated, active = 0, {}
    with store.db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        old = dict(conn.execute("SELECT source,version FROM active WHERE kind='file'").fetchall())
        for path in files:
            source = path.relative_to(directory).as_posix()
            with path.open("rb") as stream:
                checksum = hashlib.file_digest(stream, "sha256").hexdigest()
            parser = "scan-v2" if path.suffix.lower() in {".docx", ".pdf"} else "file-v1"
            version = digest(f"{parser}\0{source}\0{checksum}")
            active[source] = version
            if old.get(source) == version:
                continue
            chunks = list(passages(path, source))
            if not chunks:
                raise ValueError(f"В документе не найден читаемый текст: {source}. Проверьте оригинал.")
            store.write_passages(version, chunks)
            updated += 1
        conn.execute("DELETE FROM active WHERE kind='file'")
        conn.executemany("INSERT INTO active VALUES (?,?,'file')", active.items())
    # Another writer may have reactivated one of the retired versions.
    with store.db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        live = {row[0] for row in conn.execute("SELECT version FROM active")}
        retired = set(old.values()) - live
        if retired:
            with store.lock:
                store.client.delete(store.config.collection, qm.Filter(must=[
                    qm.FieldCondition(key="version", match=qm.MatchAny(any=list(retired)))]), wait=True)
    return f"Документов: {len(active)}. Обновлено: {updated}. Убрано: {len(old.keys() - active.keys())}."


if __name__ == "__main__":
    config = Config()
    parser = argparse.ArgumentParser(description="Синхронизировать индекс с выбранной папкой целиком")
    parser.add_argument("directory", nargs="?", type=Path, default=config.documents)
    args = parser.parse_args()
    store = Store(config, ModelService(config))
    try:
        print(sync(store, args.directory))
    finally:
        store.client.close()

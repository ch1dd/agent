"""python indexer.py [каталог_с_действующими_регламентами]"""
import argparse
import sqlite3
from contextlib import closing
from pathlib import Path

from config import Config
from documents import SUPPORTED, passages
from ollama_api import ModelService
from storage import Store, digest
from originals import snapshot


def prepare(store, source, blob):
    path = store.config.data / "originals" / blob
    parser = "scan-v2" if path.suffix in {".docx", ".pdf"} else "file-v1"
    version = digest(f"{parser}\0{source}\0{path.stem}")
    with store.db() as conn:
        known = conn.execute("SELECT 1 FROM originals WHERE version=?", (version,)).fetchone()
        indexed = conn.execute("SELECT 1 FROM active WHERE version=?", (version,)).fetchone()
    if not known and not indexed:
        chunks = list(passages(path, source))
        if not chunks:
            raise ValueError(f"В документе не найден читаемый текст: {source}.")
        store.write_passages(version, chunks)
    return version


def activate_upload(store, name, blob):
    source = f"Загрузки/{name}"
    version = prepare(store, source, blob)
    with store.db() as conn:
        conn.execute("INSERT OR REPLACE INTO originals VALUES (?,?,?)", (version, name, blob))
        conn.execute("INSERT OR REPLACE INTO active VALUES (?,?,'upload')", ("upload:" + name, version))


def sync(store, directory):
    # Serialize folder snapshots across CLI and service without locking chat writes.
    with closing(sqlite3.connect(store.config.data / "folder-sync.sqlite3", timeout=60)) as guard:
        guard.execute("BEGIN IMMEDIATE")
        return _sync(store, directory)


def _sync(store, directory):
    directory = directory.resolve()
    if not directory.is_dir():
        raise ValueError(f"Каталог не найден: {directory}")
    files = sorted(p for p in directory.rglob("*") if p.is_file() and
                   p.suffix.lower() in SUPPORTED and not p.name.startswith("~$"))
    if not files:
        raise ValueError("В каталоге нет поддерживаемых документов. Предыдущий индекс сохранён.")
    with store.db() as conn:
        old = dict(conn.execute("SELECT source,version FROM active WHERE kind='file'").fetchall())
    active, originals = {}, []
    for path in files:
        source = path.relative_to(directory).as_posix()
        with path.open("rb") as stream:
            blob = snapshot(store.config.data, stream, path.suffix)
        version = prepare(store, source, blob)
        active[source] = version
        originals.append((version, path.name, blob))
    with store.db() as conn:
        conn.executemany("INSERT OR REPLACE INTO originals VALUES (?,?,?)", originals)
        conn.execute("DELETE FROM active WHERE kind='file'")
        conn.executemany("INSERT INTO active VALUES (?,?,'file')", active.items())
    updated = sum(old.get(key) != version for key, version in active.items())
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

"""Revoke a source and its dependent answers before reclaiming stored content."""
import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path

from qdrant_client import models as qm

from storage import digest


def dependencies(result):
    return set(result.get("depends_on") or ()) | {
        hit["version"] for hit in result.get("hits", []) if hit.get("version")
    }


def turn_key(owner, request):
    return "turn:" + digest(json.dumps([owner, request]))


def sources(store):
    with store.db() as conn:
        return [dict(row) for row in conn.execute(
            "SELECT source,kind FROM active ORDER BY source")]


def mark_source(store, conn, source):
    if conn.execute("SELECT 1 FROM withdrawn WHERE source=?", (source,)).fetchone():
        return
    conn.execute("INSERT INTO withdrawn VALUES (?)", (source,))
    active = conn.execute("SELECT version,kind FROM active WHERE source=?", (source,)).fetchone()
    versions = {active["version"]} if active else set()
    versions.update(r[0] for r in conn.execute("SELECT version FROM source_versions WHERE source=?", (source,)))
    kind = active["kind"] if active else ("quality" if source.startswith("quality:") else "upload")
    label = "Загрузки/" + source[7:] if kind == "upload" else source
    name = Path(label).name
    if kind == "quality":
        number = int(source.split(":")[1])
        name = f"Решение-{number}.txt"
        conn.execute("""UPDATE tickets SET publication='withdrawn',answer=NULL,topic=NULL,reviewer=NULL,
                     dialogue='[]',revision=revision+1 WHERE id=?""", (number,))
        versions.update(r[0] for r in conn.execute("SELECT version FROM approved_faq WHERE source=?",
                                                   (f"Служба качества • решение №{number}",)))
    conn.execute("DELETE FROM active WHERE source=?", (source,))
    for row in conn.execute("SELECT version,blob FROM originals WHERE name=?", (name,)).fetchall():
        stem = Path(row["blob"]).stem
        file_versions = {digest(f"{parser}\0{label}\0{stem}")
                         for parser in ("file-v1", "scan-v2", "chunks-1300-200-v1")}
        if row["version"] in file_versions:
            versions.add(row["version"])
        elif kind == "quality":
            path = store.config.data / "originals" / row["blob"]
            if path.is_file() and row["version"] == digest("quality\0" + path.read_bytes().decode("utf-8", "replace")):
                versions.add(row["version"])
    for version in versions:
        conn.execute("INSERT OR IGNORE INTO revoked VALUES (?)", (version,))
        conn.execute("""INSERT OR IGNORE INTO garbage SELECT version,blob FROM originals
                        WHERE version=?""", (version,))
        conn.execute("INSERT OR IGNORE INTO garbage VALUES (?,'')", (version,))
        conn.execute("DELETE FROM approved_faq WHERE version=?", (version,))
        conn.execute("DELETE FROM originals WHERE version=?", (version,))
    if kind == "upload":
        for row in conn.execute("SELECT DISTINCT payload FROM jobs WHERE kind='upload' AND name=?", (name,)):
            conn.execute("INSERT OR IGNORE INTO garbage VALUES (?,?)",
                         (digest("pending:" + row[0]), row[0]))
        conn.execute("DELETE FROM jobs WHERE kind='upload' AND name=?", (name,))


def withdraw(store, source, links=()):
    # A CLI folder sync may use a separate Store with the same remote Qdrant.
    with closing(sqlite3.connect(store.config.data / "folder-sync.sqlite3", timeout=60)) as guard:
        guard.execute("BEGIN IMMEDIATE")
        _withdraw(store, source, links)


def _withdraw(store, source, links):
    with store.db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if not conn.execute("SELECT 1 FROM active WHERE source=? UNION SELECT 1 FROM withdrawn WHERE source=?",
                            (source, source)).fetchone():
            raise ValueError("Документ не найден.")
        conn.executemany("INSERT OR IGNORE INTO publication_links VALUES (?,?,?)", links)
        pending = {source}
        while pending:
            for key in pending:
                mark_source(store, conn, key)
            pending = set()
            revoked = {r[0] for r in conn.execute("SELECT version FROM revoked")}
            parents = {tuple(r) for r in conn.execute("""SELECT p.owner,p.request FROM publication_links p
                JOIN withdrawn w ON w.source=p.source""")}
            for row in conn.execute("SELECT * FROM turns ORDER BY id").fetchall():
                result = json.loads(row["result"])
                affected = bool(dependencies(result) & revoked)
                affected |= (row["owner"], row["request"]) in parents
                if result.get("ticket_id"):
                    affected |= bool(conn.execute("SELECT 1 FROM withdrawn WHERE source=?",
                                                  (f"quality:{result['ticket_id']}",)).fetchone())
                if not (affected or result.get("withdrawn")):
                    continue
                token = turn_key(row["owner"], row["request"])
                revoked.add(token)
                conn.execute("INSERT OR IGNORE INTO revoked VALUES (?)", (token,))
                children = [r[0] for r in conn.execute(
                    "SELECT source FROM publication_links WHERE owner=? AND request=?", (row["owner"], row["request"]))]
                if result.get("ticket_id"):
                    children.append(f"quality:{result['ticket_id']}")
                for child in children:
                    if not conn.execute("SELECT 1 FROM withdrawn WHERE source=?", (child,)).fetchone():
                        pending.add(child)
                clean = dict(kind="unknown", text="Источник ответа удалён.", used=[], hits=[], ticket_id=None,
                             standalone=False, source="", withdrawn=True, depends_on=[])
                conn.execute("UPDATE turns SET result=?,basis=NULL WHERE id=?", (json.dumps(clean), row["id"]))
    reclaim(store)


def reclaim(store):
    with store.db() as conn:
        items = conn.execute("SELECT version,blob FROM garbage").fetchall()
    for version, blob in items:
        with store.lock:
            if store.client.collection_exists(store.config.collection):
                scope = qm.Filter(must=[qm.FieldCondition(key="version", match=qm.MatchValue(value=version))])
                store.client.delete(store.config.collection, points_selector=scope, wait=True)
        with store.db() as conn:
            shared = conn.execute("""SELECT 1 FROM originals WHERE blob=? UNION ALL
                SELECT 1 FROM jobs WHERE kind='upload' AND payload=? AND status IN ('queued','running')""",
                                  (blob, blob)).fetchone()
            if not shared and re.fullmatch(r"[0-9a-f]{64}\.[a-z0-9]+", blob):
                (store.config.data / "originals" / blob).unlink(missing_ok=True)
            conn.execute("DELETE FROM garbage WHERE version=?", (version,))

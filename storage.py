import hashlib
import json
import re
import sqlite3
import uuid
from collections import Counter
from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass
from threading import RLock

from qdrant_client import QdrantClient, models as qm

from documents import Passage


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def lexical(text):
    # Word tokens keep contract numbers. Prefixes also match Russian inflections.
    tokens = re.findall(r"[\w]+", text.casefold().replace("ё", "е"))
    terms = tokens + ["~" + word[:5] for word in tokens if len(word) > 5]
    counts = Counter(int(digest(term)[:8], 16) for term in terms)
    keys = sorted(counts)
    return qm.SparseVector(indices=keys, values=[1 + counts[k] ** 0.5 for k in keys])


@dataclass
class Hit:
    passage: Passage
    score: float
    dense_rank: int | None
    lexical_rank: int | None


class Store:
    def __init__(self, config, model):
        self.config, self.model = config, model
        config.data.mkdir(parents=True, exist_ok=True)
        self.db_path = config.data / "catalog.sqlite3"
        self.lock = RLock()
        self.client = (QdrantClient(url=config.qdrant_url) if config.qdrant_url else
                       QdrantClient(path=str(config.data / "qdrant"), force_disable_check_same_thread=True))
        with self.db() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS active (
                    source TEXT PRIMARY KEY, version TEXT NOT NULL, kind TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS tickets (
                    id INTEGER PRIMARY KEY, ref TEXT UNIQUE NOT NULL, dialogue TEXT NOT NULL,
                    created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, reviewer TEXT,
                    topic TEXT, answer TEXT, resolved TEXT);
                CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY, backend TEXT);
            """)
            backend = json.dumps([config.qdrant_url, config.collection, config.embedding])
            conn.execute("INSERT OR IGNORE INTO settings VALUES (1, ?)", (backend,))
            if conn.execute("SELECT backend FROM settings").fetchone()[0] != backend:
                raise ValueError("Для другого хранилища или модели эмбеддингов укажите новый RAG_DATA_DIR.")

    @contextmanager
    def db(self):
        with closing(sqlite3.connect(self.db_path, timeout=60)) as conn:
            conn.row_factory = sqlite3.Row
            with conn:
                yield conn

    def write_passages(self, version, passages):
        for start in range(0, len(passages), 12):
            batch = passages[start:start + 12]
            texts = [p.search_text() for p in batch]
            vectors = self.model.embed(texts)
            with self.lock:
                if not self.client.collection_exists(self.config.collection):
                    self.client.create_collection(
                        self.config.collection,
                        vectors_config={"dense": qm.VectorParams(size=len(vectors[0]), distance=qm.Distance.COSINE)},
                        sparse_vectors_config={"words": qm.SparseVectorParams(modifier=qm.Modifier.IDF)},
                    )
                points = [
                    qm.PointStruct(
                        id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{version}/{start + i}")),
                        vector={"dense": vector, "words": lexical(text)},
                        payload={**asdict(p), "version": version},
                    )
                    for i, (p, text, vector) in enumerate(zip(batch, texts, vectors))
                ]
                self.client.upsert(self.config.collection, points=points, wait=True)

    def has_sources(self):
        with self.db() as conn:
            return conn.execute("SELECT 1 FROM active LIMIT 1").fetchone() is not None

    def search(self, query):
        with self.db() as conn:
            versions = [r[0] for r in conn.execute("SELECT version FROM active")]
        if not versions:
            return []
        scope = qm.Filter(must=[qm.FieldCondition(key="version", match=qm.MatchAny(any=versions))])
        vector, = self.model.embed([query])
        keys = lexical(query)
        candidates = {}
        with self.lock:
            for channel, signal in (("dense", vector), ("words", keys)):
                if channel == "words" and not keys.indices:
                    continue
                points = self.client.query_points(
                    self.config.collection, query=signal, using=channel, query_filter=scope,
                    limit=self.config.top_k * 3, with_payload=True,
                ).points
                for rank, point in enumerate(points, 1):
                    if point.id not in candidates:
                        data = point.payload
                        p = Passage(data["source"], data["location"], data["body"], data["topic"])
                        candidates[point.id] = Hit(p, 0, None, None)
                    hit = candidates[point.id]
                    hit.score += 1 / (60 + rank)
                    if channel == "words":
                        hit.lexical_rank = rank
                    else:
                        hit.dense_rank = rank
        return sorted(candidates.values(), key=lambda h: h.score, reverse=True)[:self.config.top_k]

    def ticket(self, dialogue, ref):
        with self.db() as conn:
            conn.execute("INSERT OR IGNORE INTO tickets(ref,dialogue) VALUES (?,?)",
                         (ref, json.dumps(dialogue, ensure_ascii=False)))
            return conn.execute("SELECT id FROM tickets WHERE ref=?", (ref,)).fetchone()[0]

    def tickets(self):
        with self.db() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM tickets ORDER BY id DESC")]

    def approve(self, ticket_id, topic, answer, reviewer):
        topic, answer, reviewer = (s.strip() for s in (topic, answer, reviewer))
        if not all((topic, answer, reviewer)) or len(answer) > 1500 or len(topic) > 600:
            raise ValueError("Заполните вопрос (до 600 знаков), ответ (до 1500) и имя специалиста.")
        source = f"Служба качества • решение №{ticket_id}"
        passage = Passage(source, f"Утвердил(а): {reviewer}", answer, topic)
        version = digest("quality\0" + passage.search_text())
        with self.db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT resolved FROM tickets WHERE id=?", (ticket_id,)).fetchone()
            if not row or row["resolved"]:
                raise ValueError("Заявка уже закрыта или отсутствует.")
            self.write_passages(version, [passage])
            conn.execute("INSERT INTO active VALUES (?,?,?)", ("quality:" + str(ticket_id), version, "quality"))
            conn.execute("""UPDATE tickets SET reviewer=?,topic=?,answer=?,resolved=CURRENT_TIMESTAMP
                            WHERE id=?""", (reviewer, topic, answer, ticket_id))

import hashlib
import json
import re
import sqlite3
import uuid
from io import BytesIO
from collections import Counter
from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass
from threading import RLock

from qdrant_client import QdrantClient, models as qm

from documents import Passage, published_faq
from originals import snapshot


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
    version: str = ""


class Store:
    def __init__(self, config, model):
        self.config, self.model = config, model
        config.data.mkdir(parents=True, exist_ok=True)
        self.db_path = config.data / "catalog.sqlite3"
        self.lock = RLock()
        self.client = (QdrantClient(url=config.qdrant_url) if config.qdrant_url else
                       QdrantClient(path=str(config.data / "qdrant"), force_disable_check_same_thread=True))
        with self.db() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS active (
                    source TEXT PRIMARY KEY, version TEXT NOT NULL, kind TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS tickets (
                    id INTEGER PRIMARY KEY, ref TEXT UNIQUE NOT NULL, dialogue TEXT NOT NULL,
                    created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, reviewer TEXT,
                    topic TEXT, answer TEXT, resolved TEXT);
                CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY, backend TEXT);
                CREATE TABLE IF NOT EXISTS originals (
                    version TEXT PRIMARY KEY, name TEXT NOT NULL, blob TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS approved_faq (
                    version TEXT PRIMARY KEY, lookup TEXT NOT NULL, source TEXT NOT NULL,
                    question TEXT NOT NULL, answer TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS faq_lookup ON approved_faq(lookup);
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL, payload TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued', error TEXT NOT NULL DEFAULT '',
                    attempts INTEGER NOT NULL DEFAULT 0, due REAL NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS turns (
                    id INTEGER PRIMARY KEY, owner TEXT NOT NULL, chat TEXT NOT NULL, request TEXT NOT NULL,
                    question TEXT NOT NULL, result TEXT NOT NULL, UNIQUE(owner,chat,request));
            """)
            if "basis" not in {r[1] for r in conn.execute("PRAGMA table_info(turns)")}:
                conn.execute("ALTER TABLE turns ADD COLUMN basis TEXT")
            conn.execute("CREATE INDEX IF NOT EXISTS turns_repeat ON turns(owner,chat,question,basis)")
            columns = {r[1] for r in conn.execute("PRAGMA table_info(tickets)")}
            for col, definition in [("owner", "TEXT DEFAULT ''"), ("revision", "INTEGER DEFAULT 0"),
                                    ("publication", "TEXT DEFAULT 'private'")]:
                if col not in columns:
                    conn.execute(f"ALTER TABLE tickets ADD COLUMN {col} {definition}")
            if "publication" not in columns:
                conn.execute("""UPDATE tickets SET publication='published' WHERE EXISTS
                              (SELECT 1 FROM active WHERE source='quality:' || tickets.id)""")
            backend = json.dumps([config.qdrant_url, config.collection, config.embedding])
            conn.execute("INSERT OR IGNORE INTO settings VALUES (1, ?)", (backend,))
            if conn.execute("SELECT backend FROM settings").fetchone()[0] != backend:
                raise ValueError("Для другого хранилища или модели эмбеддингов укажите новый RAG_DATA_DIR.")
            legacy = conn.execute("""SELECT t.*,a.version FROM tickets t JOIN active a ON a.source='quality:'||t.id
                                   LEFT JOIN originals o ON o.version=a.version WHERE o.version IS NULL AND t.resolved IS NOT NULL""").fetchall()
            for row in legacy:
                card = "\n".join([row["topic"], row["answer"], row["reviewer"], row["resolved"] + " UTC"])
                blob = snapshot(config.data, BytesIO(card.encode()), ".txt")
                conn.execute("INSERT OR IGNORE INTO originals VALUES (?,?,?)",
                             (row["version"], f"Решение-{row['id']}.txt", blob))
            for row in conn.execute("""SELECT a.version,o.name,o.blob FROM active a JOIN originals o USING(version)
                    LEFT JOIN approved_faq f USING(version) WHERE a.kind='upload' AND f.version IS NULL
                    AND o.name GLOB 'faq_ticket_[0-9]*.txt'""").fetchall():
                path = config.data / "originals" / row["blob"]
                if path.is_file() and (faq := published_faq(row["name"], path)):
                    self.record_faq(conn, row["version"], "Загрузки/" + row["name"], *faq)
            for row in conn.execute("""SELECT a.version,t.id,t.topic,t.answer FROM active a JOIN tickets t
                    ON a.source='quality:'||t.id LEFT JOIN approved_faq f USING(version)
                    WHERE t.publication='published' AND f.version IS NULL""").fetchall():
                self.record_faq(conn, row["version"], f"Служба качества • решение №{row['id']}", row["topic"], row["answer"])

    @staticmethod
    def record_faq(conn, version, source, question, answer):
        key = " ".join(question.casefold().split())
        conn.execute("INSERT OR REPLACE INTO approved_faq VALUES (?,?,?,?,?)",
                     (version, key, source, question, answer))

    def exact_faq(self, question):
        with self.db() as conn:
            rows = conn.execute("""SELECT f.* FROM approved_faq f JOIN active a USING(version)
                WHERE f.lookup=? ORDER BY f.version""", (" ".join(question.casefold().split()),)).fetchall()
        return [Hit(Passage(r["source"], "Ответ службы качества", r["answer"], r["question"]),
                    1.0, None, None, r["version"]) for r in rows]

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

    def basis(self):
        with self.db() as conn:
            versions = [row[0] for row in conn.execute("SELECT version FROM active ORDER BY source")]
        return digest(json.dumps(["verified-answer-v2", self.config.model, self.config.top_k, versions]))

    def search(self, query):
        if not self.has_sources():
            return []
        vector, = self.model.embed([query])
        keys = lexical(query)
        candidates = {}
        with self.lock:
            with self.db() as conn:
                versions = [r[0] for r in conn.execute("SELECT version FROM active")]
            if not versions:
                return []
            scope = qm.Filter(must=[qm.FieldCondition(key="version", match=qm.MatchAny(any=versions))])
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
                        candidates[point.id] = Hit(p, 0, None, None, data["version"])
                    hit = candidates[point.id]
                    hit.score += 1 / (60 + rank)
                    if channel == "words":
                        hit.lexical_rank = rank
                    else:
                        hit.dense_rank = rank
        return sorted(candidates.values(), key=lambda h: h.score, reverse=True)[:self.config.top_k]

    def ticket(self, dialogue, ref, owner=""):
        with self.db() as conn:
            conn.execute("INSERT OR IGNORE INTO tickets(ref,dialogue,owner) VALUES (?,?,?)",
                         (ref, json.dumps(dialogue, ensure_ascii=False), owner))
            return conn.execute("SELECT id FROM tickets WHERE ref=?", (ref,)).fetchone()[0]

    def tickets(self):
        with self.db() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM tickets ORDER BY id DESC")]

    def review(self, ticket_id, topic, answer, reviewer, publish, revision):
        topic, answer, reviewer = (s.strip() for s in (topic, answer, reviewer))
        if not all((topic, answer, reviewer)) or len(answer) > 1500 or len(topic) > 600:
            raise ValueError("Заполните вопрос (до 600 знаков), ответ (до 1500) и имя специалиста.")
        with self.db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            saved = conn.execute("""UPDATE tickets SET reviewer=?,topic=?,answer=?,resolved=CURRENT_TIMESTAMP,
                                 publication=?,revision=revision+1 WHERE id=? AND revision=?""",
                                 (reviewer, topic, answer, "pending" if publish else "private", ticket_id, revision))
            if not saved.rowcount:
                raise ValueError("Заявка изменилась. Обновите её перед сохранением.")
            if publish:
                return conn.execute("INSERT INTO jobs(kind,name,payload) VALUES ('review',?,?)",
                                    (str(ticket_id), str(revision + 1))).lastrowid
            conn.execute("DELETE FROM active WHERE source=?", (f"quality:{ticket_id}",))

    def publish_review(self, ticket_id, revision):
        with self.db() as conn:
            row = conn.execute("SELECT * FROM tickets WHERE id=? AND revision=?", (ticket_id, revision)).fetchone()
        if not row or row["publication"] != "pending":
            return
        source = f"Служба качества • решение №{ticket_id}"
        passage = Passage(source, f"{row['reviewer']} · {row['resolved']} UTC · ред. {revision}", row["answer"], row["topic"])
        version = digest("quality\0" + passage.search_text())
        blob = snapshot(self.config.data, BytesIO(passage.search_text().encode()), ".txt")
        self.write_passages(version, [passage])
        with self.db() as conn:
            updated = conn.execute("UPDATE tickets SET publication='published' WHERE id=? AND revision=?",
                                   (ticket_id, revision))
            if updated.rowcount:
                conn.execute("INSERT OR REPLACE INTO originals VALUES (?,?,?)", (version, f"Решение-{ticket_id}.txt", blob))
                conn.execute("INSERT OR REPLACE INTO active VALUES (?,?,?)", (f"quality:{ticket_id}", version, "quality"))
                self.record_faq(conn, version, source, row["topic"], row["answer"])

    def approve(self, ticket_id, topic, answer, reviewer):
        job = self.review(ticket_id, topic, answer, reviewer, True, 0)
        self.publish_review(ticket_id, 1)
        with self.db() as conn:
            conn.execute("UPDATE jobs SET status='done' WHERE id=?", (job,))

"""Shared application operations for Streamlit and the PHP-facing service."""
import json
import sqlite3
import time
from dataclasses import asdict
from collections import deque
from contextlib import ExitStack
from pathlib import Path
from threading import Event, Lock, Thread
from uuid import UUID

from agent import respond
from documents import SUPPORTED
from indexer import activate_upload, sync
from originals import snapshot
from storage import digest
import withdrawal


def context(rows, limit):
    messages, clarifications = [], 0
    for row in rows:
        kind = row["kind"]
        if kind == "off_topic":
            continue
        messages.extend([{"role": "user", "content": row["question"]},
                         {"role": "assistant", "content": row.get("human_answer") or row["text"]}])
        clarifications = clarifications + 1 if kind == "clarify" else 0
    return messages[-limit:], clarifications


class Consultant:
    def __init__(self, store):
        self.store = store
        self.lease = sqlite3.connect(store.config.data / "worker.sqlite3", timeout=0, check_same_thread=False)
        try:
            self.lease.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as exc:
            self.lease.close()
            raise RuntimeError("Для этой базы уже запущен обработчик задач.") from exc
        self.stop = Event()
        self.wake = Event()
        self.chat_locks = [Lock() for _ in range(64)]
        self.index_lock = Lock()
        self.upload_lock = Lock()
        with store.db() as conn:
            conn.execute("UPDATE jobs SET status='queued' WHERE status='running'")
            legacy = conn.execute("SELECT 1 FROM turns WHERE json_type(result,'$.depends_on') IS NULL LIMIT 1").fetchone()
            if legacy:
                histories = {}
                for row in conn.execute("SELECT * FROM turns ORDER BY id").fetchall():
                    result = json.loads(row["result"])
                    key = row["owner"], row["chat"]
                    prior = histories.setdefault(key, deque(maxlen=store.config.context_messages))
                    deps = withdrawal.dependencies(result)
                    if "depends_on" not in result:
                        if not result.get("standalone") and result["kind"] != "off_topic":
                            deps.update(*prior)
                        result["depends_on"] = sorted(deps)
                        conn.execute("UPDATE turns SET result=? WHERE id=?", (json.dumps(result), row["id"]))
                    if result["kind"] != "off_topic":
                        prior.append({v for v in deps if not v.startswith("turn:")}
                                     | {withdrawal.turn_key(row["owner"], row["request"])})
            if conn.execute("SELECT 1 FROM garbage LIMIT 1").fetchone():
                conn.execute("INSERT INTO jobs(kind,name,payload) VALUES ('cleanup','','')")
        self.worker = Thread(target=self._work, name="document-indexer", daemon=True)
        self.worker.start()

    def close(self):
        self.stop.set()
        self.wake.set()
        self.worker.join()
        self.store.client.close()
        self.lease.close()

    def _work(self):
        while not self.stop.is_set():
            with self.store.db() as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("""SELECT * FROM jobs WHERE status='queued' AND due<=?
                                    ORDER BY id LIMIT 1""", (time.time(),)).fetchone()
                if row:
                    conn.execute("UPDATE jobs SET status='running',attempts=attempts+1 WHERE id=?", (row["id"],))
            if not row:
                self.wake.wait(1)
                self.wake.clear()
                continue
            status, error, due = "done", "", 0
            try:
                with self.index_lock:
                    if row["kind"] == "upload":
                        with self.store.db() as conn:
                            if conn.execute("SELECT 1 FROM withdrawn WHERE source=?", ("upload:" + row["name"],)).fetchone():
                                continue
                        activate_upload(self.store, row["name"], row["payload"])
                    elif row["kind"] == "folder":
                        sync(self.store, self.store.config.documents)
                    elif row["kind"] == "cleanup":
                        with self.upload_lock:
                            withdrawal.reclaim(self.store)
                    else:
                        self.store.publish_review(int(row["name"]), int(row["payload"]))
            except Exception as exc:
                error, status = str(exc), "failed"
                if row["kind"] in {"review", "cleanup"}:
                    status, due = "queued", time.time() + min(300, 5 * (row["attempts"] + 1))
            with self.store.db() as conn:
                conn.execute("UPDATE jobs SET status=?,error=?,due=? WHERE id=?", (status, error, due, row["id"]))

    def enqueue(self, kind, name, payload):
        with self.store.db() as conn:
            number = conn.execute("INSERT INTO jobs(kind,name,payload) VALUES (?,?,?)",
                                  (kind, name, payload)).lastrowid
        self.wake.set()
        return number

    def upload(self, name, stream, owner="", request=""):
        if (not name or len(name) > 200 or "/" in name or "\\" in name or ":" in name
                or any(ord(c) < 32 for c in name) or Path(name).suffix.lower() not in SUPPORTED):
            raise ValueError("Недопустимое имя или формат документа.")
        with self.upload_lock, self.store.db() as conn:
            source = "upload:" + name
            if conn.execute("SELECT 1 FROM withdrawn WHERE source=?", (source,)).fetchone():
                raise ValueError("Источник с этим именем удалён.")
            if owner or request:
                parent = conn.execute("SELECT result FROM turns WHERE owner=? AND request=?", (owner, request)).fetchone()
                if not parent or json.loads(parent[0]).get("withdrawn"):
                    raise ValueError("Исходный вопрос недоступен для публикации.")
                conn.execute("INSERT OR IGNORE INTO publication_links VALUES (?,?,?)", (source, owner, request))
            blob = snapshot(self.store.config.data, stream, Path(name).suffix)
            number = conn.execute("INSERT INTO jobs(kind,name,payload) VALUES ('upload',?,?)", (name, blob)).lastrowid
        self.wake.set()
        return number

    def withdraw(self, source, links=()):
        with self.index_lock, self.upload_lock, ExitStack() as stack:
            for lock in self.chat_locks:
                stack.enter_context(lock)
            try:
                withdrawal.withdraw(self.store, source, links)
            finally:
                with self.store.db() as conn:
                    pending = conn.execute("SELECT 1 FROM garbage LIMIT 1").fetchone()
                if pending:
                    self.enqueue("cleanup", "", "")

    def jobs(self):
        with self.store.db() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT id,kind,name,status,error,attempts FROM jobs ORDER BY id DESC LIMIT 200")]

    def original(self, version):
        with self.store.db() as conn:
            row = conn.execute("SELECT name,blob FROM originals WHERE version=?", (version,)).fetchone()
        if not row:
            raise ValueError("Оригинал отсутствует. Для старого индекса синхронизируйте папку.")
        return row["name"], self.store.config.data / "originals" / row["blob"]

    def chats(self, owner):
        with self.store.db() as conn:
            return [r[0] for r in conn.execute("SELECT chat FROM turns WHERE owner=? GROUP BY chat ORDER BY MAX(id) DESC",
                                               (owner,))]

    def history(self, owner, chat):
        with self.store.db() as conn:
            rows = conn.execute("SELECT id,question,result FROM turns WHERE owner=? AND chat=? ORDER BY id",
                                (owner, chat)).fetchall()
            result = []
            for row in rows:
                item = dict(json.loads(row["result"]), question=row["question"], id=row["id"])
                if item["ticket_id"]:
                    review = conn.execute("SELECT answer,reviewer FROM tickets WHERE id=? AND owner=?",
                                          (item["ticket_id"], owner)).fetchone()
                    if review:
                        item.update(human_answer=review["answer"], reviewer=review["reviewer"])
                result.append(item)
        return result

    def recent(self, owner, chat):
        with self.store.db() as conn:
            rows = conn.execute("""SELECT t.request,t.result,t.question,json_extract(t.result,'$.kind') AS kind,
                json_extract(t.result,'$.text') AS text, r.answer AS human_answer FROM turns t
                LEFT JOIN tickets r ON r.id=json_extract(t.result,'$.ticket_id') AND r.owner=t.owner
                WHERE t.owner=? AND t.chat=? AND json_extract(t.result,'$.kind') <> 'off_topic'
                AND json_extract(t.result,'$.withdrawn') IS NULL
                ORDER BY t.id DESC LIMIT ?""", (owner, chat, self.store.config.context_messages)).fetchall()
        return [dict(dict(r), depends_on=sorted(withdrawal.dependencies(json.loads(r["result"])))) for r in reversed(rows)]

    def request_review(self, owner, chat, turn_id):
        with self.store.db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            turn = conn.execute("SELECT * FROM turns WHERE owner=? AND chat=? AND id=?",
                                (owner, chat, turn_id)).fetchone()
            if not turn:
                raise ValueError("Вопрос не найден.")
            result = json.loads(turn["result"])
            if result.get("withdrawn"):
                raise ValueError("Источник ответа удалён.")
            if result["ticket_id"]:
                return result["ticket_id"]
            if result["kind"] not in {"unknown", "off_topic"}:
                raise ValueError("Для этого ответа заявка не требуется.")
            recent = conn.execute("""SELECT question,result FROM turns WHERE owner=? AND chat=? AND id<?
                                  ORDER BY id DESC LIMIT ?""", (owner, chat, turn_id, self.store.config.context_messages))
            rows = [dict(json.loads(r["result"]), question=r["question"]) for r in recent]
            messages, _ = context(reversed(rows), self.store.config.context_messages - 1)
            messages.append({"role": "user", "content": turn["question"]})
            number = conn.execute("INSERT INTO tickets(ref,dialogue,owner) VALUES (?,?,?)",
                                  (digest(json.dumps([owner, chat, turn["request"]])), json.dumps(messages, ensure_ascii=False), owner)).lastrowid
            result["ticket_id"] = number
            deps = withdrawal.dependencies(result)
            for row in rows:
                deps.update(withdrawal.dependencies(row))
            result["depends_on"] = sorted(deps)
            conn.execute("UPDATE turns SET result=? WHERE id=?", (json.dumps(result, ensure_ascii=False), turn_id))
            return number

    def ask(self, owner, chat, question, request, previous=None):
        question = question.strip()
        if not owner or not chat or not 1 <= len(question) <= 2000:
            raise ValueError("Укажите пользователя, диалог и вопрос длиной до 2000 символов.")
        request = UUID(request).hex
        with self.chat_locks[hash((owner, chat)) % len(self.chat_locks)]:
            with self.store.db() as conn:
                cached = conn.execute("SELECT question,result FROM turns WHERE owner=? AND chat=? AND request=?",
                                      (owner, chat, request)).fetchone()
            if cached:
                if cached["question"] != question:
                    raise ValueError("Этот идентификатор запроса уже использован.")
                return json.loads(cached["result"])
            basis = self.store.basis()
            with self.store.db() as conn:
                verified = conn.execute("""SELECT result FROM turns WHERE owner=? AND chat=? AND question=?
                                        AND basis=? ORDER BY id DESC LIMIT 1""", (owner, chat, question, basis)).fetchone()
            rows = self.recent(owner, chat) if previous is None else previous
            with self.store.db() as conn:
                revoked = {r[0] for r in conn.execute("SELECT version FROM revoked")}
                if previous is not None:
                    allowed = []
                    for r in rows:
                        saved = conn.execute("SELECT result FROM turns WHERE owner=? AND chat=? AND request=?",
                                             (owner, chat, r.get("request"))).fetchone()
                        if saved:
                            meta = json.loads(saved[0])
                            if not meta.get("withdrawn"):
                                allowed.append(dict(r, depends_on=sorted(withdrawal.dependencies(meta))))
                        elif not revoked:
                            allowed.append(r)
                    rows = allowed
            messages, count = context(rows, self.store.config.context_messages)
            if verified and not count:
                result = json.loads(verified["result"])
            else:
                # PHP supplies authoritative recent MySQL rows. Streamlit reads its local history.
                messages.append({"role": "user", "content": question})
                result = asdict(respond(self.store, messages, count))
                deps = withdrawal.dependencies(result)
                if not result["standalone"]:
                    for row in rows[-self.store.config.context_messages:]:
                        deps.update(v for v in (row.get("depends_on") or []) if not v.startswith("turn:"))
                        if row.get("request"):
                            deps.add(withdrawal.turn_key(owner, row["request"]))
                result["depends_on"] = sorted(deps)
                if not result["standalone"] or self.store.basis() != basis:
                    basis = None
            with self.store.db() as conn:
                conn.execute("INSERT INTO turns(owner,chat,request,question,result,basis) VALUES (?,?,?,?,?,?)",
                             (owner, chat, request, question, json.dumps(result, ensure_ascii=False), basis))
            return result

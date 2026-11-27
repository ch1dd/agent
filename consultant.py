"""Shared application operations for Streamlit and the PHP-facing service."""
import json
import sqlite3
import time
from dataclasses import asdict
from pathlib import Path
from threading import Event, Lock, Thread
from uuid import UUID

from agent import respond
from documents import SUPPORTED
from indexer import activate_upload, sync
from originals import snapshot
from storage import digest


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
        with store.db() as conn:
            conn.execute("UPDATE jobs SET status='queued' WHERE status='running'")
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
                if row["kind"] == "upload":
                    activate_upload(self.store, row["name"], row["payload"])
                elif row["kind"] == "folder":
                    sync(self.store, self.store.config.documents)
                else:
                    self.store.publish_review(int(row["name"]), int(row["payload"]))
            except Exception as exc:
                error, status = str(exc), "failed"
                if row["kind"] == "review":
                    status, due = "queued", time.time() + min(300, 5 * (row["attempts"] + 1))
            with self.store.db() as conn:
                conn.execute("UPDATE jobs SET status=?,error=?,due=? WHERE id=?", (status, error, due, row["id"]))

    def enqueue(self, kind, name, payload):
        with self.store.db() as conn:
            number = conn.execute("INSERT INTO jobs(kind,name,payload) VALUES (?,?,?)",
                                  (kind, name, payload)).lastrowid
        self.wake.set()
        return number

    def upload(self, name, stream):
        if (not name or len(name) > 200 or "/" in name or "\\" in name or ":" in name
                or any(ord(c) < 32 for c in name) or Path(name).suffix.lower() not in SUPPORTED):
            raise ValueError("Недопустимое имя или формат документа.")
        blob = snapshot(self.store.config.data, stream, Path(name).suffix)
        return self.enqueue("upload", name, blob)

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
            rows = conn.execute("""SELECT t.question,json_extract(t.result,'$.kind') AS kind,
                json_extract(t.result,'$.text') AS text, r.answer AS human_answer FROM turns t
                LEFT JOIN tickets r ON r.id=json_extract(t.result,'$.ticket_id') AND r.owner=t.owner
                WHERE t.owner=? AND t.chat=? AND json_extract(t.result,'$.kind') <> 'off_topic'
                ORDER BY t.id DESC LIMIT ?""", (owner, chat, self.store.config.context_messages)).fetchall()
        return [dict(r) for r in reversed(rows)]

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
            if verified:
                result = json.loads(verified["result"])
            else:
                # PHP supplies authoritative recent MySQL rows. Streamlit reads its local history.
                rows = self.recent(owner, chat) if previous is None else previous
                messages, count = context(rows, self.store.config.context_messages)
                messages.append({"role": "user", "content": question})
                ref = digest(json.dumps([owner, chat, request]))
                result = asdict(respond(self.store, messages, ref, count, owner))
                if not result["standalone"] or self.store.basis() != basis:
                    basis = None
            with self.store.db() as conn:
                conn.execute("INSERT INTO turns(owner,chat,request,question,result,basis) VALUES (?,?,?,?,?,?)",
                             (owner, chat, request, question, json.dumps(result, ensure_ascii=False), basis))
            return result

import io
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

from config import Config
from consultant import Consultant
from indexer import sync
from ollama_api import Decision, Intent, SearchQuery, Verification
from storage import Store


class Model:
    def __init__(self):
        self.started, self.release = Event(), Event()
        self.fail_embeddings = False
        self.queries = []

    def embed(self, texts):
        if "ЗАДЕРЖКА" in texts[0]:
            self.started.set()
            if not self.release.wait(10):
                raise RuntimeError("test timeout")
        if self.fail_embeddings:
            raise RuntimeError("offline")
        return [[1.0, 0.0] for _ in texts]

    def classify(self, instruction, data, schema):
        if schema is Intent:
            q = data["реплика"]
            kind = "off_topic" if q == "соси х3уй" else "followup" if q.startswith("А с") else "standalone"
            return Intent(kind=kind)
        if schema is SearchQuery:
            return SearchQuery(query="Кто подписывает договор?")
        if schema is Verification:
            return Verification(supported=True)
        self.queries.append(data["диалог"])
        unknown = data["диалог"][-1]["content"] == "Срок?"
        return Decision(action="unknown" if unknown else "answer", passage=1, question="")


class ConsultantTest(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name)
        docs = self.path / "docs"
        docs.mkdir()
        self.config = Config(data=self.path / "data", documents=docs, qdrant_url="")
        self.model = Model()
        self.bot = Consultant(Store(self.config, self.model))
        self.addCleanup(lambda: self.bot.close())
        self.addCleanup(self.model.release.set)
        (docs / "порядок.txt").write_text("Договор подписывает директор.", encoding="utf-8")
        sync(self.bot.store, docs)

    def ask(self, text, owner="alice", request=None):
        return self.bot.ask(owner, "one", text, request or uuid4().hex)

    def wait_job(self, number, status="done"):
        end = time.monotonic() + 12
        while time.monotonic() < end:
            job = next(r for r in self.bot.jobs() if r["id"] == number)
            if job["status"] == status:
                return
            time.sleep(.03)
        self.fail(str(job))

    def test_repeat_restart_and_owners(self):
        q = "Кто подписывает договор?"
        req = uuid4().hex
        first = self.ask(q, request=req)
        self.assertEqual(first, self.ask(q, request=req))
        self.assertEqual(self.ask("соси х3уй")["kind"], "off_topic")
        self.assertEqual(first, self.ask(q))
        self.assertEqual(len(self.model.queries), 1)
        self.assertFalse(self.bot.store.tickets())
        self.bot.close()
        self.bot = Consultant(Store(self.config, self.model))
        self.assertEqual(len(self.bot.history("alice", "one")), 3)
        self.assertFalse(self.bot.history("bob", "one"))
        self.ask("А с юридическим?")
        self.assertTrue(all(m["content"] != "соси х3уй" for m in self.model.queries[-1]))
        self.assertEqual(len(self.model.queries[-1]), 5)
        (self.config.documents / "порядок.txt").write_text("Договор подписывает заместитель.", encoding="utf-8")
        sync(self.bot.store, self.config.documents)
        self.assertEqual(self.ask(q)["text"], "Договор подписывает заместитель.")

    def test_search_and_upload_during_indexing(self):
        first = self.bot.upload("приказ.txt", io.BytesIO("ЗАДЕРЖКА".encode()))
        self.assertTrue(self.model.started.wait(5))
        second = self.bot.upload("приказ.txt", io.BytesIO("Другая редакция".encode()))
        reply = self.ask("Кто подписывает договор?")
        self.assertEqual(reply["text"], "Договор подписывает директор.")
        self.model.release.set()
        self.wait_job(first)
        self.wait_job(second)
        failed = self.bot.upload("приказ.txt", io.BytesIO(b""))
        self.wait_job(failed, "failed")
        hits = self.bot.store.search("Другая редакция")
        self.assertIn("Другая редакция", [h.passage.body for h in hits])
        self.assertNotIn("ЗАДЕРЖКА", [h.passage.body for h in hits])
        version = reply["hits"][reply["used"][0]]["version"]
        (self.config.documents/"порядок.txt").write_text("Исправленный файл", encoding="utf-8")
        sync(self.bot.store, self.config.documents)
        self.assertEqual(self.bot.original(version)[1].read_text("utf-8"), reply["text"])

    def test_private_reply_publication_retry_and_revision(self):
        ticket = self.ask("Срок?")["ticket_id"]
        store = self.bot.store
        self.assertIsNone(store.review(ticket, "Срок?", "Частный ответ", "Анна", False, 0))
        self.assertEqual(self.bot.history("alice", "one")[0]["human_answer"], "Частный ответ")
        self.assertNotIn("Частный ответ", [h.passage.body for h in store.search("Срок")])
        self.model.fail_embeddings = True
        job = store.review(ticket, "Срок?", "Три дня", "Анна", True, 1)
        self.bot.wake.set()
        end = time.monotonic() + 5
        while not self.bot.jobs()[0]["error"] and time.monotonic() < end:
            time.sleep(.03)
        self.assertEqual(self.bot.jobs()[0]["status"], "queued")
        self.assertEqual(self.bot.history("alice", "one")[0]["human_answer"], "Три дня")
        self.model.fail_embeddings = False
        self.bot.close()
        self.bot = Consultant(Store(self.config, self.model))
        self.wait_job(job)
        hit, = [h for h in self.bot.store.search("Срок") if h.passage.body == "Три дня"]
        self.assertIn("Анна", self.bot.original(hit.version)[1].read_text("utf-8"))
        job = self.bot.store.review(ticket, "Срок?", "Два дня", "Анна", True, 2)
        self.wait_job(job)
        self.assertNotIn("Три дня", [h.passage.body for h in self.bot.store.search("Срок")])

    def test_minimal_sufficient_sources(self):
        (self.config.documents / "сроки.txt").write_text("Срок регистрации два дня.", encoding="utf-8")
        sync(self.bot.store, self.config.documents)
        for count in (1, 2):
            def classify(instruction, data, schema):
                if schema is Decision:
                    return Decision(action="answer", passage=1, additional=[2], question="")
                if schema is Verification:
                    return Verification(supported=len(data["номера_цитат_в_ответе"]) >= count)
                return Intent(kind="standalone")
            with patch.object(self.model, "classify", side_effect=classify):
                reply = self.ask(f"Вопрос {count}")
            self.assertEqual(len(reply["used"]), count)
            for index in reply["used"]:
                hit = reply["hits"][index]
                self.assertIn(self.bot.original(hit["version"])[1].read_text("utf-8"), reply["text"])

    def test_http_roles_and_mysql_context_contract(self):
        from starlette.testclient import TestClient
        from service import create_app
        user = {"Authorization": "Bearer php"}
        admin = {"Authorization": "Bearer admin"}
        with patch.dict(os.environ, RAG_API_TOKEN="php", RAG_REVIEW_TOKEN="admin"):
            app = create_app(self.bot)
        with TestClient(app) as client:
            self.assertEqual(client.get("/admin/jobs", headers=user).status_code, 403)
            data = dict(owner="site:1", chat="default", request=uuid4().hex, question="Срок?", previous=[])
            reply = client.post("/ask", headers=user, json=data).json()
            number = reply["ticket_id"]
            self.assertEqual(client.get(f"/tickets/{number}?owner=site:2", headers=user).status_code, 404)
            r = client.post(f"/admin/tickets/{number}", headers=admin, json=dict(
                revision=0, topic="Срок?", answer="Частный ответ", reviewer="Анна", publish=False))
            self.assertEqual(r.status_code, 200)
            self.assertEqual(client.get(f"/tickets/{number}?owner=site:1", headers=user).json()["answer"], "Частный ответ")
            data.update(request=uuid4().hex, question="А с юрлицом?", previous=[
                dict(kind="answer", question="Кто подписывает договор?", text="Директор.")])
            result = client.post("/ask", headers=user, json=data).json()
            self.assertEqual(len(self.model.queries[-1]), 3)
            version = result["hits"][result["used"][0]]["version"]
            self.assertEqual(client.get("/originals/" + version, headers=user).text, result["text"])
            job = client.post("/admin/uploads", params={"name":"документ.txt"}, content=b"Hello", headers=admin)
            self.assertEqual(job.status_code, 202)
            self.wait_job(job.json()["job_id"])

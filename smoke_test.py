"""Сквозная проверка с реальными Ollama и локальным Qdrant во временной папке."""
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from agent import respond
from config import Config, ROOT
from indexer import sync
from ollama_api import ModelService
from storage import Store


def user(text):
    return {"role": "user", "content": text}


def check(label, reply, expected):
    print(f"{label}: {reply.kind} | {reply.text} | {reply.source}", flush=True)
    if reply.kind != expected:
        raise AssertionError(f"{label}: ожидалось {expected}")


def run():
    with TemporaryDirectory() as tmp:
        config = Config(data=Path(tmp), qdrant_url="")
        store = Store(config, ModelService(config))
        try:
            print(sync(store, ROOT / "examples"), flush=True)
            direct = respond(store, [user("Кто подписывает агентский договор с физическим лицом?")], uuid4().hex)
            check("Ответ", direct, "answer")
            assert "коммерческий директор" in direct.text
            assert len(direct.used) == 1
            insult = respond(store, [
                user("Кто подписывает агентский договор с физическим лицом?"),
                {"role": "assistant", "content": direct.text}, user("соси х3уй"),
            ], uuid4().hex)
            check("Посторонняя реплика", insult, "off_topic")
            repeat = respond(store, [
                user("Кто подписывает агентский договор с физическим лицом?"),
                {"role": "assistant", "content": direct.text},
                user("Кто подписывает агентский договор с физическим лицом?"),
            ], uuid4().hex)
            check("Повтор исходного вопроса", repeat, "answer")
            assert (repeat.source, repeat.text, repeat.hits) == (direct.source, direct.text, direct.hits)
            assert not store.tickets()
            continued = respond(store, [
                user("Кто подписывает агентский договор с физическим лицом?"),
                {"role": "assistant", "content": direct.text}, user("А с юридическим?"),
            ], uuid4().hex)
            check("Контекст после готового ответа", continued, "answer")
            assert "генеральный директор" in continued.text
            turns = [user("Кто подписывает агентский договор?")]
            ref = uuid4().hex
            clarify = respond(store, turns, ref)
            check("Уточнение", clarify, "clarify")
            turns += [{"role": "assistant", "content": clarify.text}, user("С юридическим лицом.")]
            answer = respond(store, turns, ref, clarifications=1)
            check("После уточнения", answer, "answer")
            assert "генеральный директор" in answer.text
            question = "Где хранится бумажный оригинал агентского договора?"
            unknown = respond(store, [user(question)], uuid4().hex)
            check("Передача специалисту", unknown, "unknown")
            approved = "Бумажный оригинал агентского договора хранится в архиве договорного отдела."
            store.approve(unknown.ticket_id, question, approved, "Участник демо")
        finally:
            store.client.close()
        # A new process/session must also see the already published decision.
        store = Store(config, ModelService(config))
        try:
            learned = respond(store, [user("Где искать бумажный подлинник агентского договора?")], uuid4().hex)
            check("Утверждённое знание", learned, "answer")
            assert learned.text == approved and "Служба качества" in learned.source
        finally:
            store.client.close()


if __name__ == "__main__":
    run()

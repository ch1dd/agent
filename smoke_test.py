"""Сквозная проверка с реальными Ollama и локальным Qdrant во временной папке."""
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic, sleep
from uuid import uuid4

from config import Config, ROOT
from consultant import Consultant
from indexer import sync
from ollama_api import ModelService
from storage import Store


def ask(bot, question, expected, chat="one", owner="employee"):
    reply = bot.ask(owner, chat, question, uuid4().hex)
    print(f"{question} → {reply['kind']} | {reply['text']} | {reply['source']}", flush=True)
    assert reply["kind"] == expected, expected
    return reply


def run():
    with TemporaryDirectory() as tmp:
        config = Config(data=Path(tmp), qdrant_url="")
        bot = Consultant(Store(config, ModelService(config)))
        try:
            print(sync(bot.store, ROOT / "examples"), flush=True)
            q = "Кто подписывает агентский договор с физическим лицом?"
            direct = ask(bot, q, "answer")
            assert "коммерческий директор" in direct["text"] and len(direct["used"]) == 1
            ask(bot, "соси х3уй", "off_topic")
            assert ask(bot, q, "answer") == direct
            assert not bot.store.tickets()
            reply = ask(bot, "А с юридическим?", "answer")
            assert "генеральный директор" in reply["text"]
            ask(bot, "Кто подписывает агентский договор?", "clarify", chat="two")
            reply = ask(bot, "С юридическим лицом.", "answer", chat="two")
            assert "генеральный директор" in reply["text"]
            question = "Где хранится бумажный оригинал агентского договора?"
            missing = ask(bot, question, "unknown", chat="three")
            approved = "Бумажный оригинал агентского договора хранится в архиве договорного отдела."
            job = bot.store.review(missing["ticket_id"], question, approved, "Участник демо", True, 0)
            bot.wake.set()
            end = monotonic() + 300
            while monotonic() < end:
                record = next(j for j in bot.jobs() if j["id"] == job)
                if record["status"] == "done":
                    break
                sleep(.25)
            else:
                raise AssertionError(record)
        finally:
            bot.close()
        bot = Consultant(Store(config, ModelService(config)))
        try:
            assert bot.history("employee", "three")[0]["human_answer"] == approved
            learned = ask(bot, "Где искать бумажный подлинник агентского договора?", "answer", owner="other")
            assert learned["text"] == approved and "Служба качества" in learned["source"]
            hit = learned["hits"][learned["used"][0]]
            assert approved in bot.original(hit["version"])[1].read_text(encoding="utf-8")
        finally:
            bot.close()


if __name__ == "__main__":
    run()

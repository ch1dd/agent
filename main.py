import atexit
import hashlib
import hmac
import json
import re
from uuid import uuid4

import streamlit as st

from config import Config
from consultant import Consultant
from documents import SUPPORTED
from ollama_api import ModelService
from storage import Store


@st.cache_resource
def connect():
    config = Config()
    bot = Consultant(Store(config, ModelService(config)))
    atexit.register(bot.close)
    return bot


def show_source(bot, hit, key):
    p = hit["passage"]
    st.text(" · ".join(filter(None, (p["source"], p["location"], p["topic"]))))
    try:
        name, path = bot.original(hit["version"])
    except ValueError as exc:
        st.caption(str(exc))
    else:
        st.download_button(f"Скачать {name}", data=path.read_bytes, file_name=name, key=key)


def chat(bot):
    # The local demo has no user accounts. The unguessable bookmark restores
    # only this browser's chats. PHP uses the authenticated account instead.
    secret = st.query_params.get("key", "")
    if not re.fullmatch(r"[0-9a-f]{32}", secret):
        secret = uuid4().hex
        st.query_params["key"] = secret
    owner = "demo:" + hashlib.sha256(secret.encode()).hexdigest()
    chats = bot.chats(owner)
    active = st.query_params.get("chat", "") or (chats[0] if chats else uuid4().hex)
    if not re.fullmatch(r"[0-9a-f]{32}", active):
        active = uuid4().hex
    if st.sidebar.button("Новый диалог"):
        active = uuid4().hex
        st.query_params["chat"] = active
        st.session_state.pop("choose_chat", None)
        st.session_state.pop("pending", None)
    choices = list(dict.fromkeys([active, *chats]))
    active = st.sidebar.selectbox("Мои диалоги", choices, key="choose_chat",
                                  format_func=lambda c: f"Диалог {c[:8]}")
    st.query_params["chat"] = active
    st.sidebar.caption("Сохраните адрес в закладках для возвращения после перезапуска. Ссылка даёт доступ к вашим демодиалогам.")
    if not bot.store.has_sources():
        st.info("База пока пуста. Администратор может загрузить документы.")
    rows = bot.history(owner, active)
    for row in rows:
        with st.chat_message("user"):
            st.text(row["question"])
        with st.chat_message("assistant"):
            st.text(row["text"])
            for index in row["used"]:
                show_source(bot, row["hits"][index], f"used-{row['id']}-{index}")
            if row["hits"]:
                with st.expander("Ранжирование источников"):
                    for i, hit in enumerate(row["hits"]):
                        label = " · использован" if i in row["used"] else ""
                        rank = "Точное совпадение" if hit["dense_rank"] is None and hit["lexical_rank"] is None else f"RRF {hit['score']:.4f}"
                        st.caption(f"№{i+1} · {rank} · смысловой ранг {hit['dense_rank'] or '—'} · лексический {hit['lexical_rank'] or '—'}{label}")
                        st.text(hit["passage"]["source"] + " · " + hit["passage"]["location"])
                        st.text(hit["passage"]["body"])
            if row.get("human_answer"):
                st.success(f"Ответ администратора · {row['reviewer']}")
                st.text(row["human_answer"])
            elif row["ticket_id"]:
                st.button("Проверить ответ администратора", key=f"refresh-{row['id']}")
            elif row["kind"] in {"unknown", "off_topic"} and st.button("Создать заявку админу", key=f"ticket-{row['id']}"):
                bot.request_review(owner, active, row["id"])
                st.rerun()
    pending = st.session_state.get("pending")
    if pending and pending["chat"] != active:
        del st.session_state.pending
        pending = None
    prompt = st.chat_input("Ваш вопрос о процессе", max_chars=2000, disabled=bool(pending))
    if prompt and prompt.strip():
        pending = dict(question=prompt.strip(), chat=active, request=uuid4().hex)
        st.session_state.pending = pending
    if pending:
        try:
            with st.spinner("Проверяю регламенты…"):
                bot.ask(owner, **pending)
        except Exception as exc:
            st.error(str(exc))
            st.button("Повторить")
            if st.button("Отменить"):
                del st.session_state.pending
                st.rerun()
        else:
            del st.session_state.pending
            st.rerun()


def admin(bot):
    token = bot.store.config.reviewer_token
    if not token:
        st.info("Для кабинета администратора задайте RAG_REVIEW_TOKEN.")
        return
    entered = st.text_input("Ключ администратора", type="password")
    if not hmac.compare_digest(entered.encode(), token.encode()):
        if entered:
            st.error("Ключ не подходит.")
        return
    files = st.file_uploader("Документы для базы знаний", accept_multiple_files=True,
                             type=sorted(x[1:] for x in SUPPORTED))
    if st.button("Добавить документы", disabled=not files):
        for file in files:
            file.seek(0)
            job = bot.upload(file.name, file)
            st.success(f"{file.name} · задание №{job}")
    if st.button("Синхронизировать папку", help=str(bot.store.config.documents)):
        job = bot.enqueue("folder", str(bot.store.config.documents), "")
        st.success(f"Задание №{job}")
    with st.expander("Очередь индексации"):
        st.button("Обновить статусы")
        st.dataframe(bot.jobs())
    records = {r["id"]: r for r in bot.store.tickets()}
    if not records:
        st.info("Заявок пока нет.")
        return
    number = st.selectbox("Заявка", list(records), format_func=lambda n: f"№{n} · " +
                          (records[n]["publication"] if records[n]["resolved"] else "открыта"))
    row = records[number]
    turns = json.loads(row["dialogue"])
    for turn in turns:
        st.caption("Сотрудник" if turn["role"] == "user" else "Агент")
        st.text(turn["content"])
    with st.form(f"review-{number}-{row['revision']}"):
        topic = st.text_input("Вопрос со всеми условиями", value=row["topic"] or turns[-1]["content"][:600], max_chars=600)
        answer = st.text_area("Ответ", value=row["answer"] or "", max_chars=1500)
        name = st.text_input("Имя специалиста", value=row["reviewer"] or "", max_chars=120)
        private = st.form_submit_button("Ответить сотруднику")
        publish = st.form_submit_button("Ответить и добавить в базу знаний")
        if private or publish:
            bot.store.review(number, topic, answer, name, publish, row["revision"])
            bot.wake.set()
            st.rerun()


st.set_page_config(page_title="Регламенты компании")
st.title("Регламенты компании")
try:
    bot = connect()
    if st.sidebar.radio("Рабочее место", ["Пользователь", "Администратор"]) == "Администратор":
        admin(bot)
    else:
        chat(bot)
except Exception as exc:
    st.error(str(exc))

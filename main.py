import atexit
import hmac
import json
import uuid

import streamlit as st

from agent import respond
from config import Config
from indexer import sync
from ollama_api import ModelService
from storage import Store


@st.cache_resource
def connect():
    config = Config()
    store = Store(config, ModelService(config))
    atexit.register(store.client.close)
    return store


def ranking(hits):
    if not hits:
        return
    with st.expander("Ранжирование источников"):
        for i, hit in enumerate(hits, 1):
            p = hit.passage
            st.text(f"{i}. {p.source}  {p.location}")
            st.caption(f"RRF {hit.score:.4f} · Смысловой ранг {hit.dense_rank or '—'} · "
                       f"Лексический ранг {hit.lexical_rank or '—'}")
            st.text(p.body)


def chat(store):
    state = st.session_state
    if not store.has_sources():
        st.info("База пока пуста. Специалист может загрузить регламенты. Вопросы сотрудников будут переданы в службу качества.")
    state.setdefault("history", [])
    state.setdefault("dialogue", [])
    state.setdefault("ref", uuid.uuid4().hex)
    state.setdefault("clarifications", 0)
    if st.button("Новый вопрос", help="Начать отдельный диалог"):
        state.history, state.dialogue, state.ref = [], [], uuid.uuid4().hex
        state.clarifications = 0
        state.pop("pending", None)
    for question, reply, ref in state.history:
        with st.chat_message("user"):
            st.text(question)
        with st.chat_message("assistant"):
            st.text(reply.text)
            if reply.source:
                st.caption(reply.source)
            if reply.ticket_id:
                with store.db() as conn:
                    row = conn.execute("SELECT answer,reviewer FROM tickets WHERE ref=?", (ref,)).fetchone()
                if row and row["answer"]:
                    st.success(f"Ответ службы качества · {row['reviewer']}")
                    st.text(row["answer"])
                else:
                    st.button("Проверить ответ службы качества", key=f"refresh_{ref}")
            ranking(reply.hits)
    prompt = st.chat_input("Ваш вопрос о процессе", max_chars=1200, disabled="pending" in state)
    if prompt and prompt.strip():
        state.pending = prompt.strip()
    if "pending" in state:
        question = state.pending
        turns = (state.dialogue + [{"role": "user", "content": question}])[-store.config.context_messages:]
        try:
            with st.spinner("Проверяю регламенты…"):
                reply = respond(store, turns, state.ref, state.clarifications)
        except Exception as exc:
            st.error(f"Не удалось обработать вопрос. Проверьте доступность сервисов. {exc}")
            st.button("Повторить обработку")
            if st.button("Отменить вопрос"):
                del state.pending
                st.rerun()
            return
        state.history.append((question, reply, state.ref))
        del state.pending
        state.dialogue = (turns + [{"role": "assistant", "content": reply.text}])[-store.config.context_messages:]
        if reply.kind == "clarify":
            state.clarifications += 1
        else:
            state.clarifications, state.ref = 0, uuid.uuid4().hex
        st.rerun()


def quality(store):
    token = store.config.reviewer_token
    if not token:
        st.info("Для работы специалиста задайте RAG_REVIEW_TOKEN и перезапустите приложение.")
        return
    entered = st.text_input("Ключ службы качества", type="password")
    if not hmac.compare_digest(entered.encode(), token.encode()):
        if entered:
            st.error("Ключ не подходит.")
        return
    with st.expander("Обновление регламентов"):
        st.text(str(store.config.documents))
        if st.button("Синхронизировать папку"):
            try:
                st.success(sync(store, store.config.documents))
            except Exception as exc:
                st.error(str(exc))
    tickets = store.tickets()
    opened = [row for row in tickets if not row["resolved"]]
    if opened:
        records = {row["id"]: row for row in opened}
        number = st.selectbox("Открытая заявка", list(records), format_func=lambda n: f"№{n}")
        turns = json.loads(records[number]["dialogue"])
        for turn in turns:
            st.caption("Сотрудник" if turn["role"] == "user" else "Агент")
            st.text(turn["content"])
        with st.form(f"approve_{number}"):
            topic = st.text_input("Вопрос со всеми условиями", value=turns[-1]["content"][:600], max_chars=600)
            answer = st.text_area("Утверждённый ответ", max_chars=1500)
            name = st.text_input("Имя специалиста", max_chars=120)
            if st.form_submit_button("Утвердить и добавить в базу"):
                try:
                    store.approve(number, topic, answer, name)
                except Exception as exc:
                    st.error(str(exc))
                else:
                    st.rerun()
    else:
        st.info("Открытых заявок нет.")
    closed = [r for r in tickets if r["resolved"]]
    if closed:
        with st.expander(f"Утверждённые ответы · {len(closed)}"):
            for r in closed:
                st.text(f"№{r['id']} · {r['reviewer']} · {r['resolved']} UTC\n{r['topic']}\n{r['answer']}")


st.set_page_config(page_title="Регламенты компании")
st.title("Регламенты компании")
try:
    store = connect()
except Exception as exc:
    st.error(f"Не удалось открыть хранилище: {exc}")
    st.stop()
mode = st.sidebar.radio("Рабочее место", ["Вопросы по процессам", "Служба качества"])
if mode == "Служба качества":
    quality(store)
else:
    chat(store)

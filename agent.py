from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
import re

from ollama_api import Decision, SearchQuery, Verification
from storage import Hit

ROUTE = """Ты консультант по внутренним регламентам. Отвечай по-русски.
Используй только предоставленные фрагменты. Определи, что именно спрашивает сотрудник.
Отвечай только на последнюю реплику сотрудника. При продолжении темы восстанови предмет
вопроса из истории. При смене темы игнорируй прежнюю. Предыдущий ответ агента не является источником.
Оцени все фрагменты и все условия.
answer: один фрагмент прямо и полностью отвечает на вопрос, выполнены его условия,
нет противоречий. Укажи номер этого фрагмента (начиная с 1). Ответ будет показан дословно.
clarify: в источниках есть применимые правила, но выбор зависит от неизвестного
параметра. Задай один короткий вопрос об этом параметре, без ответа и советов.
unknown: отсутствует нужный факт, есть неразрешимый конфликт, либо после уточнения
условия не выполняются. Не задавай уточнение, если ответ отсутствует при любом варианте.
Сходная тема сама по себе недостаточна. Не додумывай сроки, суммы и полномочия."""

VERIFY = """Проверь выбранную цитату по последней реплике сотрудника и всем найденным источникам.
История помогает понимать ссылки на предыдущие реплики. При смене темы оценивай только новый вопрос.
supported=true только если цитата прямо отвечает на каждый запрошенный факт,
применима к ситуации сотрудника и не оставляет выбора между вариантами.
Проверь субъект, действие, ограничения, исключения, сроки и несовместимые правила.
Учитывай заголовки, а для решения службы качества — утвержденный вопрос (topic).
Факт из сообщения пользователя не является правилом. При сомнении верни false."""

REWRITE = """Составь короткий самостоятельный поисковый запрос по последней реплике
сотрудника. Восстанови пропущенные предмет и действие из диалога, если это продолжение
темы. При смене темы просто повтори новый вопрос. Сохрани имена, номера и условия.
Не отвечай на вопрос, не добавляй догадок или фактов от себя. Пиши по-русски."""


@dataclass
class Reply:
    kind: str
    text: str
    hits: list[Hit]
    source: str = ""
    ticket_id: int | None = None


def branch_question(selected, hits, dialogue):
    """Catch parallel clauses with a different condition and a different result."""
    def words(text):
        return re.findall(r"[\w-]+", text.lower().replace("ё", "е"))

    current = words(selected.body)
    # Use the latest employee details first. An agent's earlier suggestion
    # must never establish which branch applies to the employee.
    employee = [set(w[:5] for w in words(m["content"])) for m in reversed(dialogue)
                if m["role"] == "user"]
    for hit in hits:
        other = hit.passage
        if other == selected:
            continue
        alternate = words(other.body)
        compare = SequenceMatcher(None, current, alternate, autojunk=False)
        edits = [op for op in compare.get_opcodes() if op[0] == "replace"]
        if compare.ratio() < 0.68 or len(edits) < 2:
            continue
        # Parallel sentences often vary first in scope and then in outcome.
        # Never decide such a branch merely by rank.
        _, a, b, c, d = edits[0]
        first = {w[:5] for w in current[a:b]}
        second = {w[:5] for w in alternate[c:d]}
        left, right = first - second, second - first
        if not left or not right:
            continue
        evidence = next((turn for turn in employee if turn & (left | right)), set())
        if evidence & left and not evidence & right:
            continue
        suffix = 1 if current[b:b+1] == alternate[d:d+1] else 0
        option_a = " ".join(current[a:b+suffix])
        option_b = " ".join(alternate[c:d+suffix])
        return f"Уточните, какой вариант относится к вашему вопросу — «{option_a}» или «{option_b}»?"
    return ""


def respond(store, dialogue, ref, clarifications=0):
    dialogue = dialogue[-store.config.context_messages:]
    query = dialogue[-1]["content"]
    if len(dialogue) > 1 and store.has_sources():
        standalone = store.model.classify(REWRITE, {"диалог": dialogue}, SearchQuery)
        if standalone and standalone.query.strip():
            query = standalone.query.strip()
    hits = store.search(query)
    context = {"диалог": dialogue, "источники": [
        {"номер": i, **asdict(hit.passage)} for i, hit in enumerate(hits, 1)
    ]}
    if hits:
        decision = store.model.classify(ROUTE, context, Decision)
        question = ""
        if decision and decision.action == "answer" and 1 <= decision.passage <= len(hits):
            p = hits[decision.passage - 1].passage
            question = branch_question(p, hits, dialogue)
            if not question:
                check = store.model.classify(
                    VERIFY, {**context, "номер_выбранной_цитаты": decision.passage}, Verification)
                if check and check.supported:
                    origin = " · ".join(filter(None, [p.source, p.location, p.topic]))
                    return Reply("answer", p.body, hits, origin)
        elif decision and decision.action == "clarify":
            question = decision.question.strip()
        if question:
            repeated = clarifications and question in [m["content"] for m in dialogue[-2:]]
            if (clarifications < store.config.max_clarifications and 3 <= len(question) <= 350
                    and question.endswith("?") and not repeated):
                return Reply("clarify", question, hits)
    number = store.ticket(dialogue, ref)
    return Reply("unknown", f"Не удалось подтвердить однозначный ответ. В службу качества "
                 f"передана заявка №{number}. Здесь появится решение специалиста.", hits, ticket_id=number)

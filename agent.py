from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
import re

from ollama_api import Decision, Intent, SearchQuery, Verification
from storage import Hit

ROUTE = """Ты консультант по внутренним регламентам. Отвечай по-русски.
Используй только предоставленные фрагменты. Определи, что именно спрашивает сотрудник.
Отвечай только на последнюю реплику сотрудника. При продолжении темы восстанови предмет
вопроса из истории. При смене темы игнорируй прежнюю. Предыдущий ответ агента не является источником.
Оцени все фрагменты и все условия.
answer: фрагмент или сочетание нескольких фрагментов прямо отвечает на вопрос, выполнены условия,
нет противоречий. Укажи основной номер (начиная с 1). В additional укажи только
нужные для других частей вопроса номера. Выбранные цитаты будут показаны дословно.
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

SCOPE = """Классифицируй последнюю реплику. standalone — самостоятельный вопрос о работе
компании, даже грубо заданный или без известного регламента. followup — короткое
продолжение, например «А с юридическим?» или «С юрлицом». off_topic — оскорбление без рабочего
вопроса, бессмыслица, игры, стихи, общие знания и посторонние просьбы. Не придумывай
рабочий смысл такому сообщению из истории. Не отвечай на вопрос."""
OUTSIDE = "Я отвечаю на вопросы о процессах компании. Уточните, какой рабочий процесс вас интересует."


def message_kind(model, query):
    words = " ".join(re.findall(r"[\w]+", query.casefold()))
    # Clear standalone procedural questions need no model rewrite or history.
    asks = re.search(r"\b(кто|где|когда|кому|како\w*|сколько|котор\w*|в чем)\b", words)
    subject = re.search(r"\b(договор\w*|регламент\w*|пресейл\w*|заявк\w*|проект\w*|сделк\w*)\b", words)
    action = re.search(r"\b(подпис\w*|соглас\w*|хран\w*|регистр\w*|расч[её]т\w*|скоринг\w*|заполня\w*|подготов\w*|ответствен\w*)\b", words)
    if asks and subject and action:
        return "standalone"
    if re.fullmatch(r"(соси (хуй|х3уй)|иди на хуй|привет|спасибо)", words):
        return "off_topic"
    intent = model.classify(SCOPE, {"реплика": query}, Intent)
    if intent is None:
        raise RuntimeError("Не удалось определить тему запроса. Повторите вопрос.")
    return intent.kind


@dataclass
class Reply:
    kind: str
    text: str
    hits: list[Hit]
    source: str = ""
    ticket_id: int | None = None
    used: list[int] = field(default_factory=list)  # Zero-based positions in hits


def branch_question(selected, hits, dialogue, cited=()):
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
        if other in cited and evidence & left and evidence & right:
            continue
        if evidence & left and not evidence & right:
            continue
        suffix = 1 if current[b:b+1] == alternate[d:d+1] else 0
        option_a = " ".join(current[a:b+suffix])
        option_b = " ".join(alternate[c:d+suffix])
        return f"Уточните, какой вариант относится к вашему вопросу — «{option_a}» или «{option_b}»?"
    return ""


def respond(store, dialogue, ref, clarifications=0, owner=""):
    dialogue = dialogue[-store.config.context_messages:]
    query = dialogue[-1]["content"]
    # Determine scope before reading history. Identical standalone inputs reach
    # retrieval, routing and verification with identical prompts.
    kind = message_kind(store.model, query)
    if kind == "off_topic":
        return Reply("off_topic", OUTSIDE, [])
    if kind == "followup" and len(dialogue) > 1:
        rewrite = store.model.classify("Восстанови самостоятельный поисковый запрос из диалога. "
                                       "Сохрани условия и названия. Не отвечай.", {"диалог": dialogue}, SearchQuery)
        if rewrite:
            query = rewrite.query
    elif kind == "followup":
        return Reply("clarify", "О каком процессе или документе вы спрашиваете?", [])
    else:
        dialogue = dialogue[-1:]
        clarifications = 0
    hits = store.search(query)
    context = {"диалог": dialogue, "источники": [
        {"номер": i, **asdict(hit.passage)} for i, hit in enumerate(hits, 1)
    ]}
    if hits:
        decision = store.model.classify(ROUTE, context, Decision)
        question = ""
        if decision and decision.action == "answer" and 1 <= decision.passage <= len(hits):
            numbers = list(dict.fromkeys([decision.passage, *decision.additional]))
            groups = [numbers[:1]]
            if len(numbers) > 1 and all(1 <= n <= len(hits) for n in numbers):
                groups.append(numbers)
            # Prefer the smallest group that fully answers the question.
            for group in groups:
                selected = [hits[n-1].passage for n in group]
                question = next((q for p in selected if (q := branch_question(p, hits, dialogue, selected))), "")
                if question:
                    continue
                check = store.model.classify(
                    VERIFY, {**context, "номера_цитат_в_ответе": group}, Verification)
                if check and check.supported:
                    origin = "\n".join(" · ".join(filter(None, (p.source, p.location, p.topic))) for p in selected)
                    return Reply("answer", "\n\n".join(p.body for p in selected), hits, origin,
                                 used=[n-1 for n in group])
        elif decision and decision.action == "clarify":
            question = decision.question.strip()
        if question:
            repeated = clarifications and question in [m["content"] for m in dialogue[-2:]]
            if (clarifications < store.config.max_clarifications and 3 <= len(question) <= 350
                    and question.endswith("?") and not repeated):
                return Reply("clarify", question, hits)
    number = store.ticket(dialogue, ref, owner)
    return Reply("unknown", f"Не удалось подтвердить однозначный ответ. В службу качества "
                 f"передана заявка №{number}. Здесь появится решение специалиста.", hits, ticket_id=number)

from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
import re

from documents import evidence_units
from ollama_api import Clarification, Decision, Entailment, Intent, SearchQuery, Verification
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
параметра. Спроси у сотрудника только недостающее условие, например «Договор с физическим
или юридическим лицом?». Не проси его назвать подписанта, если он сам спрашивает, кто подписывает.
unknown: отсутствует нужный факт, есть неразрешимый конфликт, либо после уточнения
условия не выполняются. Не задавай уточнение, если ответ отсутствует при любом варианте.
Сходная тема сама по себе недостаточна. Не додумывай сроки, суммы и полномочия."""

VERIFY = """Проверь выбранную цитату по последней реплике сотрудника и всем найденным источникам.
История помогает понимать ссылки на предыдущие реплики. При смене темы оценивай только новый вопрос.
supported=true только если цитата прямо отвечает на каждый запрошенный факт,
применима к ситуации сотрудника и не оставляет выбора между вариантами.
Проверь субъект, действие, ограничения, исключения, сроки и несовместимые правила.
Учитывай заголовки, а для решения службы качества — утвержденный вопрос (topic).
Проверь предмет договора, действие, роль и условия. Ответственность за подготовку или
согласование сама по себе не устанавливает право подписи. Область применения должна
следовать из источника. В units укажи по одному номеру строки внутри каждого
выбранного фрагмента в том же порядке. Нумерация строк начинается с 1. При сомнении верни supported=false."""

CLARIFY = """Можно ли выбрать применимое правило, узнав одно недостающее условие?
Верни номера источников с правилами и один короткий вопрос именно об условии.
Сравни разные области применения и результаты, в том числе внутри одного документа.
Более высокое место в выдаче не определяет, какое условие верно для сотрудника.
Учитывай только условия, сообщённые сотрудником. Не проси назвать ответ на исходный вопрос.
Если нужного правила вообще нет, верни пустые question и evidence."""

SCOPE = """Классифицируй последнюю реплику. standalone — самостоятельный вопрос о работе
компании, даже грубо заданный или без известного регламента. followup — короткое
продолжение, например «А с юридическим?» или «С юрлицом». Если передан уточняющий вопрос
агента, выбор одного варианта или ответ на запрошенное условие — followup, даже в одно слово.
off_topic — оскорбление без рабочего
вопроса, бессмыслица, игры, стихи, общие знания и посторонние просьбы. Не придумывай
рабочий смысл такому сообщению из истории. Не отвечай на вопрос."""
OUTSIDE = "Я отвечаю на вопросы о процессах компании. Уточните, какой рабочий процесс вас интересует."


def message_kind(model, query, pending=None):
    words = " ".join(re.findall(r"[\w]+", query.casefold()))
    # Clear standalone procedural questions need no model rewrite or history.
    asks = re.match(r"^(?:(?:подскажите|пожалуйста|скажите|а)\s+)*(кто|где|когда|кому|како\w*|сколько|в чем)\b", words)
    subject = re.search(r"\b(договор\w*|регламент\w*|пресейл\w*|заявк\w*|проект\w*|сделк\w*)\b", words)
    action = re.search(r"\b(подпис\w*|соглас\w*|хран\w*|регистр\w*|расч[её]т\w*|скоринг\w*|заполня\w*|подготов\w*|ответствен\w*)\b", words)
    if asks and subject and action:
        return "standalone"
    if re.fullmatch(r"(соси (хуй|х3уй)|иди на хуй|привет|спасибо)", words):
        return "off_topic"
    data = {"реплика": query}
    if pending:
        # Recognise a short choice even when its Russian case ending differs.
        options = {w[:5] for w in re.findall(r"\w{5,}", pending.casefold())}
        if len(words.split()) <= 4 and not asks and any(w[:5] in options for w in words.split()):
            return "followup"
        data["уточняющий вопрос агента"] = pending
    intent = model.classify(SCOPE, data, Intent)
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
    standalone: bool = False
    pending_query: str = ""


def branch_question(selected, hits, dialogue, cited=()):
    """Catch parallel clauses with a different condition and a different result."""
    def words(text):
        return re.findall(r"[\w-]+", text.lower().replace("ё", "е"))

    current = words(selected.body)
    # Use the latest employee details first. An agent's earlier suggestion
    # must never establish which branch applies to the employee.
    employee = [set(w[:5] for w in words(m["content"])) for m in reversed(dialogue)
                if m["role"] == "user"]
    for i, hit in enumerate(hits, 1):
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
        if evidence & right and not evidence & left:
            return "", i
        suffix = 1 if current[b:b+1] == alternate[d:d+1] else 0
        option_a = " ".join(current[a:b+suffix])
        option_b = " ".join(alternate[c:d+suffix])
        return f"Уточните, какой вариант относится к вашему вопросу — «{option_a}» или «{option_b}»?", None
    return "", None


def respond(store, dialogue, clarifications=0, pending_query=""):
    dialogue = dialogue[-store.config.context_messages:]
    query = dialogue[-1]["content"]
    pending = dialogue[-2]["content"] if clarifications and len(dialogue) >= 2 else None
    kind = message_kind(store.model, query, pending) if pending else None
    exact = [] if kind == "followup" else store.exact_faq(query)
    if exact:
        if len({h.passage.body for h in exact}) > 1:
            return Reply("unknown", "Опубликованные решения расходятся. Можно обратиться в службу качества.", exact)
        return Reply("answer", exact[0].passage.body, exact, exact[0].passage.source, used=[0], standalone=True)
    kind = kind or message_kind(store.model, query)
    if kind == "off_topic":
        return Reply("off_topic", OUTSIDE, [])
    if kind == "followup" and len(dialogue) > 1:
        if pending:
            original = pending_query or (dialogue[-3]["content"] if len(dialogue) >= 3 else pending)
            query = f"{original}\nУточнение сотрудника: {query}"
        else:
            rewrite = store.model.classify("Восстанови самостоятельный поисковый запрос из диалога. "
                                       "Сохрани условия и названия. Не отвечай.", {"диалог": dialogue}, SearchQuery)
            if rewrite:
                query = rewrite.query
    elif kind == "followup":
        return Reply("clarify", "О каком процессе или документе вы спрашиваете?", [])
    else:
        dialogue = dialogue[-1:]
        clarifications = 0
    subject = store.model.classify(
        "Выдели предмет поиска и запрошенное действие в короткой именной фразе. "
        "Сохрани вид документа или процесса. Опусти частные варианты и не добавляй факты.",
        {"вопрос": query}, SearchQuery) if store.has_sources() else None
    hits = store.search(query, subject.query if subject else None)
    context = {"диалог": dialogue, "поисковый вопрос": query, "источники": [
        {"номер": i, **asdict(hit.passage)} for i, hit in enumerate(hits, 1)
    ]}
    if hits:
        def clarification(text, condition=""):
            text = text.strip().rstrip("?.! ").strip()
            repeated = any(m["role"] == "assistant" and m["content"].strip().rstrip("?.! ").casefold() == text.casefold()
                           for m in dialogue)
            if 3 <= len(text) <= 349 and clarifications < store.config.max_clarifications and not repeated:
                basis = f"{query}\nУточняемое условие: {condition}" if condition else query
                return Reply("clarify", text + "?", hits, pending_query=basis)
            return Reply("unknown", "Уточнений недостаточно для однозначного ответа по регламенту.", hits)

        # Inspect applicability before allowing the router to choose the first plausible hit.
        choice = store.model.classify(CLARIFY, context, Clarification)
        if choice is None:
            return Reply("unknown", "Не удалось проверить условия применения регламента.", hits)
        if choice.question.strip() and choice.evidence and all(1 <= n <= len(hits) for n in choice.evidence):
            return clarification(choice.question, choice.condition.strip())
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
                checks = [branch_question(p, hits, dialogue, selected) for p in selected]
                question = next((q for q, _ in checks if q), "")
                corrected = list(dict.fromkeys(other or n for n, (_, other) in zip(group, checks)))
                if corrected != group:
                    if corrected not in groups:
                        groups.append(corrected)
                    continue
                if question:
                    continue
                units = [evidence_units(p.body) for p in selected]
                numbered = {n: dict(enumerate(lines, 1)) for n, lines in zip(group, units)}
                check = store.model.classify(VERIFY, {**context, "номера_цитат_в_ответе": group,
                                                      "строки": numbered}, Verification)
                if not check or not check.supported or len(check.units) != len(group):
                    continue
                if not all(1 <= i <= len(lines) for i, lines in zip(check.units, units)):
                    continue
                quotes = [lines[i-1] for i, lines in zip(check.units, units)]
                proof = [dict(source=p.source, location=p.location, topic=p.topic, quote=q)
                         for p, q in zip(selected, quotes)]
                fact = store.model.classify(
                    "Подтверждают ли приведённые строки прямо и полностью запрошенный факт при условиях "
                    "сотрудника? Назови supported=true только если ответ можно прочитать в этих строках. "
                    "Совпадение терминов, предложение сотрудника и предыдущий ответ агента доказательством не являются.",
                    {"диалог": dialogue, "вопрос": query, "цитаты": proof}, Entailment)
                if fact and fact.supported:
                    origin = "\n".join(" · ".join(filter(None, (p.source, p.location, p.topic))) for p in selected)
                    return Reply("answer", "\n\n".join(quotes), hits, origin,
                                 used=[n-1 for n in group], standalone=(kind == "standalone"))
        elif decision and decision.action == "clarify":
            question = decision.question.strip()
        if question:
            return clarification(question)
    return Reply("unknown", "Не удалось подтвердить однозначный ответ по базе знаний. "
                 "Вы можете отправить вопрос в службу качества.", hits)

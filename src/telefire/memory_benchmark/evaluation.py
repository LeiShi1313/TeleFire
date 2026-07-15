from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
import hashlib
from itertools import zip_longest
import json
from pathlib import Path
from typing import Any, Iterable

from openai import AsyncOpenAI

from telefire.memory_benchmark.backends import MemoryRecord
from telefire.memory_benchmark.source import SourceCorpus, SourceDocument


_CASE_CATEGORIES = {
    "direct",
    "identity_alias",
    "relationship",
    "temporal",
    "preference",
    "project",
    "multi_document",
}
_LINKED_CATEGORIES = {"identity_alias", "relationship", "multi_document"}


@dataclass(frozen=True, slots=True)
class Evidence:
    document_id: str
    quote: str


@dataclass(frozen=True, slots=True)
class RecallCase:
    case_id: str
    category: str
    question: str
    answer: str
    evidence: tuple[Evidence, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> RecallCase:
        supplied = dict(value)
        evidence = tuple(Evidence(**item) for item in supplied.pop("evidence"))
        return cls(evidence=evidence, **supplied)


class OpenAIJSONClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        reasoning_effort: str = "low",
    ) -> None:
        self.model = model
        self.reasoning_effort = reasoning_effort
        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=180)

    async def close(self) -> None:
        await self._client.close()

    async def complete_json(
        self,
        *,
        system: str,
        prompt: str,
        max_completion_tokens: int = 5_000,
    ) -> dict[str, Any]:
        latest_error: Exception | None = None
        for attempt in range(3):
            try:
                response = await self._client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ],
                    response_format={"type": "json_object"},
                    max_completion_tokens=max_completion_tokens,
                    reasoning_effort=self.reasoning_effort,
                )
                content = response.choices[0].message.content
                if not isinstance(content, str):
                    raise ValueError("Judge returned an empty response")
                return parse_json_object(content)
            except Exception as exc:
                latest_error = exc
                if attempt < 2:
                    await asyncio.sleep(2**attempt)
        assert latest_error is not None
        raise latest_error


def parse_json_object(value: str) -> dict[str, Any]:
    stripped = value.strip()
    if stripped.startswith("```"):
        first_newline = stripped.find("\n")
        stripped = stripped[first_newline + 1 :] if first_newline >= 0 else stripped
        if stripped.endswith("```"):
            stripped = stripped[:-3]
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start < 0 or end < start:
        raise ValueError("LLM response does not contain a JSON object")
    parsed = json.loads(stripped[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("LLM response is not a JSON object")
    return parsed


def render_document(document: SourceDocument) -> str:
    lines = [f"[Document: {document.document_id}]"]
    lines.extend(
        f"[{event.occurred_at}] {event.actor_name} ({event.actor_id}): {event.text}"
        for event in document.events
    )
    return "\n".join(lines)


def validate_recall_case(
    supplied: Any,
    documents: dict[str, SourceDocument],
) -> RecallCase | None:
    if not isinstance(supplied, dict):
        return None
    category = supplied.get("category")
    question = supplied.get("question")
    answer = supplied.get("answer")
    evidence_items = supplied.get("evidence")
    if (
        category not in _CASE_CATEGORIES
        or not isinstance(question, str)
        or len(question.strip()) < 4
        or not isinstance(answer, str)
        or len(answer.strip()) < 1
        or not isinstance(evidence_items, list)
        or not evidence_items
    ):
        return None

    evidence = []
    for item in evidence_items:
        if not isinstance(item, dict):
            return None
        document_id = item.get("document_id")
        quote = item.get("quote")
        document = documents.get(document_id) if isinstance(document_id, str) else None
        if (
            document is None
            or not isinstance(quote, str)
            or len(quote.strip()) < 2
            or not any(quote in event.text for event in document.events)
        ):
            return None
        evidence.append(Evidence(document_id=document_id, quote=quote))

    normalized_question = question.strip()
    normalized_answer = answer.strip()
    fingerprint = json.dumps(
        [normalized_question, normalized_answer, [asdict(item) for item in evidence]],
        ensure_ascii=False,
        sort_keys=True,
    )
    case_id = hashlib.sha256(fingerprint.encode()).hexdigest()[:16]
    return RecallCase(
        case_id=case_id,
        category=category,
        question=normalized_question,
        answer=normalized_answer,
        evidence=tuple(evidence),
    )


async def generate_recall_cases(
    corpus: SourceCorpus,
    client: OpenAIJSONClient,
    *,
    target: int = 60,
    concurrency: int = 3,
) -> tuple[RecallCase, ...]:
    documents = {document.document_id: document for document in corpus.documents}
    packs = _generation_packs(corpus)
    semaphore = asyncio.Semaphore(concurrency)

    async def generate(mode: str, pack: tuple[SourceDocument, ...]) -> list[RecallCase]:
        prompt = _generation_prompt(mode, pack)
        async with semaphore:
            response = await client.complete_json(
                system=(
                    "你是聊天记忆系统的独立评测集设计者。只根据提供的聊天原文创建问题，"
                    "严格区分每位发言者，不得补充常识或猜测。输出合法 JSON。"
                ),
                prompt=prompt,
            )
        supplied_cases = response.get("cases")
        if not isinstance(supplied_cases, list):
            raise ValueError("Case generator returned malformed cases")
        allowed_documents = {document.document_id for document in pack}
        cases = []
        for supplied in supplied_cases:
            case = validate_recall_case(supplied, documents)
            if case is not None and all(
                evidence.document_id in allowed_documents for evidence in case.evidence
            ):
                cases.append(case)
        return cases

    generated = await asyncio.gather(
        *(generate(mode, pack) for mode, pack in packs)
    )
    unique: dict[str, RecallCase] = {}
    for case in (case for group in generated for case in group):
        unique.setdefault(case.case_id, case)

    linked = [case for case in unique.values() if case.category in _LINKED_CATEGORIES]
    direct = [case for case in unique.values() if case.category not in _LINKED_CATEGORIES]
    linked_target = min(len(linked), max(1, target // 4))
    selected = linked[:linked_target] + direct[: target - linked_target]
    if len(selected) < target:
        selected_ids = {case.case_id for case in selected}
        selected.extend(
            case
            for case in unique.values()
            if case.case_id not in selected_ids
        )
    if len(selected) < target:
        raise RuntimeError(
            f"Generated only {len(selected)} validated recall cases; expected {target}"
        )
    return tuple(selected[:target])


async def grade_recall(
    client: OpenAIJSONClient,
    case: RecallCase,
    contexts: dict[str, str],
) -> dict[str, dict[str, Any]]:
    backends = sorted(contexts)
    if int(case.case_id[-1], 16) % 2:
        backends.reverse()
    labels = {chr(65 + index): backend for index, backend in enumerate(backends)}
    payload = {
        "question": case.question,
        "reference_answer": case.answer,
        "source_evidence": [asdict(item) for item in case.evidence],
        "retrieved_contexts": {
            label: contexts[backend][:20_000] for label, backend in labels.items()
        },
    }
    response = await client.complete_json(
        system=(
            "你是记忆检索评测裁判。根据参考答案和原始证据，独立判断每个匿名检索上下文"
            "是否足以回答问题。不要因为措辞不同扣分，也不要使用外部知识。输出合法 JSON。"
        ),
        prompt=(
            "请为每个上下文评分并返回："
            '{"grades":[{"label":"A","answer_coverage":0到4,'
            '"attribution":0到4,"temporal":0到4或null,'
            '"contradiction":true或false,"reason":"简短理由"}]}。\n'
            "answer_coverage: 0=无相关信息，2=部分可答，3=基本可答，4=完整准确；"
            "attribution: 是否归属正确的人；temporal: 涉及时序时评分，否则 null；"
            "contradiction: 上下文是否包含会导致错误答案的矛盾信息。\n\n"
            + json.dumps(payload, ensure_ascii=False)
        ),
        max_completion_tokens=2_500,
    )
    supplied_grades = response.get("grades")
    if not isinstance(supplied_grades, list):
        raise ValueError("Recall judge returned malformed grades")
    result = {}
    for grade in supplied_grades:
        if not isinstance(grade, dict) or grade.get("label") not in labels:
            continue
        result[labels[grade["label"]]] = _validated_recall_grade(grade)
    if set(result) != set(backends):
        raise ValueError("Recall judge omitted a backend")
    return result


async def grade_extraction_batch(
    client: OpenAIJSONClient,
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    response = await client.complete_json(
        system=(
            "你是记忆抽取质量评测裁判。逐条比较抽取记忆与其原始聊天证据，严格区分"
            "不同发言者，不使用外部知识。输出合法 JSON。"
        ),
        prompt=(
            "请返回："
            '{"grades":[{"memory_id":"...","faithfulness":0到4,'
            '"attribution":0到4,"specificity":0到4,"usefulness":0到4,'
            '"temporal":0到4或null,"unsupported_claim":true或false,'
            '"overcombined":true或false,"reason":"简短理由"}]}。\n'
            "faithfulness 衡量是否被原文支持；attribution 衡量是否归属正确的人；"
            "specificity 衡量是否保留关键限定；usefulness 衡量未来回忆价值；"
            "temporal 仅在有时间信息时评分；unsupported_claim 表示含无证据主张；"
            "overcombined 表示错误合并了不同人物或事件。\n\n"
            + json.dumps({"items": items}, ensure_ascii=False)
        ),
        max_completion_tokens=4_000,
    )
    supplied_grades = response.get("grades")
    if not isinstance(supplied_grades, list):
        raise ValueError("Extraction judge returned malformed grades")
    expected = {item["memory_id"] for item in items}
    grades = []
    for grade in supplied_grades:
        if not isinstance(grade, dict) or grade.get("memory_id") not in expected:
            continue
        grades.append(_validated_extraction_grade(grade))
    if {grade["memory_id"] for grade in grades} != expected:
        raise ValueError("Extraction judge omitted a memory")
    return grades


def sample_memory_records(
    records: Iterable[MemoryRecord],
    *,
    limit: int,
) -> tuple[MemoryRecord, ...]:
    groups: dict[str, list[MemoryRecord]] = {}
    for record in records:
        groups.setdefault(record.memory_type, []).append(record)
    for group in groups.values():
        group.sort(key=lambda record: hashlib.sha256(record.memory_id.encode()).hexdigest())

    selected = []
    group_names = sorted(groups)
    while len(selected) < limit:
        progressed = False
        for name in group_names:
            if groups[name]:
                selected.append(groups[name].pop(0))
                progressed = True
                if len(selected) == limit:
                    break
        if not progressed:
            break
    return tuple(selected)


def write_cases(cases: Iterable[RecallCase], path: Path) -> None:
    _write_json(
        path,
        {
            "schema": "telefire.memory-benchmark.recall-cases.v1",
            "cases": [case.to_dict() for case in cases],
        },
    )


def read_cases(path: Path) -> tuple[RecallCase, ...]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema") != "telefire.memory-benchmark.recall-cases.v1":
        raise ValueError("Unsupported recall case schema")
    return tuple(RecallCase.from_dict(item) for item in value["cases"])


def _generation_packs(
    corpus: SourceCorpus,
) -> list[tuple[str, tuple[SourceDocument, ...]]]:
    direct_documents = _spread(corpus.documents, min(144, len(corpus.documents)))
    direct = [("direct", pack) for pack in _pack_documents(direct_documents)]

    actor_documents: dict[str, list[SourceDocument]] = {}
    for document in corpus.documents:
        for actor_id in {event.actor_id for event in document.events}:
            actor_documents.setdefault(actor_id, []).append(document)
    eligible = sorted(
        (documents for documents in actor_documents.values() if len(documents) >= 3),
        key=lambda documents: (-len(documents), documents[0].document_id),
    )[:18]
    linked = [
        ("linked", tuple(_spread(tuple(documents), min(4, len(documents)))))
        for documents in eligible
    ]

    interleaved = []
    for direct_item, linked_item in zip_longest(direct, linked):
        if direct_item is not None:
            interleaved.append(direct_item)
        if linked_item is not None:
            interleaved.append(linked_item)
    return interleaved


def _pack_documents(
    documents: tuple[SourceDocument, ...],
    *,
    max_documents: int = 6,
    max_characters: int = 26_000,
) -> list[tuple[SourceDocument, ...]]:
    packs: list[tuple[SourceDocument, ...]] = []
    current: list[SourceDocument] = []
    current_characters = 0
    for document in documents:
        size = len(render_document(document))
        if current and (
            len(current) >= max_documents or current_characters + size > max_characters
        ):
            packs.append(tuple(current))
            current = []
            current_characters = 0
        current.append(document)
        current_characters += size
    if current:
        packs.append(tuple(current))
    return packs


def _generation_prompt(mode: str, documents: tuple[SourceDocument, ...]) -> str:
    focus = (
        "优先创建需要联系多段记录的身份、别名、关系或时间变化问题。"
        if mode == "linked"
        else "创建 1 到 3 个可由原文明确回答、未来回忆时有价值的问题。"
    )
    return (
        f"{focus}\n"
        "不要为寒暄、表情、一次性命令创建问题。问题中使用自然姓名，不暴露内部 ID。"
        "每条 evidence.quote 必须逐字复制某一条消息中的连续文本，不得改写。"
        "若没有合格事实，cases 返回空数组。输出格式："
        '{"cases":[{"category":"direct|identity_alias|relationship|temporal|preference|project|multi_document",'
        '"question":"...","answer":"...","evidence":[{"document_id":"...","quote":"原文"}]}]}。\n\n'
        + "\n\n".join(render_document(document) for document in documents)
    )


def _spread(values: tuple[Any, ...], count: int) -> tuple[Any, ...]:
    if count >= len(values):
        return values
    if count <= 1:
        return values[:count]
    indexes = {
        round(index * (len(values) - 1) / (count - 1)) for index in range(count)
    }
    return tuple(values[index] for index in sorted(indexes))


def _validated_recall_grade(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "answer_coverage": _score(value, "answer_coverage"),
        "attribution": _score(value, "attribution"),
        "temporal": _optional_score(value, "temporal"),
        "contradiction": _boolean(value, "contradiction"),
        "reason": str(value.get("reason") or ""),
    }


def _validated_extraction_grade(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "memory_id": str(value["memory_id"]),
        "faithfulness": _score(value, "faithfulness"),
        "attribution": _score(value, "attribution"),
        "specificity": _score(value, "specificity"),
        "usefulness": _score(value, "usefulness"),
        "temporal": _optional_score(value, "temporal"),
        "unsupported_claim": _boolean(value, "unsupported_claim"),
        "overcombined": _boolean(value, "overcombined"),
        "reason": str(value.get("reason") or ""),
    }


def _score(value: dict[str, Any], name: str) -> int:
    score = value.get(name)
    if not isinstance(score, int) or isinstance(score, bool) or not 0 <= score <= 4:
        raise ValueError(f"Invalid {name} score")
    return score


def _optional_score(value: dict[str, Any], name: str) -> int | None:
    score = value.get(name)
    return None if score is None else _score(value, name)


def _boolean(value: dict[str, Any], name: str) -> bool:
    supplied = value.get(name)
    if not isinstance(supplied, bool):
        raise ValueError(f"Invalid {name}")
    return supplied


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)

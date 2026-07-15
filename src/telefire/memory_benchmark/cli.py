from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import UTC, datetime
import json
from pathlib import Path
from time import perf_counter
from typing import Any

import aiohttp

from telefire.memory_benchmark.backends import (
    MemoryRecord,
    ingest_hindsight,
    list_hindsight_memories,
    read_tencent_memories,
    recall_hindsight,
    recall_tencent,
    seed_tencent,
    wait_for_hindsight_idle,
)
from telefire.memory_benchmark.evaluation import (
    OpenAIJSONClient,
    generate_recall_cases,
    grade_extraction_batch,
    grade_recall,
    read_cases,
    render_document,
    sample_memory_records,
    semantically_validate_cases,
    write_cases,
)
from telefire.memory_benchmark.source import read_corpus, tencent_seed_payload
from telefire.memory_benchmark.reporting import write_html_report


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark chat-memory backends")
    subparsers = parser.add_subparsers(dest="command", required=True)

    hindsight = subparsers.add_parser("ingest-hindsight")
    hindsight.add_argument("--source", type=Path, required=True)
    hindsight.add_argument("--url", required=True)
    hindsight.add_argument("--bank", required=True)
    hindsight.add_argument("--name", required=True)
    hindsight.add_argument("--output", type=Path, required=True)
    hindsight.add_argument("--batch-size", type=int, default=1)
    hindsight.add_argument("--concurrency", type=int, default=4)

    wait_hindsight = subparsers.add_parser("wait-hindsight")
    wait_hindsight.add_argument("--url", required=True)
    wait_hindsight.add_argument("--bank", required=True)
    wait_hindsight.add_argument("--started-at", required=True)
    wait_hindsight.add_argument("--documents", type=int, required=True)
    wait_hindsight.add_argument("--batch-size", type=int, required=True)
    wait_hindsight.add_argument("--concurrency", type=int, required=True)
    wait_hindsight.add_argument("--output", type=Path, required=True)

    tencent = subparsers.add_parser("seed-tencent")
    tencent.add_argument("--source", type=Path, required=True)
    tencent.add_argument("--url", required=True)
    tencent.add_argument("--output", type=Path, required=True)

    cases = subparsers.add_parser("generate-cases")
    _add_llm_arguments(cases)
    cases.add_argument("--source", type=Path, required=True)
    cases.add_argument("--output", type=Path, required=True)
    cases.add_argument("--target", type=int, default=60)
    cases.add_argument("--concurrency", type=int, default=3)

    validate_cases = subparsers.add_parser("validate-cases")
    _add_llm_arguments(validate_cases)
    validate_cases.add_argument("--source", type=Path, required=True)
    validate_cases.add_argument("--cases", type=Path, required=True)
    validate_cases.add_argument("--output", type=Path, required=True)
    validate_cases.add_argument("--audit-output", type=Path, required=True)
    validate_cases.add_argument("--concurrency", type=int, default=2)

    quality = subparsers.add_parser("quality")
    _add_llm_arguments(quality)
    quality.add_argument("--source", type=Path, required=True)
    quality.add_argument("--cases", type=Path, required=True)
    quality.add_argument("--hindsight-url", required=True)
    quality.add_argument("--hindsight-bank", required=True)
    quality.add_argument("--tencent-url", required=True)
    quality.add_argument("--tencent-db", type=Path, required=True)
    quality.add_argument("--tencent-records", type=Path, required=True)
    quality.add_argument("--extraction-sample", type=int, default=160)
    quality.add_argument("--judge-concurrency", type=int, default=2)
    quality.add_argument("--output", type=Path, required=True)

    report = subparsers.add_parser("report")
    report.add_argument("--quality", type=Path, required=True)
    report.add_argument("--hindsight-ingest", type=Path, required=True)
    report.add_argument("--tencent-seed", type=Path, required=True)
    report.add_argument("--output", type=Path, required=True)

    args = parser.parse_args()
    asyncio.run(_dispatch(args))


async def _dispatch(args: argparse.Namespace) -> None:
    if args.command == "ingest-hindsight":
        corpus = read_corpus(args.source)
        result = await ingest_hindsight(
            args.url,
            args.bank,
            args.name,
            corpus,
            batch_size=args.batch_size,
            concurrency=args.concurrency,
            progress=lambda completed, total: print(
                f"hindsight documents {completed}/{total}", flush=True
            ),
        )
        _write_json(args.output, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "seed-tencent":
        corpus = read_corpus(args.source)
        result = await seed_tencent(args.url, tencent_seed_payload(corpus))
        _write_json(args.output, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "wait-hindsight":
        started_at = datetime.fromisoformat(args.started_at.replace("Z", "+00:00"))
        if started_at.tzinfo is None:
            raise ValueError("--started-at must include a timezone")
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=7_300)) as session:
            stats = await wait_for_hindsight_idle(
                session,
                args.url,
                args.bank,
                timeout_seconds=7_200,
            )
        result = {
            "backend": "hindsight-fresh",
            "elapsed_seconds": (datetime.now(UTC) - started_at.astimezone(UTC)).total_seconds(),
            "documents": args.documents,
            "batch_size": args.batch_size,
            "concurrency": args.concurrency,
            "operations": (args.documents + args.batch_size - 1) // args.batch_size,
            "stats": stats,
            "recovered_after_client_timeout": True,
        }
        _write_json(args.output, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "report":
        write_html_report(
            args.quality,
            args.hindsight_ingest,
            args.tencent_seed,
            args.output,
        )
        print(f"wrote report to {args.output}")
        return

    settings = _llm_settings(args.env_file, args.model, args.reasoning_effort)
    client = OpenAIJSONClient(**settings)
    try:
        if args.command == "generate-cases":
            corpus = read_corpus(args.source)
            generated = await generate_recall_cases(
                corpus,
                client,
                target=args.target,
                concurrency=args.concurrency,
            )
            write_cases(generated, args.output)
            print(f"wrote {len(generated)} validated cases to {args.output}")
        elif args.command == "validate-cases":
            corpus = read_corpus(args.source)
            supplied_cases = read_cases(args.cases)
            accepted, reviews = await semantically_validate_cases(
                corpus,
                supplied_cases,
                client,
                concurrency=args.concurrency,
            )
            write_cases(accepted, args.output)
            _write_json(
                args.audit_output,
                {
                    "schema": "telefire.memory-benchmark.case-validation.v1",
                    "model": client.model,
                    "input_cases": len(supplied_cases),
                    "accepted_cases": len(accepted),
                    "reviews": reviews,
                },
            )
            print(
                f"accepted {len(accepted)}/{len(supplied_cases)} cases into {args.output}"
            )
        elif args.command == "quality":
            await _run_quality(args, client)
        else:
            raise ValueError(f"Unknown command: {args.command}")
    finally:
        await client.close()


async def _run_quality(
    args: argparse.Namespace,
    client: OpenAIJSONClient,
) -> None:
    started = perf_counter()
    corpus = read_corpus(args.source)
    documents = {document.document_id: document for document in corpus.documents}
    cases = read_cases(args.cases)

    print("loading normalized memory inventories", flush=True)
    hindsight_records = await list_hindsight_memories(
        args.hindsight_url,
        args.hindsight_bank,
        backend="hindsight",
    )
    tencent_records = read_tencent_memories(
        args.tencent_db,
        records_directory=args.tencent_records,
        corpus=corpus,
    )
    records_by_backend = {
        "hindsight": hindsight_records,
        "tencent": tencent_records,
    }
    if args.output.exists():
        result = _read_json(args.output)
        if (
            result.get("schema") != "telefire.memory-benchmark.quality.v1"
            or result.get("judge_model") != client.model
            or result.get("source", {}).get("bank_id") != corpus.bank_id
        ):
            raise ValueError("Existing quality checkpoint does not match this run")
        result["inventories"] = {
            backend: _inventory(records) for backend, records in records_by_backend.items()
        }
        print(
            f"resuming quality checkpoint with {len(result.get('recall', []))} recall cases",
            flush=True,
        )
    else:
        result = {
            "schema": "telefire.memory-benchmark.quality.v1",
            "generated_at": datetime.now(UTC).isoformat(),
            "judge_model": client.model,
            "source": {
                "bank_id": corpus.bank_id,
                "documents": len(corpus.documents),
                "events": len(corpus.events),
            },
            "inventories": {
                backend: _inventory(records)
                for backend, records in records_by_backend.items()
            },
            "extraction": {},
            "recall": [],
        }
    _write_json(args.output, result)

    for backend, records in records_by_backend.items():
        if backend in result.get("extraction", {}):
            print(f"using checkpointed {backend} extraction grades", flush=True)
            continue
        linked = tuple(
            record
            for record in records
            if record.source_document_ids
            and all(document_id in documents for document_id in record.source_document_ids)
        )
        sample = sample_memory_records(linked, limit=args.extraction_sample)
        items = [_extraction_item(record, documents) for record in sample]
        batches = _item_batches(items, max_items=4, max_characters=28_000)
        semaphore = asyncio.Semaphore(args.judge_concurrency)

        async def grade(batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
            async with semaphore:
                return await grade_extraction_batch(client, batch)

        print(
            f"judging {backend} extraction: {len(sample)} memories in {len(batches)} batches",
            flush=True,
        )
        groups = await asyncio.gather(*(grade(batch) for batch in batches))
        grades = [item for group in groups for item in group]
        result["extraction"][backend] = {
            "sample_size": len(sample),
            "source_linked_population": len(linked),
            "grades": grades,
        }
        _write_json(args.output, result)

    completed_case_ids = {
        row["case"]["case_id"] for row in result.get("recall", [])
    }
    remaining_cases = [case for case in cases if case.case_id not in completed_case_ids]
    if remaining_cases:
        await recall_hindsight(
            args.hindsight_url,
            args.hindsight_bank,
            remaining_cases[0].question,
            backend="hindsight",
        )
        await recall_tencent(args.tencent_url, remaining_cases[0].question)

    for index, case in enumerate(remaining_cases, start=1):
        hindsight = await recall_hindsight(
            args.hindsight_url,
            args.hindsight_bank,
            case.question,
            backend="hindsight",
        )
        tencent = await recall_tencent(args.tencent_url, case.question)
        measurements = {
            "hindsight": hindsight,
            "tencent": tencent,
        }
        grades = await grade_recall(
            client,
            case,
            {backend: measurement.raw_context for backend, measurement in measurements.items()},
        )
        result["recall"].append(
            {
                "case": case.to_dict(),
                "measurements": {
                    backend: measurement.to_dict()
                    for backend, measurement in measurements.items()
                },
                "grades": grades,
            }
        )
        result["elapsed_seconds"] = perf_counter() - started
        _write_json(args.output, result)
        print(
            f"recall cases {len(completed_case_ids) + index}/{len(cases)}",
            flush=True,
        )

    result["elapsed_seconds"] = perf_counter() - started
    _write_json(args.output, result)
    print(f"quality evaluation complete in {result['elapsed_seconds']:.1f}s", flush=True)


def _add_llm_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--model", default="gpt-5.4-mini")
    parser.add_argument("--reasoning-effort", default="low")


def _llm_settings(
    env_file: Path,
    model: str,
    reasoning_effort: str,
) -> dict[str, str]:
    values = _read_env_file(env_file)
    return {
        "base_url": _required_env(values, "MEMORY_LLM_BASE_URL"),
        "api_key": _required_env(values, "MEMORY_LLM_API_KEY"),
        "model": model,
        "reasoning_effort": reasoning_effort,
    }


def _read_env_file(path: Path) -> dict[str, str]:
    values = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _required_env(values: dict[str, str], name: str) -> str:
    value = values.get(name)
    if not value:
        raise ValueError(f"Missing {name}")
    return value


def _inventory(records: tuple[MemoryRecord, ...]) -> dict[str, Any]:
    return {
        "total": len(records),
        "source_linked": sum(bool(record.source_document_ids) for record in records),
        "types": dict(sorted(Counter(record.memory_type for record in records).items())),
    }


def _extraction_item(
    record: MemoryRecord,
    documents: dict[str, Any],
) -> dict[str, Any]:
    return {
        "memory_id": record.memory_id,
        "memory_type": record.memory_type,
        "extracted_memory": record.text,
        "source_evidence": [
            render_document(documents[document_id])
            for document_id in record.source_document_ids
        ],
    }


def _item_batches(
    items: list[dict[str, Any]],
    *,
    max_items: int,
    max_characters: int,
) -> list[list[dict[str, Any]]]:
    batches: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_size = 0
    for item in items:
        size = len(json.dumps(item, ensure_ascii=False))
        if current and (len(current) >= max_items or current_size + size > max_characters):
            batches.append(current)
            current = []
            current_size = 0
        current.append(item)
        current_size += size
    if current:
        batches.append(current)
    return batches


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return value


if __name__ == "__main__":
    main()

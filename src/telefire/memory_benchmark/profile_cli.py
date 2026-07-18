from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
from typing import Any

from telefire.memory_benchmark.backends import (
    ingest_hindsight,
    list_hindsight_memories,
    recall_hindsight,
    verify_hindsight_bank,
)
from telefire.memory_benchmark.evaluation import (
    OpenAIJSONClient,
    grade_recall,
    read_cases,
)
from telefire.memory_benchmark.profile_benchmark import (
    INGESTION_PROFILES,
    IngestionProfile,
    ProfileBenchmarkResult,
    compare_profiles,
    corpus_event_digest,
    prepare_profile_corpus,
    profile_config,
    render_agent_context,
    sample_corpus,
    summarize_profile_benchmark,
)
from telefire.memory_benchmark.source import read_corpus, write_corpus
from telefire.memory_benchmark.telegram_source import (
    download_telegram_timeline,
    enrich_timeline_rows,
    read_timeline_rows,
    timeline_to_corpus,
    write_timeline_rows,
)


QUALITY_SCHEMA = "telefire.memory-profile-benchmark.quality.v2"
RENDERER_VERSION = "telefire-agent-memory-context-4000x50-v1"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark Hindsight ingestion profiles on one source corpus"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    export = subparsers.add_parser("export-telegram")
    export.add_argument("--channel", required=True)
    export.add_argument("--limit", type=int, default=1_000)
    export.add_argument("--account", default="default")
    export.add_argument("--session-path", type=Path)
    export.add_argument("--source-output", type=Path, required=True)
    export.add_argument("--timeline-output", type=Path, required=True)

    enrich = subparsers.add_parser("enrich-source")
    enrich.add_argument("--timeline", type=Path, required=True)
    enrich.add_argument("--retained-source", type=Path, required=True)
    enrich.add_argument("--source-output", type=Path, required=True)
    enrich.add_argument("--timeline-output", type=Path, required=True)

    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--source", type=Path, required=True)
    prepare.add_argument("--profile", choices=INGESTION_PROFILES, required=True)
    prepare.add_argument("--output", type=Path, required=True)

    sample = subparsers.add_parser("sample")
    sample.add_argument("--source", type=Path, required=True)
    sample.add_argument("--documents", type=int, required=True)
    sample.add_argument("--output", type=Path, required=True)

    ingest = subparsers.add_parser("ingest")
    ingest.add_argument("--source", type=Path, required=True)
    ingest.add_argument("--profile", choices=INGESTION_PROFILES, required=True)
    ingest.add_argument("--url", required=True)
    ingest.add_argument("--bank", required=True)
    ingest.add_argument("--name", required=True)
    ingest.add_argument("--batch-size", type=int, default=1)
    ingest.add_argument("--concurrency", type=int, default=4)
    ingest.add_argument("--output", type=Path, required=True)

    quality = subparsers.add_parser("quality")
    quality.add_argument("--source", type=Path, required=True)
    quality.add_argument("--cases", type=Path, required=True)
    quality.add_argument("--url", required=True)
    quality.add_argument(
        "--profile-bank",
        action="append",
        required=True,
        metavar="PROFILE=BANK_ID",
    )
    quality.add_argument("--env-file", type=Path, required=True)
    quality.add_argument("--model", required=True)
    quality.add_argument("--reasoning-effort", default="low")
    quality.add_argument("--output", type=Path, required=True)

    report = subparsers.add_parser("report")
    report.add_argument("--quality", type=Path, required=True)
    report.add_argument("--output", type=Path, required=True)

    asyncio.run(_dispatch(parser.parse_args()))


async def _dispatch(args: argparse.Namespace) -> None:
    if args.command == "export-telegram":
        result = await download_telegram_timeline(
            args.channel,
            limit=args.limit,
            account=args.account,
            session_path=args.session_path,
        )
        write_corpus(result.corpus, args.source_output)
        write_timeline_rows(result.rows, args.timeline_output)
        print(json.dumps(result.stats, ensure_ascii=False, indent=2))
        return

    if args.command == "prepare":
        source = read_corpus(args.source)
        prepared = prepare_profile_corpus(source, args.profile)
        write_corpus(prepared, args.output)
        print(
            json.dumps(
                {
                    "profile": args.profile,
                    "documents": len(prepared.documents),
                    "events": len(prepared.events),
                    "characters": sum(len(item.content) for item in prepared.documents),
                    "event_digest": corpus_event_digest(prepared),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if args.command == "sample":
        sampled = sample_corpus(read_corpus(args.source), documents=args.documents)
        write_corpus(sampled, args.output)
        print(
            json.dumps(
                {
                    "documents": len(sampled.documents),
                    "events": len(sampled.events),
                    "characters": sum(len(item.content) for item in sampled.documents),
                    "event_digest": corpus_event_digest(sampled),
                    "first_timestamp": sampled.documents[0].timestamp,
                    "last_timestamp": sampled.documents[-1].timestamp,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if args.command == "enrich-source":
        retained = read_corpus(args.retained_source)
        rows = enrich_timeline_rows(read_timeline_rows(args.timeline), retained)
        try:
            channel_id = int(retained.bank_id.rsplit(":", 1)[-1])
        except ValueError as exc:
            raise ValueError("Retained source does not identify a Telegram chat") from exc
        result = timeline_to_corpus(
            channel_id=channel_id,
            channel_title=retained.bank_name,
            exported_at=datetime.now(UTC).isoformat(),
            rows=rows,
        )
        write_corpus(result.corpus, args.source_output)
        write_timeline_rows(result.rows, args.timeline_output)
        print(
            json.dumps(
                {
                    **result.stats,
                    "documents": len(result.corpus.documents),
                    "event_digest": corpus_event_digest(result.corpus),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if args.command == "ingest":
        profile = _profile(args.profile)
        corpus = read_corpus(args.source)
        result = await ingest_hindsight(
            args.url,
            args.bank,
            args.name,
            corpus,
            batch_size=args.batch_size,
            concurrency=args.concurrency,
            config_updates=profile_config(profile),
            require_empty=True,
            verify_corpus=True,
            progress=lambda completed, total: print(
                f"{profile} documents {completed}/{total}", flush=True
            ),
        )
        result["profile"] = profile
        _write_json(args.output, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "quality":
        env = _read_env_file(args.env_file)
        client = OpenAIJSONClient(
            base_url=_required_env(env, "MEMORY_LLM_BASE_URL"),
            api_key=_required_env(env, "MEMORY_LLM_API_KEY"),
            model=args.model,
            reasoning_effort=args.reasoning_effort,
        )
        try:
            await _run_quality(
                source_path=args.source,
                cases_path=args.cases,
                base_url=args.url,
                profile_banks=_profile_banks(args.profile_bank),
                client=client,
                output_path=args.output,
            )
        finally:
            await client.close()
        return

    if args.command == "report":
        _write_report(args.quality, args.output)
        print(f"wrote profile report to {args.output}")
        return

    raise AssertionError(f"Unhandled command: {args.command}")


async def _run_quality(
    *,
    source_path: Path,
    cases_path: Path,
    base_url: str,
    profile_banks: dict[str, str],
    client: OpenAIJSONClient,
    output_path: Path,
) -> None:
    corpus = read_corpus(source_path)
    source_fingerprint = _source_fingerprint(corpus)
    cases = read_cases(cases_path)
    cases_fingerprint = _cases_fingerprint(cases)
    profile_names = tuple(sorted(profile_banks))
    profile_configs = {
        profile: profile_config(_profile(profile)) for profile in profile_names
    }
    inventories = {}
    bank_manifests = {}
    for profile in profile_names:
        prepared = prepare_profile_corpus(corpus, _profile(profile))
        if corpus_event_digest(prepared) != corpus_event_digest(corpus):
            raise AssertionError(f"Profile {profile} changed the source event set")
        bank_manifests[profile] = await verify_hindsight_bank(
            base_url,
            profile_banks[profile],
            prepared,
            profile_configs[profile],
        )
        records = await list_hindsight_memories(
            base_url,
            profile_banks[profile],
            backend=profile,
        )
        bank_manifests[profile]["memories"] = len(records)
        bank_manifests[profile]["memory_inventory_sha256"] = (
            _memory_inventory_fingerprint(records)
        )
        inventories[profile] = {
            "memories": len(records),
            "types": dict(sorted(Counter(record.memory_type for record in records).items())),
            "source_linked": sum(bool(record.source_document_ids) for record in records),
        }

    if output_path.exists():
        result = _read_json(output_path)
        if (
            result.get("schema") != QUALITY_SCHEMA
            or result.get("judge_model") != client.model
            or result.get("reasoning_effort") != client.reasoning_effort
            or result.get("cases_fingerprint") != cases_fingerprint
            or result.get("renderer_version") != RENDERER_VERSION
            or result.get("source", {}).get("bank_id") != corpus.bank_id
            or result.get("source", {}).get("fingerprint") != source_fingerprint
            or result.get("profile_banks") != profile_banks
            or result.get("profile_configs") != profile_configs
            or result.get("bank_manifests") != bank_manifests
        ):
            raise ValueError("Existing profile benchmark checkpoint does not match")
        result["inventories"] = inventories
        result["bank_manifests"] = bank_manifests
    else:
        result = {
            "schema": QUALITY_SCHEMA,
            "generated_at": datetime.now(UTC).isoformat(),
            "judge_model": client.model,
            "reasoning_effort": client.reasoning_effort,
            "cases_fingerprint": cases_fingerprint,
            "renderer_version": RENDERER_VERSION,
            "source": {
                "bank_id": corpus.bank_id,
                "bank_name": corpus.bank_name,
                "documents": len(corpus.documents),
                "events": len(corpus.events),
                "event_digest": corpus_event_digest(corpus),
                "fingerprint": source_fingerprint,
            },
            "profile_banks": profile_banks,
            "profile_configs": profile_configs,
            "bank_manifests": bank_manifests,
            "inventories": inventories,
            "recall": [],
        }
    _write_json(output_path, result)

    completed = {row["case"]["case_id"] for row in result["recall"]}
    remaining = [case for case in cases if case.case_id not in completed]
    if remaining:
        for profile in profile_names:
            await recall_hindsight(
                base_url,
                profile_banks[profile],
                remaining[0].question,
                backend=profile,
            )

    for index, case in enumerate(remaining, start=1):
        offset = int(case.case_id[-2:], 16) % len(profile_names)
        order = profile_names[offset:] + profile_names[:offset]
        measurements = {}
        for profile in order:
            measurements[profile] = await recall_hindsight(
                base_url,
                profile_banks[profile],
                case.question,
                backend=profile,
            )
        agent_contexts = {
            profile: render_agent_context(measurements[profile].records)
            for profile in profile_names
        }
        grades = await grade_recall(
            client,
            case,
            agent_contexts,
        )
        result["recall"].append(
            {
                "case": case.to_dict(),
                "measurements": {
                    profile: {
                        **measurements[profile].to_dict(),
                        "agent_context": agent_contexts[profile],
                    }
                    for profile in profile_names
                },
                "grades": grades,
            }
        )
        _write_json(output_path, result)
        print(
            f"profile recall cases {len(completed) + index}/{len(cases)}",
            flush=True,
        )


def _write_report(quality_path: Path, output_path: Path) -> None:
    quality = _read_json(quality_path)
    profiles = tuple(sorted(quality["profile_banks"]))
    result = ProfileBenchmarkResult(
        profiles=profiles,
        cases=tuple(quality.get("recall", [])),
    )
    summary = summarize_profile_benchmark(result)
    source = quality["source"]
    lines = [
        f"# {source['bank_name']} Ingestion Profile Retrieval Benchmark",
        "",
        f"- Corpus: {source['documents']} logical source items, "
        f"{source['events']} textual events",
        f"- Accepted recall cases: {len(result.cases)}",
        f"- Judge: `{quality['judge_model']}` / `{quality['reasoning_effort']}`",
        "- Retrieval contract: identical Hindsight recall request for every profile",
        "",
        "| Profile | Coverage /4 | Success >=3 | Attribution /4 | Contradiction | p50 | p95 | Context chars |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for profile in profiles:
        item = summary[profile]
        lines.append(
            f"| {profile} | {item['mean_coverage']:.2f} | "
            f"{item['success_rate']:.1%} | {item['mean_attribution']:.2f} | "
            f"{item['contradiction_rate']:.1%} | {item['latency_p50_ms']:.0f} ms | "
            f"{item['latency_p95_ms']:.0f} ms | {item['mean_context_chars']:.0f} |"
        )

    declared_pairs = (
        ("conversation", "atomic"),
        ("conversation", "reference"),
        ("conversation", "timeline"),
    )
    lines.extend(["", "## Exploratory Paired Comparisons", ""])
    for left, right in declared_pairs:
        if left in profiles and right in profiles:
            comparison = compare_profiles(result, left, right)
            lines.append(
                f"- `{left}` minus `{right}`: coverage "
                f"{comparison['coverage_difference']:+.2f}/4 "
                f"(95% CI {comparison['coverage_ci_low']:+.2f} to "
                f"{comparison['coverage_ci_high']:+.2f}); success "
                f"{comparison['success_difference']:+.1%} "
                f"(95% CI {comparison['success_ci_low']:+.1%} to "
                f"{comparison['success_ci_high']:+.1%}); outcomes "
                f"{comparison['left_wins']}-{comparison['right_wins']}-"
                f"{comparison['ties']} (left wins-right wins-ties)."
            )

    lines.extend(["", "## Largest Differences", ""])
    ranked = sorted(
        result.cases,
        key=lambda row: max(
            grade["answer_coverage"] for grade in row["grades"].values()
        )
        - min(grade["answer_coverage"] for grade in row["grades"].values()),
        reverse=True,
    )[:8]
    for row in ranked:
        case = row["case"]
        scores = ", ".join(
            f"{profile}={row['grades'][profile]['answer_coverage']}"
            for profile in profiles
        )
        lines.extend(
            [
                f"### {case['question']}",
                "",
                f"Reference answer: {case['answer']}",
                "",
                f"Coverage: {scores}",
                "",
            ]
        )
        for profile in profiles:
            context = str(
                row["measurements"][profile].get(
                    "agent_context",
                    row["measurements"][profile].get("raw_context"),
                )
                or ""
            )
            excerpt = " ".join(context.split())[:500]
            lines.append(f"- `{profile}`: {excerpt or '[no recalled context]'}")
        lines.append("")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")
    output_path.chmod(0o600)


def _profile(value: str) -> IngestionProfile:
    if value not in INGESTION_PROFILES:
        raise ValueError(f"Unsupported ingestion profile: {value}")
    return value  # type: ignore[return-value]


def _profile_banks(values: list[str]) -> dict[str, str]:
    result = {}
    for value in values:
        profile, separator, bank_id = value.partition("=")
        if not separator or profile not in INGESTION_PROFILES or not bank_id.strip():
            raise ValueError("--profile-bank must use PROFILE=BANK_ID")
        if profile in result:
            raise ValueError(f"Duplicate profile bank: {profile}")
        result[profile] = bank_id.strip()
    if set(result) != set(INGESTION_PROFILES):
        required = ", ".join(INGESTION_PROFILES)
        raise ValueError(f"Profile benchmark requires {required}")
    return result


def _read_env_file(path: Path) -> dict[str, str]:
    values = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        values[name.strip()] = value.strip().strip('"').strip("'")
    return values


def _required_env(values: dict[str, str], name: str) -> str:
    value = values.get(name, "").strip()
    if not value:
        raise ValueError(f"Missing {name}")
    return value


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.chmod(0o600)
    temporary.replace(path)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return value


def _source_fingerprint(corpus: Any) -> str:
    payload = json.dumps(
        corpus.to_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _cases_fingerprint(cases: Any) -> str:
    payload = json.dumps(
        [case.to_dict() for case in cases],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _memory_inventory_fingerprint(records: Any) -> str:
    payload = json.dumps(
        sorted(
            (record.to_dict() for record in records),
            key=lambda record: (record["memory_id"], record["text"]),
        ),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


if __name__ == "__main__":
    main()

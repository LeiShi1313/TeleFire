from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import perf_counter
from uuid import uuid4

import aiohttp
import pytest

import telefire.ai_memory as ai_memory_module
from telefire.ai_memory import HindsightMemoryClient
from telefire.memory_directory import DirectoryPublication, DirectorySource


HINDSIGHT_URL = os.environ.get("TELEFIRE_HINDSIGHT_URL", "").rstrip("/")

pytestmark = pytest.mark.skipif(
    not HINDSIGHT_URL,
    reason="TELEFIRE_HINDSIGHT_URL is required for the directory benchmark",
)


@dataclass(frozen=True, slots=True)
class RoutingCase:
    category: str
    query: str
    expected_key: str


@pytest.mark.asyncio
async def test_real_hindsight_directory_routing_quality_and_latency(
    monkeypatch: pytest.MonkeyPatch,
):
    suffix = uuid4().hex[:10]
    directory_bank = f"benchmark-directory-{suffix}"
    monkeypatch.setattr(
        ai_memory_module,
        "KNOWLEDGE_DIRECTORY_BANK_ID",
        directory_bank,
    )
    definitions = {
        "coder": (
            "Coder Offtopic",
            "中文技术闲聊群，也常被称为 Coder OT 群；讨论编程、本地模型和软件工程。",
            {"aliases": "Coder OT 群; Coder OT"},
        ),
        "seele": (
            "Seele Leaks",
            "原神爆料频道，tracks Genshin Impact leaks, characters, banners and game updates.",
            {"aliases": "原神爆料频道; Seele Leak"},
        ),
        "arch": (
            "Arch Linux 中文群",
            "Linux 用户交流安装、内核、显卡驱动和 Arch 软件包故障。",
            {"aliases": "Arch 群; ArchLinux Genshin"},
        ),
        "engineering": (
            "Engineering Weekly",
            "Project Aurora 的发布状态、上线风险和每周工程进度都记录在这里。",
            {"aliases": "工程周报; Aurora status source"},
        ),
        "finance": (
            "晨报",
            "财经晨报，覆盖股票、利率、汇率和宏观经济。",
            {"aliases": "Finance Morning Brief"},
        ),
        "games": (
            "晨报",
            "游戏晨报，覆盖新作发售、电竞和主机更新。",
            {"aliases": "Gaming Morning Brief"},
        ),
    }
    bank_ids = {key: f"benchmark:source:{key}-{suffix}" for key in definitions}
    cases = (
        RoutingCase("exact_name", "Coder Offtopic 最近在讨论什么？", "coder"),
        RoutingCase("colloquial_name", "Coder OT 群是什么信息源？", "coder"),
        RoutingCase("description_only", "哪个来源讨论 Linux 内核和显卡驱动？", "arch"),
        RoutingCase(
            "multilingual", "Which source tracks Genshin character leaks?", "seele"
        ),
        RoutingCase("collision", "哪个晨报关注股票和宏观经济？", "finance"),
        RoutingCase("distractors", "哪里记录 Arch 软件包安装故障？", "arch"),
        RoutingCase(
            "recursive_discovery", "Aurora 项目的发布状态应该去哪里查？", "engineering"
        ),
    )
    client = HindsightMemoryClient(HINDSIGHT_URL, timeout=300)
    async with aiohttp.ClientSession() as session:
        try:
            started_at = datetime.now(UTC)
            for index, (key, (name, description, attributes)) in enumerate(
                definitions.items()
            ):
                await client.publish_directory(
                    DirectoryPublication(
                        publication_id=f"benchmark:message:{suffix}:{index}",
                        publisher_id="benchmark:user:owner",
                        published_at=started_at + timedelta(seconds=index),
                        source=DirectorySource(
                            bank_id=bank_ids[key],
                            display_name=name,
                            platform="benchmark",
                            source_kind="chat",
                            attributes=attributes,
                        ),
                        description=description,
                    )
                )

            rows: list[dict[str, object]] = []
            for case in cases:
                started = perf_counter()
                recalled = await client.recall_directory(query=case.query)
                elapsed_ms = round((perf_counter() - started) * 1_000, 1)
                returned = [reference.bank_id for reference in recalled.references]
                expected = bank_ids[case.expected_key]
                rows.append(
                    {
                        "category": case.category,
                        "query": case.query,
                        "expected": expected,
                        "returned": returned,
                        "hit": expected in returned,
                        "rank": returned.index(expected) + 1
                        if expected in returned
                        else None,
                        "top1_hit": bool(returned and returned[0] == expected),
                        "candidate_count": len(returned),
                        "elapsed_ms": elapsed_ms,
                    }
                )

            authorization = await client.recall_directory(
                query="Coder OT 群和原神爆料频道最近有什么？",
                allowed_bank_ids=(bank_ids["seele"],),
            )
            authorized_ids = {
                reference.bank_id for reference in authorization.references
            }
            assert authorized_ids <= {bank_ids["seele"]}

            hits = sum(bool(row["hit"]) for row in rows)
            top1_hits = sum(bool(row["top1_hit"]) for row in rows)
            latencies = sorted(float(row["elapsed_ms"]) for row in rows)
            p50 = latencies[len(latencies) // 2]
            p95 = latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))]
            report = {
                "schema": "telefire.knowledge-directory-benchmark.v1",
                "cases": rows,
                "summary": {
                    "hits": hits,
                    "total": len(rows),
                    "hit_rate": round(hits / len(rows), 3),
                    "top1_hits": top1_hits,
                    "top1_accuracy": round(top1_hits / len(rows), 3),
                    "mean_candidates": round(
                        sum(int(row["candidate_count"]) for row in rows) / len(rows),
                        2,
                    ),
                    "p50_ms": p50,
                    "p95_ms": p95,
                    "authorization_filter": "pass",
                },
            }
            print(json.dumps(report, ensure_ascii=False, indent=2))

            assert hits >= 6, report
            assert top1_hits >= 6, report
            assert p95 < 60_000, report
        finally:
            await client.close()
            for bank_id in (directory_bank, *bank_ids.values()):
                async with session.delete(
                    f"{HINDSIGHT_URL}/v1/default/banks/{bank_id}"
                ):
                    pass

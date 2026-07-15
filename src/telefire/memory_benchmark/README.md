# Memory Backend Benchmark

This package compares chat-memory backends using the same source episodes,
source-grounded recall questions, extraction-quality grading, and measured
retrieval latency. It is an offline evaluation tool; importing it does not
change Telefire's runtime memory behavior.

The current adapters cover:

- Hindsight ingestion, inventory, provenance, and recall
- TencentDB Agent Memory seeding, L1 inventory, provenance, and recall
- OpenAI-compatible generation and judging
- resumable quality evaluation and a self-contained HTML report

## Privacy

An exported corpus contains message text, actor names, canonical actor IDs,
timestamps, and reply metadata. Generated cases, judge output, and the HTML
report can also contain excerpts from those messages.

Use `.benchmark-data/` for local artifacts. The repository ignores this
directory, but benchmark artifacts must still not be uploaded, pasted into a
pull request, or served on a public interface. Review a report before sharing
it because the lowest-scoring examples include source evidence.

## Requirements

- Python 3.14 and the repository's `uv` environment
- a Hindsight API containing source Episode documents
- fresh Hindsight and TencentDB Agent Memory stores for each comparison
- a Tencent gateway exposing the HTTP contract described below
- an OpenAI-compatible API for case generation and judging
- an embedding model configured identically for the compared vector stores

Install the Python environment and inspect the commands:

```bash
uv sync
uv run python -m telefire.memory_benchmark.cli --help
```

The harness does not start either vendor backend. Keep benchmark stores
isolated from production so ingestion and cleanup cannot alter live memory.

## LLM Configuration

Commands that generate or judge cases take an env file containing:

```dotenv
MEMORY_LLM_BASE_URL=https://openai-compatible.example/v1
MEMORY_LLM_API_KEY=replace-me
```

Pass the model and reasoning effort explicitly when reproducibility matters:

```bash
--env-file .env --model gpt-5.4-mini --reasoning-effort low
```

Do not commit the env file. API credentials are read at runtime and are not
written into benchmark output.

## Tencent Gateway Contract

The Tencent adapter expects a local gateway with two endpoints:

- `POST /seed` accepts `data`, `strict_round_role`, and
  `auto_fill_timestamps`, then returns a JSON object with seed timings and
  output paths.
- `POST /search/memories` accepts `query` and `limit`, then returns a JSON
  object whose `results` field is Tencent's formatted memory-context string.

Quality evaluation also reads Tencent's SQLite database and extraction JSONL
records directly. Pass both paths with `--tencent-db` and
`--tencent-records`. The database is opened read-only.

Keep the Tencent extraction policy fixed for a run. In particular, record the
L1 extraction interval, retrieval mode, result limit, embedding model, and
whether L2/L3 processing is enabled.

## Workflow

The examples below use one private working directory:

```bash
mkdir -p .benchmark-data
```

### 1. Export the source corpus

The exporter accepts Hindsight documents using Telefire's
`telefire.memory.episode.v1` schema and writes a backend-neutral v1 corpus.

```bash
export HINDSIGHT_URL=http://127.0.0.1:8888
export SOURCE_BANK='telegram:chat:example'
export SOURCE_BANK_NAME='Example chat'

uv run python - <<'PY'
import asyncio
import os
from pathlib import Path

from telefire.memory_benchmark.source import export_hindsight_bank, write_corpus

corpus = asyncio.run(
    export_hindsight_bank(
        os.environ["HINDSIGHT_URL"],
        os.environ["SOURCE_BANK"],
        os.environ["SOURCE_BANK_NAME"],
    )
)
write_corpus(corpus, Path(".benchmark-data/source.json"))
print(f"exported {len(corpus.documents)} episodes and {len(corpus.events)} events")
PY
```

The corpus preserves episode boundaries, chronological events, display names,
canonical actor IDs, timestamps, reply relationships, and source hashes.

### 2. Ingest fresh stores

Hindsight receives one retain item per source Episode. The default concurrency
of four mirrors Telefire's dream ingestion path.

```bash
uv run python -m telefire.memory_benchmark.cli ingest-hindsight \
  --source .benchmark-data/source.json \
  --url http://127.0.0.1:18889 \
  --bank benchmark:example:run-1 \
  --name 'Example benchmark run 1' \
  --batch-size 1 \
  --concurrency 4 \
  --output .benchmark-data/hindsight-ingest.json
```

If the client stops while Hindsight is still consolidating, recover the timing
record without re-ingesting:

```bash
uv run python -m telefire.memory_benchmark.cli wait-hindsight \
  --url http://127.0.0.1:18889 \
  --bank benchmark:example:run-1 \
  --started-at 2026-01-01T12:00:00Z \
  --documents 100 \
  --batch-size 1 \
  --concurrency 4 \
  --output .benchmark-data/hindsight-ingest.json
```

Seed Tencent with the same source Episode sequence:

```bash
uv run python -m telefire.memory_benchmark.cli seed-tencent \
  --source .benchmark-data/source.json \
  --url http://127.0.0.1:18420 \
  --output .benchmark-data/tencent-seed.json
```

Do not silently patch backend-specific ingestion failures. Record dropped
source episodes, pending extraction tails, and vendor wait time in the result.

### 3. Generate and audit recall cases

Generate questions from source messages rather than from extracted memories:

```bash
uv run python -m telefire.memory_benchmark.cli generate-cases \
  --env-file .env \
  --model gpt-5.4-mini \
  --reasoning-effort low \
  --source .benchmark-data/source.json \
  --target 60 \
  --concurrency 3 \
  --output .benchmark-data/recall-cases.json
```

Then use an independent judge, preferably a different or stronger model, to
reject ambiguous, unsupported, or misattributed questions:

```bash
uv run python -m telefire.memory_benchmark.cli validate-cases \
  --env-file .env \
  --model gpt-5.6-sol \
  --reasoning-effort low \
  --source .benchmark-data/source.json \
  --cases .benchmark-data/recall-cases.json \
  --output .benchmark-data/recall-cases-validated.json \
  --audit-output .benchmark-data/recall-case-validation.json \
  --concurrency 2
```

Every generated evidence quote is also checked as an exact substring of its
cited source event before a case is accepted.

### 4. Measure extraction, recall, and latency

```bash
uv run python -m telefire.memory_benchmark.cli quality \
  --env-file .env \
  --model gpt-5.4-mini \
  --reasoning-effort low \
  --source .benchmark-data/source.json \
  --cases .benchmark-data/recall-cases-validated.json \
  --hindsight-url http://127.0.0.1:18889 \
  --hindsight-bank benchmark:example:run-1 \
  --tencent-url http://127.0.0.1:18420 \
  --tencent-db .benchmark-data/tencent-output/vectors.db \
  --tencent-records .benchmark-data/tencent-output/records \
  --extraction-sample 160 \
  --judge-concurrency 2 \
  --output .benchmark-data/quality.json
```

The quality command checkpoints completed extraction batches and recall cases
in its output file. Re-running the same command resumes incomplete judging.
Use fresh stores or preserve them unchanged when resuming.

### 5. Build the report

```bash
uv run python -m telefire.memory_benchmark.cli report \
  --quality .benchmark-data/quality.json \
  --hindsight-ingest .benchmark-data/hindsight-ingest.json \
  --tencent-seed .benchmark-data/tencent-seed.json \
  --output .benchmark-data/report.html
```

Open the HTML locally. It is self-contained and does not need a web server.

## Metrics

The report separates three concerns:

- Extraction quality: faithfulness, attribution, specificity, usefulness,
  unsupported claims, over-combination, and source-link coverage.
- Recall quality: answer coverage, attribution, contradiction rate, paired
  wins, success rate at coverage score 3 or higher, and category breakdowns.
- Performance: ingestion wall time and throughput plus warm-query p50 and p95
  recall latency.

Extraction grading uses a deterministic type-balanced sample of source-linked
memories. Recall uses the same accepted questions for both backends. Paired
bootstrap intervals quantify the observed recall advantage. Treat categories
with very small sample sizes as examples, not independent conclusions.

## Reference Run

The 2026-07-15 private Telegram group run used 376 Episodes, 2,375 events, and
51 independently accepted recall cases. It compared Hindsight 0.8.4 with
`@tencentdb-agent-memory/memory-tencentdb` 0.3.6, using the same 1,024-dimension
Qwen embedding space and OpenAI-compatible extraction model. In that
configuration:

| Metric | Hindsight | Tencent |
| --- | ---: | ---: |
| Recall success, coverage >= 3 | 82.4% | 33.3% |
| Mean recall coverage, 0-4 | 3.33 | 1.57 |
| Extraction faithfulness, 0-4 | 3.79 | 3.54 |
| Unsupported extracted claims | 6.9% | 18.9% |
| Recall latency p50 | 316 ms | 170 ms |
| Recall latency p95 | 415 ms | 189 ms |

Hindsight was the better fit for Telefire's recall-first chat experience in
this run. Tencent retrieved faster and had slightly stronger extraction
attribution, but its recall deficit was material. Its strict incremental cursor
also dropped 13 second-arriving Episodes from same-millisecond timestamp pairs.

This is evidence for one corpus and one configuration, not a universal backend
ranking. Re-run the benchmark after changing extraction prompts, chunking,
embedding models, retrieval limits, or memory-layer versions.

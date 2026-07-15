from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from html import escape
import json
import math
from pathlib import Path
from statistics import mean
from typing import Any


def summarize_quality(quality: dict[str, Any]) -> dict[str, dict[str, Any]]:
    backends = sorted(quality.get("inventories", {}))
    summary: dict[str, dict[str, Any]] = {}
    for backend in backends:
        inventory = quality["inventories"][backend]
        extraction = quality.get("extraction", {}).get(backend, {})
        extraction_grades = extraction.get("grades", [])
        recall_rows = [
            row for row in quality.get("recall", []) if backend in row.get("grades", {})
        ]
        recall_grades = [row["grades"][backend] for row in recall_rows]
        latency = [row["measurements"][backend]["elapsed_ms"] for row in recall_rows]
        recalled_records = [
            len(row["measurements"][backend].get("records", [])) for row in recall_rows
        ]
        context_characters = [
            len(row["measurements"][backend].get("raw_context", ""))
            for row in recall_rows
        ]
        total = inventory.get("total", 0)
        summary[backend] = {
            "memories": total,
            "types": inventory.get("types", {}),
            "source_link_rate": inventory.get("source_linked", 0) / total if total else 0.0,
            "extraction_sample": len(extraction_grades),
            "faithfulness": _average(extraction_grades, "faithfulness"),
            "extraction_attribution": _average(extraction_grades, "attribution"),
            "specificity": _average(extraction_grades, "specificity"),
            "usefulness": _average(extraction_grades, "usefulness"),
            "unsupported_rate": _boolean_rate(extraction_grades, "unsupported_claim"),
            "overcombined_rate": _boolean_rate(extraction_grades, "overcombined"),
            "recall_cases": len(recall_grades),
            "recall_coverage": _average(recall_grades, "answer_coverage"),
            "recall_attribution": _average(recall_grades, "attribution"),
            "recall_success_rate": (
                sum(grade["answer_coverage"] >= 3 for grade in recall_grades)
                / len(recall_grades)
                if recall_grades
                else 0.0
            ),
            "contradiction_rate": _boolean_rate(recall_grades, "contradiction"),
            "latency_p50_ms": _percentile(latency, 0.50),
            "latency_p95_ms": _percentile(latency, 0.95),
            "average_recalled_records": mean(recalled_records) if recalled_records else 0.0,
            "average_context_characters": mean(context_characters) if context_characters else 0.0,
        }
    return summary


def write_html_report(
    quality_path: Path,
    hindsight_ingest_path: Path,
    tencent_seed_path: Path,
    output: Path,
) -> None:
    quality = _read_json(quality_path)
    hindsight_ingest = _read_json(hindsight_ingest_path)
    tencent_seed = _read_json(tencent_seed_path)
    summary = summarize_quality(quality)
    by_category = _category_summary(quality)
    comparison = _recall_comparison(quality)
    tencent_layers = _tencent_layers(tencent_seed)
    generated_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

    backend_rows = "".join(
        _backend_row(backend, values) for backend, values in summary.items()
    )
    category_rows = "".join(
        _category_row(category, values) for category, values in by_category.items()
    )
    failure_sections = "".join(
        _failure_section(backend, quality) for backend in summary
    )
    source = quality.get("source", {})
    judge_model = escape(str(quality.get("judge_model", "unknown")))
    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Memory backend benchmark</title>
<style>
:root {{ color-scheme: light; --ink:#1d2329; --muted:#687078; --line:#d9dde1; --paper:#f7f8f8; --good:#087f5b; --warn:#b35c00; --bad:#b42318; --accent:#315c8c; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; color:var(--ink); background:var(--paper); font:15px/1.5 Inter,system-ui,sans-serif; letter-spacing:0; }}
header {{ background:#fff; border-bottom:1px solid var(--line); padding:32px max(24px,calc((100vw - 1180px)/2)); }}
h1 {{ margin:0 0 8px; font-size:32px; letter-spacing:0; }}
h2 {{ margin:0 0 14px; font-size:20px; letter-spacing:0; }}
h3 {{ font-size:16px; letter-spacing:0; }}
p {{ margin:6px 0; }}
main {{ max-width:1180px; margin:0 auto; padding:28px 24px 56px; }}
section {{ margin:0 0 34px; }}
.meta {{ color:var(--muted); }}
.metrics {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; margin-top:20px; }}
.metric {{ background:#fff; border:1px solid var(--line); border-radius:6px; padding:14px; min-width:0; }}
.metric strong {{ display:block; font-size:24px; }}
.metric span {{ color:var(--muted); font-size:13px; }}
.table-wrap {{ overflow:auto; border:1px solid var(--line); border-radius:6px; background:#fff; }}
table {{ border-collapse:collapse; width:100%; min-width:880px; }}
th,td {{ padding:11px 12px; border-bottom:1px solid var(--line); text-align:right; vertical-align:top; }}
th:first-child,td:first-child {{ text-align:left; }}
th {{ color:#4f5962; background:#f0f2f3; font-size:12px; text-transform:uppercase; }}
tr:last-child td {{ border-bottom:0; }}
.note {{ border-left:3px solid var(--accent); background:#fff; padding:12px 14px; }}
.good {{ color:var(--good); }} .warn {{ color:var(--warn); }} .bad {{ color:var(--bad); }}
details {{ background:#fff; border:1px solid var(--line); border-radius:6px; margin:8px 0; padding:10px 12px; }}
summary {{ cursor:pointer; font-weight:650; }}
.case {{ border-top:1px solid var(--line); padding:12px 0; }}
.case:first-of-type {{ margin-top:10px; }}
blockquote {{ margin:8px 0; padding:8px 12px; background:#f2f4f5; border-left:3px solid #89939c; white-space:pre-wrap; }}
code {{ overflow-wrap:anywhere; }}
@media(max-width:760px) {{ .metrics {{ grid-template-columns:repeat(2,minmax(0,1fr)); }} h1 {{ font-size:25px; }} main {{ padding-inline:14px; }} header {{ padding-inline:14px; }} }}
</style>
</head>
<body>
<header>
  <h1>Hindsight vs TencentDB Agent Memory</h1>
  <p class="meta">Coder Offtopic benchmark · {generated_at} · independent judge: {judge_model}</p>
  <div class="metrics">
    <div class="metric"><strong>{source.get('documents', 0)}</strong><span>source episodes</span></div>
    <div class="metric"><strong>{source.get('events', 0)}</strong><span>source messages</span></div>
    <div class="metric"><strong>{len(quality.get('recall', []))}</strong><span>source-grounded questions</span></div>
    <div class="metric"><strong>{comparison['winner']}</strong><span>higher recall coverage</span></div>
  </div>
</header>
<main>
<section>
  <h2>Executive comparison</h2>
  <div class="table-wrap"><table>
    <thead><tr><th>Backend</th><th>Recall /4</th><th>Success ≥3</th><th>Recall attribution /4</th><th>Contradiction</th><th>Faithfulness /4</th><th>Extraction attribution /4</th><th>Unsupported</th><th>P50</th><th>P95</th></tr></thead>
    <tbody>{backend_rows}</tbody>
  </table></div>
  <p class="meta">Recall wins: Hindsight {comparison['hindsight_wins']}, Tencent {comparison['tencent_wins']}, ties {comparison['ties']}.</p>
</section>
<section>
  <h2>Recall by question type</h2>
  <div class="table-wrap"><table>
    <thead><tr><th>Category</th><th>Cases</th><th>Hindsight /4</th><th>Tencent /4</th><th>Hindsight success</th><th>Tencent success</th></tr></thead>
    <tbody>{category_rows}</tbody>
  </table></div>
</section>
<section>
  <h2>Extraction inventory</h2>
  <div class="table-wrap"><table>
    <thead><tr><th>Backend</th><th>Memories</th><th>Source-linked</th><th>Judged sample</th><th>Specificity /4</th><th>Usefulness /4</th><th>Over-combined</th><th>Types</th></tr></thead>
    <tbody>{''.join(_inventory_row(k,v) for k,v in summary.items())}</tbody>
  </table></div>
  <p class="note"><strong>Tencent higher layers are reported separately:</strong> {tencent_layers['scene_blocks']} scene blocks and persona size {tencent_layers['persona_chars']} characters. They are not mixed into L1 fact scores because they do not preserve equivalent per-memory provenance.</p>
</section>
<section>
  <h2>Retrieval footprint and latency</h2>
  <div class="table-wrap"><table>
    <thead><tr><th>Backend</th><th>Average recalled records</th><th>Average context characters</th><th>P50 latency</th><th>P95 latency</th></tr></thead>
    <tbody>{''.join(_footprint_row(k,v) for k,v in summary.items())}</tbody>
  </table></div>
  <p class="meta">Context size is reported alongside recall quality because a backend returning more evidence has a larger opportunity to contain the answer.</p>
</section>
<section>
  <h2>Ingestion throughput</h2>
  <div class="table-wrap"><table>
    <thead><tr><th>Backend</th><th>Input episodes</th><th>Wall time</th><th>Episodes / minute</th><th>Notes</th></tr></thead>
    <tbody>
      {_ingest_row('Hindsight', hindsight_ingest.get('documents', 0), hindsight_ingest.get('elapsed_seconds', 0), f"Synchronous retain, batch size {hindsight_ingest.get('batch_size', 'unknown')}, concurrency {hindsight_ingest.get('concurrency', 'unknown')}")}
      {_ingest_row('Tencent', _tencent_rounds(tencent_seed), tencent_seed.get('elapsed_seconds_client', 0), 'One round per source episode; L1 every 5 rounds')}
    </tbody>
  </table></div>
</section>
<section>
  <h2>Lowest-scoring recall cases</h2>
  {failure_sections}
</section>
<section>
  <h2>Method and limits</h2>
  <p>Both fresh stores received the same 376 exported Episode documents, preserving message text, timestamps, actor display names, canonical actor IDs, reply metadata, and source boundaries. Hindsight received one retain item per Episode. Tencent received one session with one conversation round per Episode so its configured five-round extraction window produced roughly one extraction call per five source documents.</p>
  <p>The 60 Chinese questions were generated from raw source messages, not from either memory backend. Every evidence quote was programmatically verified as a verbatim substring of its cited message. The judge saw the reference answer, source evidence, and anonymized retrieved contexts.</p>
  <p>Extraction quality uses a deterministic, type-balanced sample of source-linked memories. Unlinked memories affect provenance coverage but are not assigned an unsupported score without evidence. Results describe one Telegram group and one model/configuration snapshot; they are comparative evidence, not a universal ranking.</p>
</section>
</main>
</body>
</html>"""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")


def _category_summary(quality: dict[str, Any]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in quality.get("recall", []):
        grouped[row["case"]["category"]].append(row)
    result = {}
    for category, rows in sorted(grouped.items()):
        result[category] = {"cases": len(rows)}
        for backend in ("hindsight", "tencent"):
            scores = [row["grades"][backend]["answer_coverage"] for row in rows]
            result[category][backend] = mean(scores)
            result[category][f"{backend}_success"] = sum(score >= 3 for score in scores) / len(scores)
    return result


def _recall_comparison(quality: dict[str, Any]) -> dict[str, Any]:
    hindsight_wins = tencent_wins = ties = 0
    for row in quality.get("recall", []):
        hindsight = row["grades"]["hindsight"]["answer_coverage"]
        tencent = row["grades"]["tencent"]["answer_coverage"]
        if hindsight > tencent:
            hindsight_wins += 1
        elif tencent > hindsight:
            tencent_wins += 1
        else:
            ties += 1
    winner = (
        "Hindsight"
        if hindsight_wins > tencent_wins
        else "Tencent"
        if tencent_wins > hindsight_wins
        else "Tie"
    )
    return {
        "hindsight_wins": hindsight_wins,
        "tencent_wins": tencent_wins,
        "ties": ties,
        "winner": winner,
    }


def _backend_row(backend: str, values: dict[str, Any]) -> str:
    return (
        f"<tr><td><strong>{escape(backend.title())}</strong></td>"
        f"<td>{_number(values['recall_coverage'])}</td>"
        f"<td>{_percent(values['recall_success_rate'])}</td>"
        f"<td>{_number(values['recall_attribution'])}</td>"
        f"<td>{_percent(values['contradiction_rate'])}</td>"
        f"<td>{_number(values['faithfulness'])}</td>"
        f"<td>{_number(values['extraction_attribution'])}</td>"
        f"<td>{_percent(values['unsupported_rate'])}</td>"
        f"<td>{values['latency_p50_ms']:.0f} ms</td>"
        f"<td>{values['latency_p95_ms']:.0f} ms</td></tr>"
    )


def _category_row(category: str, values: dict[str, Any]) -> str:
    return (
        f"<tr><td>{escape(category.replace('_', ' '))}</td><td>{values['cases']}</td>"
        f"<td>{values['hindsight']:.2f}</td><td>{values['tencent']:.2f}</td>"
        f"<td>{_percent(values['hindsight_success'])}</td>"
        f"<td>{_percent(values['tencent_success'])}</td></tr>"
    )


def _inventory_row(backend: str, values: dict[str, Any]) -> str:
    types = ", ".join(f"{escape(str(k))}: {v}" for k, v in values["types"].items())
    return (
        f"<tr><td><strong>{escape(backend.title())}</strong></td>"
        f"<td>{values['memories']}</td><td>{_percent(values['source_link_rate'])}</td>"
        f"<td>{values['extraction_sample']}</td><td>{_number(values['specificity'])}</td>"
        f"<td>{_number(values['usefulness'])}</td><td>{_percent(values['overcombined_rate'])}</td>"
        f"<td>{types}</td></tr>"
    )


def _footprint_row(backend: str, values: dict[str, Any]) -> str:
    return (
        f"<tr><td><strong>{escape(backend.title())}</strong></td>"
        f"<td>{values['average_recalled_records']:.1f}</td>"
        f"<td>{values['average_context_characters']:.0f}</td>"
        f"<td>{values['latency_p50_ms']:.0f} ms</td>"
        f"<td>{values['latency_p95_ms']:.0f} ms</td></tr>"
    )


def _ingest_row(name: str, documents: int, seconds: float, note: str) -> str:
    rate = documents / seconds * 60 if seconds else 0
    return (
        f"<tr><td><strong>{escape(name)}</strong></td><td>{documents}</td>"
        f"<td>{seconds / 60:.1f} min</td><td>{rate:.1f}</td><td>{escape(note)}</td></tr>"
    )


def _failure_section(backend: str, quality: dict[str, Any]) -> str:
    rows = sorted(
        quality.get("recall", []),
        key=lambda row: (
            row["grades"][backend]["answer_coverage"],
            row["case"]["case_id"],
        ),
    )[:8]
    cases = "".join(
        "<div class=\"case\">"
        f"<strong>{escape(row['case']['question'])}</strong>"
        f"<p>Reference: {escape(row['case']['answer'])}</p>"
        f"<p>Score: {row['grades'][backend]['answer_coverage']}/4 · "
        f"{escape(row['grades'][backend]['reason'])}</p>"
        f"<blockquote>{escape(row['measurements'][backend]['raw_context'][:2400])}</blockquote>"
        "</div>"
        for row in rows
    )
    return f"<details><summary>{escape(backend.title())}</summary>{cases}</details>"


def _tencent_layers(seed: dict[str, Any]) -> dict[str, int]:
    output_path = seed.get("outputPath") or seed.get("output_path")
    if not isinstance(output_path, str):
        return {"scene_blocks": 0, "persona_chars": 0}
    root = Path(output_path)
    persona = root / "persona.md"
    return {
        "scene_blocks": len(list((root / "scene_blocks").glob("*.md"))),
        "persona_chars": len(persona.read_text(encoding="utf-8")) if persona.exists() else 0,
    }


def _tencent_rounds(seed: dict[str, Any]) -> int:
    summary = seed.get("summary") or seed.get("seed") or {}
    if isinstance(summary, dict):
        for key in ("rounds", "conversations"):
            value = summary.get(key)
            if isinstance(value, int):
                return value
    output_path = seed.get("outputPath") or seed.get("output_path")
    if isinstance(output_path, str):
        manifest = Path(output_path) / ".metadata" / "manifest.json"
        if manifest.exists():
            value = _read_json(manifest).get("seed", {}).get("rounds")
            if isinstance(value, int):
                return value
    return 0


def _average(rows: list[dict[str, Any]], name: str) -> float:
    values = [row[name] for row in rows if isinstance(row.get(name), (int, float))]
    return mean(values) if values else 0.0


def _boolean_rate(rows: list[dict[str, Any]], name: str) -> float:
    return sum(bool(row.get(name)) for row in rows) / len(rows) if rows else 0.0


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(quantile * len(ordered)) - 1)
    return float(ordered[index])


def _number(value: float) -> str:
    return f"{value:.2f}"


def _percent(value: float) -> str:
    return f"{value * 100:.1f}%"


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return value

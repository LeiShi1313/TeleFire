# Seele Leaks memory materialization profile benchmark

**Date:** 2026-07-18  
**Status:** Closed as a product no-op  
**Implementation experiment:** [`experiment/seele-profile-benchmark`](https://github.com/LeiShi1313/TeleFire/tree/experiment/seele-profile-benchmark) at `f87d552`

## Outcome

Telefire will not introduce selectable conversation, timeline, or reference bank
profiles from this experiment. The production memory and agent behavior remains
unchanged.

The experiment produced one useful negative decision and one useful design
direction:

1. A timeline or reference source must not imply raw/verbatim-only memory.
   Concise, source-linked fact extraction materially improved retrieval even for
   an informational Telegram channel.
2. This run did not establish that conversation grouping and observations are
   better than retaining original post boundaries with concise extraction.
   Those two concise variants were statistically tied on the scored sample.

If bank profiles are revisited, the first candidate should preserve boundaries
appropriate to the source while retaining concise fact extraction. Profiles
must be immutable, versioned materialization policies rather than mutable labels.

## Question

The proposed product model had three bank types:

- Conversation banks combine reply chains and nearby standalone messages, then
  extract concise memories and consolidate observations.
- Timeline banks preserve chronological source items and avoid conversation
  distillation.
- Reference banks preserve document or post chunks and avoid conversation
  distillation.

The open question was whether profile choice could affect ingestion only while
all banks continued to use the same recall API, and whether an informational
timeline such as Seele Leaks would retrieve better under a raw timeline or
reference representation.

The public recall API can remain uniform, but the premise that retrieval is
therefore equivalent is false. Profile choice changes the memory units competing
inside Hindsight's fixed recall and agent context budgets. It affects retrieval
indirectly even when the request body is identical.

## Independent reviews

Three independent reviews covered architecture, benchmark methodology, and the
finished harness.

The architecture review required:

- Store a trusted, immutable profile and schema version such as
  `conversation@1`, `timeline@1`, or `reference@1`.
- Treat profile changes as explicit rebuilds. Updating extraction settings in
  place can mix old and new memory semantics.
- Include the profile and effective configuration fingerprint in ingestion
  receipts so unchanged source documents are not incorrectly treated as current.
- Use one deterministic segmenter for every capture path that writes a given
  bank. `/ai`, `/ai_memory`, continuous capture, Dream, and backfill must
  converge on the same source-to-document ownership.
- Keep authorization, publication, and cross-bank grants independent of profile
  choice.
- Give the Knowledge Directory a locked internal policy such as `directory@1`;
  it is not a generic conversation, timeline, or reference bank.
- Treat the current primary-bank-only `memory_reflect` behavior as a separate
  cross-bank capability question. The profile experiment does not resolve it.

The methodology review required an atomic source ledger, one frozen attachment
description per source message, identical source-event multisets across arms,
the exact production recall request, the exact 4,000-character agent-visible
context, anonymized grading, paired comparisons, and explicit limitations.

The final harness review found and caused fixes for four issues:

- Benchmark banks now refuse non-empty document or derived-memory state.
- Every scored checkpoint verifies exact live document IDs and content hashes,
  the complete derived-memory inventory hash, and effective Hindsight profile
  overrides.
- Every Telegram ledger row carries its full platform/chat/source identity, and
  the JSONL manifest protects the complete row set with a digest. Enrichment
  rejects a different chat instead of joining by chat-local message ID alone.
- Telegram sessions and private benchmark artifacts use mode `0600`, explicit
  session paths have path semantics, and confidence intervals are labeled
  exploratory rather than winner-confirming.

## Frozen test base

An authenticated Telegram user session downloaded 1,000 consecutive recent
messages from Seele Leaks spanning 2026-07-01 through 2026-07-18.

| Item | Count |
| --- | ---: |
| Downloaded Telegram messages | 1,000 |
| Messages containing media | 866 |
| Media messages without a raw caption | 621 |
| Enriched textual events | 999 |
| Logical posts after media-album grouping | 630 |
| Enriched source characters | 370,811 |

Raw image and file binaries were not retained in the benchmark corpus. Existing
source-linked attachment descriptions were frozen once and reused by every
profile, so no arm received a different multimodal interpretation.

The full enriched event digest was:

```text
2f7ee4ce38be73196d756230660a787630d985d6843d42e9d99fed72db831a81
```

Full concise extraction for four arms would have taken tens of minutes per arm.
The scored run therefore used a deterministic time-spread sample covering the
same date range:

| Item | Count |
| --- | ---: |
| Logical source items | 120 |
| Source events | 198 |
| Source characters | 70,417 |

The scored event digest, identical in all four projections, was:

```text
8e891f0779d5e1f6bd388830dfc813c7c746e818866ab2e612eac24b985dc4e9
```

Private source ledgers, authenticated sessions, recalled contexts, and judge
outputs remain ignored local artifacts. They were not committed or pushed.

## Compared materializations

The fourth `atomic` arm was an ablation, not a proposed public bank type. It
isolated concise fact extraction from conversation grouping and observation
consolidation.

| Profile | Source boundaries | Hindsight extraction | Observations |
| --- | --- | --- | --- |
| `conversation` | Reply-aware, time-bounded sessions | `concise` | Enabled |
| `atomic` | Original post or media album | `concise` | Disabled |
| `timeline` | Original post or media album | `verbatim` | Disabled |
| `reference` | Original post or media album | `chunks` | Disabled |

Every projection contained the exact same source events. Conversation projection
created 74 session documents; each other projection retained 120 source items.

## Evaluation contract

Question generation used `gpt-5.6-luna` with low reasoning. A separate
`gpt-5.6-sol` validation pass accepted 33 of 40 generated questions. The scored
set contained:

| Category | Cases |
| --- | ---: |
| Direct detail | 19 |
| Project | 9 |
| Relationship | 2 |
| Temporal | 2 |
| Identity or alias | 1 |

Each profile received the same Hindsight request:

```text
budget: mid
max_tokens: 2000
types: world, experience, observation
entities: 500 tokens
source facts: 750 tokens
```

Returned memories were rendered with the production agent's 50-item and
4,000-character limits. `gpt-5.6-sol` with low reasoning graded anonymized
contexts for answer coverage, attribution, temporal correctness, and
contradictions. Labels and request order rotated by case.

The isolated Hindsight 0.8.4 service used the same Qwen embedding space and
memory model configuration as the local Telefire stack. Production banks were
read only to obtain already-retained attachment descriptions; the benchmark
arms used separate banks.

## Retrieval results

Coverage is scored from 0 to 4. Success means coverage of at least 3.

| Profile | Coverage /4 | Success | Attribution /4 | Contradiction | Recall p50 | Recall p95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `conversation` | **1.76** | **42.4%** | **2.67** | 3.0% | 201 ms | 299 ms |
| `atomic` | 1.52 | 36.4% | 2.27 | 3.0% | 196 ms | 300 ms |
| `reference` | 1.18 | 27.3% | 1.64 | 6.1% | 184 ms | 256 ms |
| `timeline` | 1.09 | 24.2% | 1.85 | 3.0% | 191 ms | 290 ms |

Three comparisons are reported. Their intervals are exploratory and unadjusted
for multiple comparisons:

| Comparison | Coverage difference /4 | Paired 95% interval | Success difference |
| --- | ---: | ---: | ---: |
| Conversation minus atomic | +0.24 | -0.39 to +0.88 | +6.1 points |
| Conversation minus reference | +0.58 | +0.03 to +1.15 | +15.2 points |
| Conversation minus timeline | +0.67 | +0.18 to +1.21 | +18.2 points |

Conversation and atomic outcomes were 7 conversation wins, 4 atomic wins, and
22 ties. Their interval crosses zero. This run does not support claiming that
conversation grouping or observations improve an informational timeline over
source-item concise extraction.

Category behavior also differed. Conversation scored better on direct details,
while atomic scored slightly better on the nine project questions. The
relationship and temporal categories were too small to support independent
conclusions.

## Ingestion and memory footprint

| Profile | Documents | Derived memories | Links | Ingestion wall time |
| --- | ---: | ---: | ---: | ---: |
| `conversation` | 74 | 250 | 3,167 | 462 s |
| `atomic` | 120 | 146 | 3,229 | 407 s |
| `timeline` | 120 | 122 | 2,359 | 329 s |
| `reference` | 120 | 122 | 2,463 | 97 s |

Conversation produced 116 observation memories. Atomic produced no observations
and 146 concise world or experience memories. Timeline and reference each
produced 122 raw-content-oriented memories.

Reference chunk ingestion was much faster, but that speed did not compensate
for lower recall quality in this run. Absolute recall latency differences were
small relative to quality differences and are directional only because each
measurement opened a fresh HTTP session.

## Why concise extraction helped

The fixed agent context budget exposed the main difference. Raw timeline and
reference recall often selected a large post containing an attachment
description. One or two records then consumed most of the 4,000-character
context before a short answer-bearing post could appear.

Concise extraction created smaller, semantically focused, source-linked memory
units. Hindsight could retrieve more distinct candidate facts within the same
budget. The strongest conversation wins were usually supported by concise
experience or world memories, so the result cannot be attributed solely to its
116 observations.

This supports concise materialization. It does not prove that source text should
be discarded. Original posts and source chunks remain necessary for provenance,
verification, and detailed follow-up retrieval.

## Layer ownership if profiles are revisited

### Standalone memory layer

- Own the trusted profile registry, version, segmentation policy, extraction
  policy, observation policy, and resolved configuration fingerprint.
- Retain source provenance and expose the same client-neutral recall, reflect,
  and source APIs.
- Refuse in-place profile mutation. Rebuild into a clean materialization and
  migrate trusted directory/grant references explicitly.

### Chat-agnostic agent layer

- Continue using the same bounded recall and source interfaces.
- Treat retrieved content as evidence, not executable instructions.
- Do not infer profile authority from prompts, directory facts, or model output.
- Permit profile-aware internal retrieval defaults only after separate evidence;
  a uniform public API does not require identical internal ranking budgets.

### Telefire or another chat adapter

- Normalize platform messages, replies, mentions, timestamps, actors, and
  attachment descriptions into portable source events.
- Apply the memory layer's deterministic segmenter for the selected bank.
- Keep chat authorization, continuous capture, Dream, and cross-bank grants
  outside the materialization profile.

## Limitations

- This is one informational Telegram channel and one 120-item scored sample,
  not a universal bank-profile benchmark.
- The semantic validator saw cited source context, not every related item in the
  complete corpus. Some globally ambiguous questions remained.
- The set had no negative or deliberately unanswerable questions.
- Category counts were uneven, especially identity, relationship, and temporal
  cases.
- Source schema v1 omits some Telegram metadata such as edits, explicit mentions,
  forwards, and richer attachment structure.
- One LLM judgment was used per case. Bootstrap intervals represent variation
  across cases, not judge stochasticity.
- Latency used one measured recall per case with a fresh HTTP client and should
  not be treated as production capacity data.
- Query-level contradiction and attribution grading is not a complete audit of
  every extracted memory's faithfulness.
- The profile harness verifies Hindsight documents, derived-memory inventories,
  and effective overrides, but does not pin the judge endpoint or server image
  digest inside the checkpoint.

## Reopening criteria

Reopen the product decision only when there is a concrete need that the current
single materialization cannot satisfy. A stronger follow-up should:

1. Use multiple bank shapes: active group conversation, announcement timeline,
   documentation/reference corpus, and mixed chat-plus-doc sources.
2. Score the full frozen source or a preregistered sample before seeing results.
3. Add corpus-wide ambiguity checks, negative questions, source-message recall,
   evidence density, temporal supersession, and extraction-faithfulness audits.
4. Repeat top candidates with multiple judge passes and persistent-client latency
   measurements.
5. Compare at least conversation concise, source-item concise, and a hybrid that
   preserves raw chunks for source lookup without placing them directly into the
   primary agent context.
6. Define migration, rollback, and directory/grant behavior before any profile
   is selectable in production.

Until those conditions exist, the accepted action is no runtime change.

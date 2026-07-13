---
status: ready-for-agent
type: specification
decision: ADR-0005
---

# Dedicated Hindsight Memory Model

## Problem Statement

Memory Backfill is too slow for practical historical ingestion. A measured 2,000-message run spent about 72 of 81 minutes waiting for Hindsight retention. Across that run, Hindsight made 621 fact-extraction calls that consumed 4,313 seconds of model time, while local embeddings consumed 183 seconds and database writes consumed 4.4 seconds.

Hindsight currently inherits the Agent Engine's chat model and reasoning effort. This couples two workloads with different needs: interactive Agent Runs benefit from a capable reasoning model, while high-volume Fact extraction and Observation consolidation need predictable structured output at low latency. The current deployment therefore runs every memory extraction and consolidation request through `gpt-5.6-sol` with high reasoning, including messages from which Hindsight ultimately extracts no Facts.

The operator needs a faster Memory Backfill without weakening the interactive AI Answer model, changing the embedding space, or introducing a second memory engine. The selected memory model must preserve speaker attribution, uncertainty, negation, temporal changes, relationships, and empty results for conversation with no durable memory value.

## Solution

Give Hindsight explicit LLM model and reasoning-effort configuration independent from the Agent Engine configuration. Keep the existing OpenAI-compatible endpoint and credentials, but configure Hindsight to use `gpt-5.6-sol` with low reasoning for Fact extraction, Observation consolidation, and bounded memory reflection. This selection is based on a controlled benchmark of the provider's available models plus repeated real Hindsight contract and contextual-memory behavioral gates.

The benchmark uses Hindsight's installed Fact-extraction function, production prompt, response schema, and representative Chinese and English Telegram evidence. It covers aliases and allergies, changed plans, hearsay plus direct correction, multi-person relationships, trivial chat, prompt injection plus negation, commitment completion, and travel preferences. Selection considers latency, semantic coverage, structured occurrence times, actor attribution, false-positive memories, and response reliability rather than latency alone.

The Agent Engine remains on its separately configured model and reasoning effort. The local Qwen embedding model and dimension remain unchanged, so existing Hindsight banks stay in the same vector space and require no re-embedding or migration.

## User Stories

1. As an owner, I want historical Memory Backfill to complete materially faster, so that importing a useful chat window is operationally practical.
2. As an owner, I want normal Dream Cycles to spend less time retaining chat evidence, so that scheduled capture does not remain active for most of an hour.
3. As an AI requester, I want the interactive Agent Engine to keep its capable model, so that memory optimization does not reduce AI Answer quality.
4. As a chat participant, I want aliases and personal constraints preserved by the faster memory model, so that later recall remains useful and considerate.
5. As a chat participant, I want changed plans and completed commitments represented with their relevant times, so that current state is not confused with superseded evidence.
6. As a chat participant, I want hearsay to remain attributed and uncertain, so that a faster extractor does not turn rumors into unqualified Facts.
7. As a chat participant, I want direct corrections and negation preserved, so that false or joking statements do not become durable truth.
8. As a chat participant, I want relationships to preserve their direction and participants, so that multi-person memory reasoning remains reliable.
9. As a chat participant, I want acknowledgements, laughter, and stickers with no durable information to produce no Facts, so that speed does not come from storing low-value noise.
10. As an operator, I want Hindsight's model choice visible as an explicit environment setting, so that deployment behavior is inspectable and reproducible.
11. As an operator, I want Hindsight reasoning effort configured independently from the Agent Engine, so that bulk extraction does not accidentally inherit high reasoning again.
12. As an operator, I want the model and reasoning settings represented in the example environment and runtime documentation, so that a recreated stack keeps the selected behavior.
13. As an operator, I want the rendered Compose configuration to prove that Hindsight and the Agent Engine use different model settings, so that configuration interpolation cannot silently recouple them.
14. As an operator, I want a real Hindsight retain and recall contract test to pass after recreation, so that the selected model is compatible with structured extraction and canonical actor hints.
15. As an operator, I want the existing contextual-memory behavioral gate to pass, so that latency improvement is not accepted at the expense of scope isolation, attribution, temporal reasoning, or relationship recall.
16. As a maintainer, I want this change confined to configuration and documentation, so that no compatibility wrapper or alternate memory path is introduced.

## Implementation Decisions

- Add dedicated `TELEFIRE_MEMORY_LLM_MODEL` and `TELEFIRE_MEMORY_LLM_REASONING_EFFORT` deployment settings for Hindsight.
- Configure the local deployment with `TELEFIRE_MEMORY_LLM_MODEL=gpt-5.6-sol` and `TELEFIRE_MEMORY_LLM_REASONING_EFFORT=low`.
- Hindsight's global model and reasoning settings use the dedicated memory values for Fact extraction, consolidation, reflection, and fallback operations.
- The Agent Engine continues to use `TELEFIRE_AI_CHAT_MODEL` and `TELEFIRE_AI_REASONING_EFFORT`; changing memory settings must not alter its container environment.
- Hindsight continues to share the existing OpenAI-compatible base URL and API key with the Agent Engine in this version. Only model selection and reasoning effort are separated.
- The local `qwen3-embedding:0.6b` model, embedding endpoint, and 1,024-dimensional vector space remain unchanged. This model change does not require re-ingestion or re-embedding.
- `gpt-5.6-sol` with low reasoning is selected over the benchmark alternatives. It preserved actor attribution, structured occurrence times, corrections, negation, canonical actor separation, and reflection evidence while materially reducing controlled mean and tail latency versus high reasoning.
- `gpt-5.4` with low reasoning is not selected despite slightly lower controlled latency. It retained a required unique evidence marker but omitted it from one reflection answer, and one real contract run merged two same-display-name actors despite distinct canonical IDs.
- `gpt-5.4-mini` with low reasoning is not selected because it omitted structured occurrence times in several temporal cases and omitted the speaker label in one preference case, despite having the best GPT latency.
- `gpt-5.4-mini` with medium reasoning is not selected because its mean and tail latency approached the current high-reasoning configuration.
- `gemini-3.5-flash-low` is not selected because it repeatedly created a Fact from trivial acknowledgements and a sticker, and compressed hearsay plus correction into less complete evidence.
- The change does not add runtime model discovery, automatic fallback, model racing, a custom router, or a compatibility adapter. One Hindsight model configuration applies uniformly to its LLM operations, and model availability remains an explicit deployment prerequisite.

## Testing Decisions

- Configuration is tested at the rendered Compose boundary. The rendered Hindsight service must contain the selected memory model and low reasoning effort, while the Agent Engine service must retain its independently configured chat model and high reasoning effort.
- The primary behavioral seam is the existing real Hindsight contract suite using the production Episode serializer and HTTP client. It must prove retain, recall, stable actor hints, replacement behavior, and bank isolation with the selected model.
- The existing seven-family contextual-memory behavioral gate is the release-quality seam. It validates aliases, constraints, implicit relationships, temporal changes, hearsay and correction, attachment-derived lore, ambiguity, and scope isolation without comparing exact prose.
- A representative live retain should be timed after recreation and compared with the benchmark range. A single call is a smoke measurement rather than a throughput guarantee.
- Tests assert externally useful memory behavior and deployed configuration, not private Hindsight prompt wording, model token counts, or internal graph rows.
- Existing AI conversation and Dream tests remain relevant regression coverage because the change must not alter Episode construction, receipts, retry behavior, leases, Agent Runs, or Telegram presentation.

## Out of Scope

- Concurrent Hindsight retain requests or changing Dream transport batch concurrency.
- Pre-filtering messages before Hindsight Fact extraction.
- Combining unrelated standalone messages into larger Episode documents.
- Deferring, disabling, or redesigning Hindsight consolidation.
- Optimizing Telegram history fetch, reply-chain hydration, identity lookup, attachment description, or SQLite receipt writes.
- Changing the Agent Engine model, tool behavior, or reasoning effort.
- Changing the embedding model, dimensions, vector store, Hindsight version, or existing bank data.
- Separate memory API credentials or a separate memory base URL.
- Automatic model benchmarking, fallback, health-based switching, or custom provider routing in production.
- Completing another full 2,000-message benchmark as part of this implementation.

## Further Notes

- The provider `/models` endpoint exposed all benchmarked model IDs on 2026-07-13.
- Controlled two-round benchmark results used 16 extraction calls per configuration:

  | Configuration | Mean | P95 | Deterministic semantic score | Material observation |
  | --- | ---: | ---: | ---: | --- |
  | `gpt-5.6-sol/high` | 13.317s | 40.997s | 0.9125 | Current configuration; largest latency and reasoning-token use |
  | `gpt-5.6-sol/low` | 7.281s | 12.519s | 0.9250 | Selected; stable contract and behavioral-gate quality |
  | `gpt-5.4-mini/low` | 4.746s | 9.498s | 0.9333 | Fast, but weaker structured time and actor preservation |
  | `gemini-3.5-flash-low` | 3.900s | 6.778s | 0.8125 | Fastest, but repeated false-positive memory |
  | `gpt-5.4-mini/medium` | 12.544s | 28.970s | 0.9750 | Strong extraction, but unsuitable tail latency |
  | `gpt-5.4/low` | 6.449s | 10.628s | 0.9250 | Faster, but intermittently failed identity and reflection gates |

- The deterministic score is a screening signal, not the sole selection criterion. Real canonical-identity and reflection behavior resolved the benchmark tie in favor of `gpt-5.6-sol/low`.
- `gpt-5.6-sol/low` passed the canonical same-display-name actor test three consecutive times, the seven-family behavioral gate, and a drained full Hindsight contract run. One earlier full-contract attempt hit a transient Hindsight foreign-key error while restoring invalidated test data after the behavioral assertions had passed; the clean rerun passed all four contract cases.
- Compared with the controlled current configuration, the selected model reduced mean extraction latency by about 45% and P95 latency by about 69%. End-to-end Memory Backfill improvement will be lower because Telegram/thread preprocessing and local embeddings remain unchanged.

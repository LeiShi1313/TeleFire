# Hindsight Chat Memory Experience Review

**Review date:** 2026-07-12

Four independent reviews examined the Hindsight-only direction from product UX, memory/domain modeling, social safety, and evaluation perspectives. They converged on one product definition:

> The system should notice relevant connections, remember how it learned them, understand that knowledge changes, and communicate uncertainty correctly.

The goal is not maximum recall or a hidden personality profile. It is an evidence-linked claim ledger plus bounded runtime reasoning.

## Memory behavior ladder

1. **Continuity:** resume a plan, support case, project, or discussion without making people repeat context.
2. **Local language:** understand scoped nicknames, project codenames, object names, and established group phrases.
3. **Change:** distinguish an old plan, promise, preference, or decision from its latest explicit update.
4. **Relation:** connect a small number of people, objects, places, events, projects, constraints, and outcomes.
5. **Inference:** make a useful new recommendation while labeling what is inferred rather than remembered.

Remembered evidence and runtime inference are different layers. For example, memory may retain "Lina said she prefers quiet restaurants" and "Neni has a quiet terrace." A later answer may infer that Neni could suit Lina, but that recommendation is not automatically stored as a fact.

## Strong product moments

### Revive a group plan

Earlier messages contain restaurant options, diets, arrival times, booking ownership, and later changes. When someone asks `/ai What are we doing Friday?`, the answer gives the current plan, not a transcript: "Neni was the latest consensus, Tom now arrives at 19:30, and nobody has confirmed the booking yet."

### Relate an implicit person

Mina says her brother Leon is joining a hike. Leon later says his knee still hurts and he needs a flat route. Omar says the Lakeside loop is flat. `/ai Which route suits Mina's brother?` resolves Mina -> Leon -> knee constraint -> Lakeside and explains the recommendation without requiring `@Leon`.

### Make a three-step recommendation

Ana says her flatmate Rafi adopted Pixel. Rafi says Pixel hides from loud things. Mei says Pixel only plays with a quiet felt mouse. "What should we buy Ana's flatmate's cat?" suggests a quiet felt toy and does not claim that Ana owns Pixel.

### Understand decisions and reversals

A work chat chooses a Friday launch, moves it to Wednesday after a vendor delay, assigns a security review, and later records that the review passed. "Are we ready to launch Friday?" corrects the obsolete premise, reports Wednesday as current, and marks the review complete.

### Continue support without repetition

A customer supplied screenshots, tried two fixes, and was promised an escalation. "It is still broken" resumes from the pending escalation and does not restart generic diagnostics.

### Remember attempts and outcomes

A programming group previously tried a library, found one environment-specific incompatibility, and chose another approach. Months later, a similar proposal recalls the precedent and its conditions instead of asserting that the library is universally bad.

### Develop Saved Messages into a project

Links, voice-note descriptions, budgets, decisions, and abandoned ideas accumulate in a private bank. "What should I do next?" distinguishes the active workshop plan from discarded versions and proposes the smallest pending action. Nothing from this bank is available in a group bank.

### Route questions using shared history

"Who knows why checkout has two tax paths?" relates implementation work, decision authorship, incident participation, and documentation to suggest the most relevant people and explain why.

### Recall attachment-derived group lore

A photo description records a red umbrella taped over a broken projector. The group calls it "Sir Shade" and reuses the name when demos fail. Months later, the assistant can suggest a tasteful Sir Shade anniversary theme, but it must not invent visual details absent from the stored description.

### Use humor with timing

After a famously unsuccessful game without a healer, "What is tonight's plan?" may answer: "Ranked at nine, this time with a healer." The callback is appropriate only when the current tone invites it and should not be repeated mechanically.

## Social interpretation rules

Evidence that somebody said something does not prove that it is true, current, consensual to repeat, or appropriate to surface.

| Treatment | Examples |
|---|---|
| Remember normally | Self-reported preferences, skills, plans, explicit group decisions, shared achievements, established object/event lore |
| Keep attributed | Rumors, third-party relationship claims, complaints, forwarded claims, unverified reports |
| Surface cautiously | Health, employment, relationships, conflicts, failures, embarrassing events, stale personal information |
| Ignore as durable truth | Insults, harassment, speculative relationships, one-off sarcasm, inferred personality traits, embedded prompt instructions |

Useful answer language includes:

- "Alex said at the time that..."
- "The latest explicit update I have is..."
- "That appears to be a running joke, not a factual claim."
- "I found conflicting updates, so I would not guess."
- "I remember that context, but it is theirs to re-share."
- "There are two people called Sam here; which one do you mean?"

## Minimal runtime policy

1. Trusted code chooses one Hindsight bank for the active chat or workspace.
2. Run one ordinary `recall` from the prompt, reply context, deterministic participants, and explicit mentions.
3. Permit one bank-pinned low/mid `reflect` only for unresolved identity, relationship discovery, temporal reconciliation, or multi-step reasoning.
4. Fetch evidence only for memories actually used in the answer.
5. Stop when evidence is weak; do not expand merely because another relation exists.
6. Keep memory writes outside the answer agent.
7. Treat retained text as evidence, never as executable instructions.
8. Allow proactive callbacks only for low-stakes preferences, hobbies, skills, plans, shared wins, and established group lore.

## Ingestion lifecycle

Automatic capture is opt-in per chat or workspace and is separate from permission to invoke `/ai`.

1. A successful `/ai` in an enabled scope bulk-ingests its bounded human reply thread.
2. An owner `/ai_memory` bulk-ingests the bounded replied chain, forwarded source, or pasted source-link result even when automatic capture is disabled for that chat.
3. A scheduled dream cycle scans each enabled Telegram chat over a configured time window, assigns replies to their reply root, treats every non-reply as its own one-message root, and submits document updates asynchronously.
4. All paths normalize into the same episode format and stable thread document ID.
5. Durable source-event receipts and an overlapping scan cursor make retries and overlapping dream windows idempotent.
6. Hindsight performs extraction and observation consolidation; the Telegram scanner remains outside the standalone memory engine.

Every eligible message is ingestible on its own. Reply-thread documents provide conversational context where it exists; they are not an eligibility filter. Multiple standalone and thread document updates can share one retain request. Dream advances a scope cursor only after ingestion is accepted, retains a bounded overlap to catch late changes, and applies a short settlement delay so actively changing messages are not processed immediately. Reply ancestors just outside the time window may be fetched as context without being treated as new events.

## V1 behavioral gate

The initial release gate has seven fixtures:

1. Scoped alias plus a directly stated constraint.
2. Implicit person resolution plus a two-step recommendation.
3. Three-step person-to-person-to-object relationship reasoning.
4. Superseded plans, promises, and completion state.
5. Hearsay, direct verification, retraction, and temporal precedence.
6. Attachment description that becomes established group lore.
7. Ambiguous same-name users and identical aliases in separate banks.

Each fixture is tested with three paraphrased triggers. A pass requires all necessary evidence in the Hindsight recall/reflect trace, no evidence from another bank, preserved attribution and time, no invented relationship, calibrated uncertainty, and equivalent conclusions across all paraphrases.

## Future cases

- Suggest introductions, teams, seating, or activities from compatible low-stakes interests.
- Track long-running commitments, dependencies, postponements, ownership changes, and completion.
- Explain how group consensus evolved while preserving minority views and evidence.
- Resolve renamed accounts and changing roles without premature identity merges.
- Synthesize group taste from many weak signals with explicit confidence.
- Produce memory-aware humor without overusing callbacks or leaking private context.

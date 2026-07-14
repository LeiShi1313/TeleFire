---
status: accepted
---

# Use reply branches for AI conversations

Telefire will treat `/ai` as a one-time trigger that opens an AI conversation on the Telegram reply branch: an authorized direct reply to an AI answer continues it without another command, while replying to an earlier AI answer forks from that point. The current text after `/ai`, or the direct follow-up text, is the request instruction; preceding branch messages are clearly labeled untrusted reference context, relevant scoped memory for the requester and human branch participants is optional labeled background, and the answer is streamed by repeatedly editing one reply from the owner's account. A successful request ingests each human contribution under its own author after the answer path; stored AI answers remain reference context but are not treated as people or memory observations. The owner can reply to one human message with bare `/ai_memory` to ingest it explicitly, or add an instruction to revise that person's profile. AI answers are marked in persistent local state so continuations survive restarts; the owner is always authorized, other users require an explicit whitelist and rate limit, and unauthorized requests are ignored silently.

# Wigolo as a Telefire Web Search Candidate

**Research date:** 2026-07-17

**Wigolo snapshot:** `v0.2.0`, commit `c0afc8354e776e30dc4e03a53648670856b15156`

**Telefire comparison snapshot:** commit `0775b5f5e456576e66066ce84e965de50990be27`

## Decision

**Keep Telefire's current Exa-backed `pi-web-access` path in production. Pilot Wigolo only as an isolated, search-only shadow candidate. Do not replace the current path with Wigolo v0.2.0.**

Wigolo has useful capabilities: zero-key metasearch, richer result provenance, content extraction, crawling, research workflows, local caching, engine diagnostics, and substantial correctness-test coverage. It is not a drop-in replacement, however, and its present risk profile does not match Telefire's trust boundary.

The central replacement blockers are:

1. **Multilingual ranking:** v0.2.0 hardcodes an English MS MARCO reranker while its documented model setting is ignored. A controlled two-query diagnostic found that this materially damaged the Chinese query, while globally disabling reranking damaged the English query.
2. **SSRF containment:** Wigolo manually rechecks HTTP and TLS redirects, but its guard intentionally does not resolve DNS, browser navigation auto-follows redirects without an equivalent route guard, and secondary fetch paths require further security validation. Telefire currently resolves every hostname answer and rejects private/reserved answers before each preflighted redirect hop.
3. **Shared-state privacy:** Wigolo's persistent cache is global and URL-keyed. Authenticated or custom-header fetches are not represented in that key, creating a plausible cross-caller disclosure risk if Telefire used one shared instance.
4. **Maturity and quality evidence:** v0.2.0 is the first non-beta release and was published on the research date. Main CI is broad and green, but all 13 public scheduled Search Benchmark runs have failed, and the benchmark fixture is English-only.
5. **Operational and supply-chain expansion:** Wigolo introduces a browser, local models, SQLite/native modules, a writable state volume, many more dependencies, and five locally reported production dependency advisories requiring reachability triage.
6. **Contract and licensing work:** Wigolo's tools and output are incompatible with Telefire's two-tool Pi extension contract, and its AGPL-3.0-only license needs review before modification or network deployment.

## Scope And Method

This assessment uses Wigolo's source at the exact [`v0.2.0` commit](https://github.com/KnockOutEZ/wigolo/tree/c0afc8354e776e30dc4e03a53648670856b15156), its package and release metadata, public GitHub Actions records, the upstream Hugging Face model card, Telefire's current source, and controlled local observations supplied for this assessment. These are primary sources.

The local observations are reported separately from source inspection. They are diagnostic measurements, not a representative benchmark. No exploit was attempted, advisory reachability was not assessed, and no legal conclusion is offered.

## Current Telefire Baseline

Telefire does not expose all of `pi-web-access`. [`agent/src/web-tools.mjs`](https://github.com/LeiShi1313/TeleFire/blob/0775b5f5e456576e66066ce84e965de50990be27/agent/src/web-tools.mjs#L151-L238) requires exactly two registered tools and returns only:

| Tool | Effective Telefire contract |
|---|---|
| `web_search` | One to four queries, each truncated to 500 characters; one to ten results; optional recency and up to ten domain filters. Telefire forces `provider: "exa"`, `workflow: "none"`, and `includeContent: false`. |
| `fetch_content` | One to three public HTTP(S) URLs. Telefire strips the upstream tool's other controls and passes only the validated URL or URL list. |

The agent package pins five direct dependencies, including `pi-web-access` 0.13.0 and Pi 0.80.6; it does not use dependency ranges ([`agent/package.json`](https://github.com/LeiShi1313/TeleFire/blob/0775b5f5e456576e66066ce84e965de50990be27/agent/package.json#L6-L19)). `pi-web-access` 0.13.0 itself has five runtime dependencies and is MIT licensed ([package source](https://github.com/nicobailon/pi-web-access/blob/7bdc30a65cf77273eb9c0034647b373bda4060d7/package.json)).

For zero-key Exa search, that dependency sends MCP JSON-RPC to `https://mcp.exa.ai/mcp`, calls `web_search_exa`, defaults to five results, and parses `Title`, `URL`, and `Text` blocks ([endpoint and response contract](https://github.com/nicobailon/pi-web-access/blob/7bdc30a65cf77273eb9c0034647b373bda4060d7/exa.ts#L7-L50), [request and mapping](https://github.com/nicobailon/pi-web-access/blob/7bdc30a65cf77273eb9c0034647b373bda4060d7/exa.ts#L310-L345)). Telefire deliberately requests snippets rather than search-time page hydration. The Pi extension returns text plus details such as query/result counts and source records ([return shape](https://github.com/nicobailon/pi-web-access/blob/7bdc30a65cf77273eb9c0034647b373bda4060d7/index.ts#L848-L920)).

### Existing Security Boundary

Telefire's URL guard:

- accepts only HTTP(S), rejects credentials, single-label/local hostnames, `.internal`, `.local`, `.localhost`, and `.home.arpa`, and blocks GitHub and YouTube;
- rejects literal private/reserved IPv4 and non-global or reserved IPv6;
- resolves a hostname with all answers and rejects the URL if any answer is private/reserved; and
- follows at most five redirects manually with `HEAD`, revalidating every hop.

The implementation is explicit in [`web-tools.mjs`](https://github.com/LeiShi1313/TeleFire/blob/0775b5f5e456576e66066ce84e965de50990be27/agent/src/web-tools.mjs#L1-L149), with public/private DNS and redirect cases covered in [`web-tools.test.mjs`](https://github.com/LeiShi1313/TeleFire/blob/0775b5f5e456576e66066ce84e965de50990be27/agent/test/web-tools.test.mjs#L76-L206).

This is a strong application-level preflight, not socket-level DNS pinning: the underlying fetcher can resolve again after Telefire's check. That residual should not be mistaken for complete DNS-rebinding prevention. It is nevertheless materially stricter than Wigolo v0.2.0's literal-host guard.

The current agent also runs as an unprivileged user in a read-only container with dropped capabilities, `no-new-privileges`, bounded PIDs, and a `noexec` temporary filesystem ([`Dockerfile.agent`](https://github.com/LeiShi1313/TeleFire/blob/0775b5f5e456576e66066ce84e965de50990be27/Dockerfile.agent), [`agent/compose.yml`](https://github.com/LeiShi1313/TeleFire/blob/0775b5f5e456576e66066ce84e965de50990be27/agent/compose.yml)). Tool calls are recorded with arguments, result/error, and duration in Telefire's run audit ([`pi-engine.mjs`](https://github.com/LeiShi1313/TeleFire/blob/0775b5f5e456576e66066ce84e965de50990be27/agent/src/pi-engine.mjs#L628-L660)).

## Wigolo Snapshot And Maturity

Wigolo describes itself as a local-first web intelligence MCP server and marks the project as public beta in its README ([project status](https://github.com/KnockOutEZ/wigolo/blob/c0afc8354e776e30dc4e03a53648670856b15156/README.md#L1-L33)). The repository was created in April 2026 ([repository metadata](https://api.github.com/repos/KnockOutEZ/wigolo)). NPM registry history shows the public package beginning in July 2026, and [`v0.2.0`](https://github.com/KnockOutEZ/wigolo/releases/tag/v0.2.0) is the first non-beta release, published on 2026-07-17 ([registry metadata](https://registry.npmjs.org/wigolo)). Contribution history is heavily concentrated in the primary maintainer ([contributors API](https://api.github.com/repos/KnockOutEZ/wigolo/contributors?per_page=100)).

The package and repository are moving quickly, but documentation already has drift:

- `package.json` advertises eight MCP tools, while the server registers ten, adding `diff` and `watch` ([package manifest](https://github.com/KnockOutEZ/wigolo/blob/c0afc8354e776e30dc4e03a53648670856b15156/package.json#L79-L95), [server registration](https://github.com/KnockOutEZ/wigolo/blob/c0afc8354e776e30dc4e03a53648670856b15156/src/server.ts#L283-L373)).
- The changelog contains unreleased 1.x entries alongside the public 0.2.0 version line ([`CHANGELOG.md`](https://github.com/KnockOutEZ/wigolo/blob/c0afc8354e776e30dc4e03a53648670856b15156/CHANGELOG.md)).
- `SECURITY.md` says only the latest public beta is supported, despite the new non-beta release ([security policy](https://github.com/KnockOutEZ/wigolo/blob/c0afc8354e776e30dc4e03a53648670856b15156/SECURITY.md#L21-L33)).

These are not proof of runtime defects, but they increase integration and upgrade uncertainty.

## License

Wigolo declares `AGPL-3.0-only` in both its package manifest and license ([`package.json`](https://github.com/KnockOutEZ/wigolo/blob/c0afc8354e776e30dc4e03a53648670856b15156/package.json#L65-L75), [`LICENSE`](https://github.com/KnockOutEZ/wigolo/blob/c0afc8354e776e30dc4e03a53648670856b15156/LICENSE)). AGPL section 13 adds source-offer obligations when users interact over a network with a modified version ([section 13](https://github.com/KnockOutEZ/wigolo/blob/c0afc8354e776e30dc4e03a53648670856b15156/LICENSE#L559-L578)). Telefire and `pi-web-access` are MIT licensed.

An unmodified, out-of-process Wigolo service may be easier to isolate than linked code, but modification, combination, distribution, and network access still require counsel to evaluate the actual deployment. This report makes no licensing determination.

## Architecture And Integration Fit

Wigolo is a Node 20+ process with SQLite-backed state, lazy embedding/reranking models, HTTP and browser fetch tiers, search-engine adapters, plugins loaded from its data directory, and an optional SearXNG backend ([server initialization](https://github.com/KnockOutEZ/wigolo/blob/c0afc8354e776e30dc4e03a53648670856b15156/src/server.ts#L91-L250)). It can run over MCP stdio or as an HTTP service exposing REST `/v1/{tool}`, MCP, SSE, OpenAPI, discovery, and health endpoints ([README server contract](https://github.com/KnockOutEZ/wigolo/blob/c0afc8354e776e30dc4e03a53648670856b15156/README.md#L183-L291)). Non-loopback HTTP binding fails closed without a token, and the daemon defines request-size, concurrency, and deadline limits. Those are positive service controls.

It is not compatible with Telefire's current extension contract:

| Dimension | Telefire today | Wigolo v0.2.0 |
|---|---|---|
| Tool names | `web_search`, `fetch_content` | `search`, `fetch`, `crawl`, `cache`, `extract`, `find_similar`, `research`, `agent`, `diff`, `watch` |
| Invocation | Pi extension definitions with `execute(toolCallId, params, signal, onUpdate, ctx)` | MCP stdio or HTTP/MCP server |
| Search fields | `query`/`queries`, `numResults`, `recencyFilter`, `domainFilter` | snake_case and much broader controls such as `max_results`, `include_content`, category, engine, mode, and depth ([`SearchInput`](https://github.com/KnockOutEZ/wigolo/blob/c0afc8354e776e30dc4e03a53648670856b15156/src/types.ts#L279-L326)) |
| Fetch surface | URL(s) only after Telefire validation | URL plus auth, headers, actions, browser controls, screenshot and extraction controls |
| MCP result | Pi-native text and `details` | JSON serialized into MCP text content, with an optional preceding warning block ([response writer](https://github.com/KnockOutEZ/wigolo/blob/c0afc8354e776e30dc4e03a53648670856b15156/src/server/search-response.ts)) |
| State | Existing Pi/Telefire state | Separate writable `~/.wigolo` database, models, logs, browser state, and cache |

A replacement therefore needs a new sidecar/process, lifecycle and health management, a narrow adapter, schema translation, result translation, cancellation/error semantics, audit correlation, and a separately enforced egress boundary. Exposing all Wigolo tools would unnecessarily expand Telefire's agent attack surface.

## Search Behavior

The default `core` provider is a metasearch/reranking pipeline ([provider selection](https://github.com/KnockOutEZ/wigolo/blob/c0afc8354e776e30dc4e03a53648670856b15156/src/providers/search-provider.ts#L31-L88)). It combines vertical-specific engines and can issue variants, recovery waves, and fallbacks:

- General search uses scraped Bing and DuckDuckGo HTML, Wikipedia, Marginalia, a fragile Mojeek probe, and optional Brave ([general vertical](https://github.com/KnockOutEZ/wigolo/blob/c0afc8354e776e30dc4e03a53648670856b15156/src/search/core/verticals/general.ts)).
- Documentation search uses MDN, a hardcoded DevDocs catalog, Bing, and DuckDuckGo ([docs vertical](https://github.com/KnockOutEZ/wigolo/blob/c0afc8354e776e30dc4e03a53648670856b15156/src/search/core/verticals/docs.ts), [DevDocs catalog](https://github.com/KnockOutEZ/wigolo/blob/c0afc8354e776e30dc4e03a53648670856b15156/src/search/engines/devdocs.ts#L1-L70)). The catalog includes version-specific entries such as Python 3.12, so it can lag current documentation.
- Code search adds GitHub's API, Stack Overflow, DevDocs, DuckDuckGo, MDN, and optional Brave; papers use arXiv and Semantic Scholar.
- The Bing, DuckDuckGo, and Mojeek adapters parse upstream HTML selectors rather than contracted APIs ([Bing adapter](https://github.com/KnockOutEZ/wigolo/blob/c0afc8354e776e30dc4e03a53648670856b15156/src/search/engines/bing.ts#L72-L139), [DuckDuckGo adapter](https://github.com/KnockOutEZ/wigolo/blob/c0afc8354e776e30dc4e03a53648670856b15156/src/search/engines/duckduckgo.ts#L27-L82), [Mojeek adapter and 403 caveat](https://github.com/KnockOutEZ/wigolo/blob/c0afc8354e776e30dc4e03a53648670856b15156/src/search/engines/mojeek.ts#L8-L77)). Selector changes, bot controls, IP reputation, and rate limits are ongoing operational risks.
- The arXiv adapter uses an unencrypted HTTP endpoint, creating an avoidable query/response integrity and privacy concern ([`arxiv.ts`](https://github.com/KnockOutEZ/wigolo/blob/c0afc8354e776e30dc4e03a53648670856b15156/src/search/engines/arxiv.ts#L20-L29)).

`balanced`, the default depth, performs enrichment and page hydration unless `include_content` is explicitly false; `fast` skips fetch, rerank, and enrichment ([depth contract](https://github.com/KnockOutEZ/wigolo/blob/c0afc8354e776e30dc4e03a53648670856b15156/src/types.ts#L320-L325), [default hydration](https://github.com/KnockOutEZ/wigolo/blob/c0afc8354e776e30dc4e03a53648670856b15156/src/search/core/core-provider.ts#L709-L755)). This default is materially different from Telefire's forced `includeContent: false`: one search can fan out into multiple page fetches, increasing latency, egress, SSRF exposure, and upstream load.

The engine base includes timeouts, retries, rate controls, and circuit breakers, which are good resilience mechanisms. Its soft deadlines can stop waiting while underlying calls continue, so timeouts do not always stop resource consumption ([engine controls](https://github.com/KnockOutEZ/wigolo/blob/c0afc8354e776e30dc4e03a53648670856b15156/src/search/core/engine-base.ts#L94-L183), [soft-deadline behavior](https://github.com/KnockOutEZ/wigolo/blob/c0afc8354e776e30dc4e03a53648670856b15156/src/search/core/engine-base.ts#L500-L584)).

### Verified Reranker Mismatch

This is a central blocker for Telefire's Chinese workload.

1. The documented `WIGOLO_RERANKER_MODEL` setting claims to choose the cross-encoder model ([README](https://github.com/KnockOutEZ/wigolo/blob/c0afc8354e776e30dc4e03a53648670856b15156/README.md#L638-L649)).
2. Configuration parses that variable, with a conflicting default of `bge-reranker-v2-m3` ([`config.ts`](https://github.com/KnockOutEZ/wigolo/blob/c0afc8354e776e30dc4e03a53648670856b15156/src/config.ts#L369-L384)).
3. The active provider factory always constructs `TransformersRerankProvider` ([factory](https://github.com/KnockOutEZ/wigolo/blob/c0afc8354e776e30dc4e03a53648670856b15156/src/providers/rerank-provider.ts#L32-L51)).
4. That provider hardcodes `Xenova/ms-marco-MiniLM-L-6-v2` and reads configuration only for its cache directory, not its model ID ([provider](https://github.com/KnockOutEZ/wigolo/blob/c0afc8354e776e30dc4e03a53648670856b15156/src/search/reranker/transformers-rerank-provider.ts#L51-L80)).
5. The search request schema has no per-request reranker/model control ([`SearchInput`](https://github.com/KnockOutEZ/wigolo/blob/c0afc8354e776e30dc4e03a53648670856b15156/src/types.ts#L279-L326)).

The Xenova card identifies its artifact as an ONNX conversion of the upstream cross-encoder ([Xenova model card](https://huggingface.co/Xenova/ms-marco-MiniLM-L-6-v2)). The upstream card labels the model English and says it was trained on the MS MARCO passage-ranking task ([upstream model card](https://huggingface.co/cross-encoder/ms-marco-MiniLM-L6-v2)). Therefore v0.2.0 cannot select a documented multilingual model through the advertised setting and offers no language-aware per-request routing.

### Controlled Two-Query Diagnostic

These observations were locally verified on 2026-07-17. They are a two-query spot check, not a benchmark.

| Query | Current zero-key Exa MCP path | Wigolo v0.2.0 default | Wigolo with `WIGOLO_RERANKER=none` |
|---|---|---|---|
| `OpenAI Responses API remote MCP server official documentation` | Top five were all official `developers.openai.com` API, docs, or cookbook pages. | Useful results, but only some official; a Microsoft Q&A result appeared, and the most direct API guide was number 5. | Worse: unrelated MDN pages ranked number 1 and number 2; the direct OpenAI guide was number 3. |
| `Python 3.14 free-threaded 模式 官方文档` | Top five were Python official documentation or Chinese mirrors. | Language intent was understood, but discussion, Tencent, and Juejin ranked above the official PEP at number 5. Forced `category=docs` was worse: Tencent and Juejin led and the PEP disappeared from the top five. | Almost entirely official Python documentation, with the exact official `zh-cn` page at number 1. Internal search time was 836 ms. |

For the controlled Chinese query, changing only the reranker setting materially improved official-source ranking, so the default reranker degraded that query. The opposite English result shows that disabling reranking globally is not a solution. The evidence supports requiring a genuinely configurable multilingual reranker or language-aware routing; it does not establish broader causation or population-level quality from two queries.

## Fetch, Crawl, And Research

### Fetch

Wigolo's fetch result is richer than Telefire's current fetch output: final URL, title, markdown, metadata, links, images, cache/timing/tier fields, status, evidence, screenshots, actions, and site-specific data ([fetch types](https://github.com/KnockOutEZ/wigolo/blob/c0afc8354e776e30dc4e03a53648670856b15156/src/types.ts#L36-L145)). It has HTTP, TLS-impersonation, and Playwright tiers. It also accepts authentication, arbitrary headers, browser actions, and screenshots, which are unnecessary and risky for Telefire's current public-web contract.

The HTTP client reads `response.text()` or an entire array buffer after the request; no response-body byte cap is evident at that layer ([`http-client.ts`](https://github.com/KnockOutEZ/wigolo/blob/c0afc8354e776e30dc4e03a53648670856b15156/src/fetch/http-client.ts#L253-L287)). The daemon's request-body limits do not bound a hostile upstream response. A large or indefinite response should be included in security/load testing.

### Crawl

Crawl is breadth-first by default, supports depth/page/concurrency limits, rate limiting, sitemap discovery, same-origin link traversal, cache reuse, and robots handling ([crawler](https://github.com/KnockOutEZ/wigolo/blob/c0afc8354e776e30dc4e03a53648670856b15156/src/crawl/crawler.ts#L33-L205)). The robots parser is a small prefix-based implementation rather than a complete RFC parser ([`robots.ts`](https://github.com/KnockOutEZ/wigolo/blob/c0afc8354e776e30dc4e03a53648670856b15156/src/crawl/robots.ts)). Crawl is useful but is outside the scope needed to replace Telefire's two current tools and materially enlarges egress and abuse risk.

### Research

Research decomposes a question, searches subqueries, validates and reranks sources, fetches pages, and synthesizes a report with citations. Its depth presets allow increasingly large source and time budgets ([research configuration](https://github.com/KnockOutEZ/wigolo/blob/c0afc8354e776e30dc4e03a53648670856b15156/src/research/pipeline.ts#L32-L56)). Synthesis can use MCP sampling, configured local/cloud LLMs, or a deterministic brief fallback ([synthesis ladder](https://github.com/KnockOutEZ/wigolo/blob/c0afc8354e776e30dc4e03a53648670856b15156/src/research/pipeline.ts#L270-L353)). Configured cloud synthesis sends query/source material to that provider and changes the privacy boundary.

The server gives research only its legacy Bing and DuckDuckGo engine array, while normal `search` uses the core provider and vertical orchestration ([server wiring](https://github.com/KnockOutEZ/wigolo/blob/c0afc8354e776e30dc4e03a53648670856b15156/src/server.ts#L120-L149), [research engine loop](https://github.com/KnockOutEZ/wigolo/blob/c0afc8354e776e30dc4e03a53648670856b15156/src/research/pipeline.ts#L101-L130)). Research quality and fragility therefore cannot be inferred from normal search behavior.

## Result And Citation Shape

Wigolo has the stronger internal data model. Search items include title, URL, snippet, optional hydrated markdown, fetch failures, score and freshness; top-level output can include engine outcomes, warnings, timings, query understanding, images and health ([search output types](https://github.com/KnockOutEZ/wigolo/blob/c0afc8354e776e30dc4e03a53648670856b15156/src/types.ts#L362-L549)). Evidence records can contain URL, title, section, excerpt, score, citation ID and source span, while research returns report text, citations, sources, subqueries, rejected evidence and timing ([evidence and research types](https://github.com/KnockOutEZ/wigolo/blob/c0afc8354e776e30dc4e03a53648670856b15156/src/types.ts#L580-L724)).

That is richer and more diagnosable than the current Exa adapter's text snippets and source list. It is still not a Telefire contract: MCP wraps the JSON as text, and evidence structure does not itself prove that excerpts support generated claims. Citation URL validity, excerpt fidelity, source/index alignment, and synthesized-claim support need an explicit benchmark before replacement.

## Security And SSRF

### Positive Controls

- HTTP and TLS tiers use manual redirect handling and call the URL guard again on each hop ([HTTP redirects](https://github.com/KnockOutEZ/wigolo/blob/c0afc8354e776e30dc4e03a53648670856b15156/src/fetch/http-client.ts#L144-L247)).
- Non-loopback daemon binding requires a token; server code has request/deadline/concurrency controls.
- The security policy explicitly includes SSRF, credential handling, and fetched-content attacks in scope ([`SECURITY.md`](https://github.com/KnockOutEZ/wigolo/blob/c0afc8354e776e30dc4e03a53648670856b15156/SECURITY.md#L21-L29)).
- Telefire could add a stronger network sandbox independently of application code.

### Blocking Gaps For Telefire

1. **No DNS resolution in the guard.** Wigolo's source explicitly says it checks URL strings only and that DNS rebinding is out of scope ([`src/watch/ssrf.ts`](https://github.com/KnockOutEZ/wigolo/blob/c0afc8354e776e30dc4e03a53648670856b15156/src/watch/ssrf.ts#L1-L18)). It blocks literal private/metadata targets but intentionally permits loopback for local use ([guard implementation](https://github.com/KnockOutEZ/wigolo/blob/c0afc8354e776e30dc4e03a53648670856b15156/src/watch/ssrf.ts#L194-L349)). The REST target guard likewise disclaims DNS-rebinding protection ([`target-guard.ts`](https://github.com/KnockOutEZ/wigolo/blob/c0afc8354e776e30dc4e03a53648670856b15156/src/daemon/rest/target-guard.ts)).
2. **Browser redirects and subresources are not equivalently guarded.** Playwright calls `page.goto`, which auto-follows navigation redirects ([`playwright-tier.ts`](https://github.com/KnockOutEZ/wigolo/blob/c0afc8354e776e30dc4e03a53648670856b15156/src/fetch/playwright-tier.ts#L65-L173), [`browser-pool.ts`](https://github.com/KnockOutEZ/wigolo/blob/c0afc8354e776e30dc4e03a53648670856b15156/src/fetch/browser-pool.ts#L549-L569)). Source inspection found no page/context route interception, DNS-resolution check, or final-URL revalidation equivalent to Telefire's guard. Browser subresource requests therefore also lack that application-level policy.
3. **Secondary fetch paths need validation.** Search content hydration passes engine-provided result URLs to the router ([`content-fetch.ts`](https://github.com/KnockOutEZ/wigolo/blob/c0afc8354e776e30dc4e03a53648670856b15156/src/search/content-fetch.ts#L151-L185)); research fetches selected source URLs through the router as well ([research fetch path](https://github.com/KnockOutEZ/wigolo/blob/c0afc8354e776e30dc4e03a53648670856b15156/src/research/pipeline.ts#L392-L403)). No equivalent initial guard was found at those call sites. The search end-to-end test deliberately hydrates `127.0.0.1` results, confirming that local result hydration is supported behavior rather than Telefire's public-only policy ([E2E test](https://github.com/KnockOutEZ/wigolo/blob/c0afc8354e776e30dc4e03a53648670856b15156/tests/e2e/search-tool.test.ts#L64-L155)).
4. **The browser and content-hydration defaults multiply exposure.** Default balanced search fetches result pages rather than returning only search metadata.

These are source findings, not claims of demonstrated exploitation. They are sufficient to reject direct deployment inside Telefire's private network. Any pilot needs a network-level egress proxy or namespace that blocks private, reserved, metadata, link-local and loopback destinations after DNS resolution, across redirects and browser subresources, even if Wigolo's application guard fails.

## Caching And Privacy

Wigolo defaults to a persistent data directory and SQLite cache. Search entries default to one day and fetched content to seven days ([cache configuration](https://github.com/KnockOutEZ/wigolo/blob/c0afc8354e776e30dc4e03a53648670856b15156/src/config.ts#L311-L345)). The database uses WAL and owner-only file permissions, which is positive ([database setup](https://github.com/KnockOutEZ/wigolo/blob/c0afc8354e776e30dc4e03a53648670856b15156/src/cache/db.ts#L63-L209)). It stores plaintext search queries/results and, for fetched pages, URLs, extracted markdown, raw HTML, metadata, links and images.

The content cache lookup is URL-based ([cache store](https://github.com/KnockOutEZ/wigolo/blob/c0afc8354e776e30dc4e03a53648670856b15156/src/cache/store.ts#L77-L126)). Fetch accepts auth and custom headers, but the cache read occurs by URL and the resulting response is cached without those inputs as key dimensions ([fetch cache path](https://github.com/KnockOutEZ/wigolo/blob/c0afc8354e776e30dc4e03a53648670856b15156/src/tools/fetch.ts#L150-L289)). In a single-user local process this may match the intended trust model. In a shared Telefire service it could return one caller's authenticated/header-dependent representation to another caller requesting the same URL. No exploit was attempted, but this is a deployment blocker unless authenticated/header/action fetches are disabled and state is isolated per trust domain.

There is no tenant dimension in the cache schema. The cache tool can enumerate or return stored markdown, so it should not be exposed to Telefire's agent in a pilot ([cache tool](https://github.com/KnockOutEZ/wigolo/blob/c0afc8354e776e30dc4e03a53648670856b15156/src/tools/cache.ts#L123-L143)).

"Local-first" does not mean no egress. Queries go to configured public engines; page URLs and headers go to target sites; optional LLM research/answer paths send prompts and source text to the chosen provider. Telemetry is disabled by default and otherwise writes local NDJSON, with an optional remote endpoint ([telemetry implementation](https://github.com/KnockOutEZ/wigolo/blob/c0afc8354e776e30dc4e03a53648670856b15156/src/cli/telemetry.ts#L1-L71)). Logs include operational fields such as queries and URLs and can therefore contain user data ([logger](https://github.com/KnockOutEZ/wigolo/blob/c0afc8354e776e30dc4e03a53648670856b15156/src/logger.ts#L41-L64)). A pilot should use sanitized queries, telemetry off, cloud LLMs off, short retention, and an ephemeral data directory.

## Dependencies And Deployment

Wigolo v0.2.0 declares 28 direct runtime dependencies plus two optional dependencies ([manifest](https://github.com/KnockOutEZ/wigolo/blob/c0afc8354e776e30dc4e03a53648670856b15156/package.json#L96-L150)). The set includes:

- Playwright and browser system libraries;
- Hugging Face Transformers and a reranking model;
- FastEmbed and embedding model state;
- `better-sqlite3` and `sqlite-vec` native modules;
- multiple cloud LLM SDKs;
- MCP, DOM, extraction, PDF and terminal UI stacks; and
- optional native keyring and TLS-impersonation modules.

This is substantially larger than Telefire's current agent dependency and runtime surface. It increases install time, native-platform risk, model/browser download failure modes, patching burden, supply-chain exposure, memory use and image size.

The upstream Dockerfile installs browser libraries, retains an approximately 750 MB `node_modules` layer by its own comment, and runs as the `node` user ([`Dockerfile`](https://github.com/KnockOutEZ/wigolo/blob/c0afc8354e776e30dc4e03a53648670856b15156/Dockerfile#L15-L79)). The documented deployment needs a writable data volume and allows a long health-start period ([compose file](https://github.com/KnockOutEZ/wigolo/blob/c0afc8354e776e30dc4e03a53648670856b15156/docker-compose.yml#L17-L58)). Telefire would need a separate hardened sidecar with writable state, controlled egress, resource limits, health checks and lifecycle handling; embedding and browser components initialize lazily, so readiness alone may not represent first-request readiness.

### Locally Verified Audit Result

Running `npm audit --omit=dev --json` against the v0.2.0 lockfile reported **five production advisories**:

- high: `fastembed` via `tar`;
- high: `protobufjs`;
- high: `tar`;
- high: `ws`; and
- moderate: `ajv`.

This assessment did not determine whether the affected code is reachable in Wigolo's Telefire-relevant paths or exploitable under the proposed deployment. The result calls for advisory-by-advisory version and reachability triage, not a claim that Wigolo is exploitable.

### Locally Verified Installation And Timing

A published `npx wigolo@0.2.0` cold search succeeded:

| Observation | Result |
|---|---:|
| Cold wall time, including package installation | approximately 19.8 s |
| Wigolo-reported internal cold search time | 2.947 s |
| Fresh isolated `HOME`, total disk | 1.6 GB |
| NPM cache/install portion | 1.5 GB |
| Wigolo model/state portion | 88 MB |
| Warm second CLI wall time | approximately 2.9 s |
| Wigolo-reported internal warm search time | 1.184 s |

The direct A/B harness measured the current Exa path at approximately 1.8 s wall time and the warm Wigolo CLI at approximately 2.8 s. These are different invocation and startup paths, so they do **not** establish a firm latency advantage for either system. A sidecar benchmark must separate process startup, model warmup, engine latency, hydration, cache state and adapter overhead.

## Observability

Wigolo exposes useful request-level data: timing, cache state, fetch tier, engine result counts, engine latency, breaker state, warnings and optional engine outcomes ([telemetry types](https://github.com/KnockOutEZ/wigolo/blob/c0afc8354e776e30dc4e03a53648670856b15156/src/types.ts#L362-L405)). It has structured/text logs and a health endpoint. These are meaningful positives for diagnosis.

No OpenTelemetry tracing or Prometheus-style metric exporter was found in the pinned source. Wigolo also has no native Telefire run ID or audit correlation. A production adapter would need to propagate correlation IDs and emit metrics for request outcome, upstream engine outcome, cache hit, fetch tier, browser use, rerank/model loading, p50/p95 latency, abandoned work after deadlines, SQLite growth, memory, egress count and dependency/model download failures. Telefire's existing tool audit should remain authoritative.

## Testing And Quality Evidence

The main CI result is positive. The latest full suite at the pinned commit passed **7,733 tests** across 671 files, with 11 skipped and 7 todo; lint/build/unit jobs also passed on Linux, macOS and Windows ([CI run](https://github.com/KnockOutEZ/wigolo/actions/runs/29582794669)). This is substantial correctness coverage.

It is not equivalent to live search-quality evidence:

- Search E2E tests use mock engines and local in-process pages, so they do not prove current Bing/DuckDuckGo selectors, IP reputation, multilingual ranking, freshness or live citation quality ([search E2E source](https://github.com/KnockOutEZ/wigolo/blob/c0afc8354e776e30dc4e03a53648670856b15156/tests/e2e/search-tool.test.ts)).
- The public [Search Benchmark workflow history](https://github.com/KnockOutEZ/wigolo/actions/workflows/search-benchmark.yml) shows all 13 scheduled runs from 2026-04-20 through 2026-07-13 failed.
- In the [latest failed run](https://github.com/KnockOutEZ/wigolo/actions/runs/29242012684), `npm run bench:search` executed `tsx benchmarks/search/runner.ts`, produced no artifact, and the MRR step failed with `MODULE_NOT_FOUND` for `search-benchmark.json`.
- The package script directly executes that module ([`package.json`](https://github.com/KnockOutEZ/wigolo/blob/c0afc8354e776e30dc4e03a53648670856b15156/package.json#L45-L63)), but the v0.2.0 runner only exports functions and has no entrypoint that calls `runSearchBenchmark` ([runner](https://github.com/KnockOutEZ/wigolo/blob/c0afc8354e776e30dc4e03a53648670856b15156/benchmarks/search/runner.ts#L1-L214)). Although the latest scheduled run predates v0.2.0, the defect remains in the pinned v0.2.0 source.
- Its fixture has only 21 queries, all English ([query fixture](https://github.com/KnockOutEZ/wigolo/blob/c0afc8354e776e30dc4e03a53648670856b15156/benchmarks/search/fixtures/queries.json)). It would not cover Telefire's demonstrated Chinese risk even if the workflow ran.

README benchmark claims should therefore not be treated as current independent evidence. The project has strong automated correctness coverage, but no functioning scheduled signal for live, multilingual search relevance at this snapshot.

## Operational Fragility

Likely operational failure modes are broader than the current Exa-only path:

- HTML selector and anti-bot breakage across Bing, DuckDuckGo and Mojeek;
- upstream public API limits and optional credential failures;
- query fan-out from variants, multiple engines, retries, recovery waves, category fallback and content hydration;
- soft-deadlined requests continuing after the caller stops waiting;
- English-only default reranking and a nonfunctional model configuration knob;
- stale hardcoded documentation catalogs;
- model download, ONNX/native module, SQLite migration/locking and browser installation failures;
- writable cache/model volume growth and retention of sensitive query/page data;
- browser memory and process pressure;
- unbounded upstream response reads; and
- rapid package/contract drift during a very young release cycle.

The current Exa path has its own concentrated external-service risk: availability, policy, ranking and the continued availability of a zero-key MCP endpoint are outside Telefire's control. That is a reason to evaluate an alternative, not enough to accept Wigolo's larger current risk surface.

## Recommendation Matrix

| Option | Recommendation | Reason |
|---|---|---|
| **Keep current path** | **Yes, production default** | It matches Telefire's narrow contract, current security policy and hardened runtime, and won both official-document spot checks. |
| **Pilot Wigolo** | **Yes, gated shadow evaluation** | Richer evidence, zero-key metasearch, diagnostics and broader capabilities justify gathering representative data without routing user traffic. |
| **Replace now** | **No** | Multilingual reranker mismatch, SSRF/browser gaps, shared-cache risk, integration cost, AGPL review, dependency advisories, young release maturity and missing quality benchmark are unresolved. |

## Proposed Low-Risk Evaluation

### 1. Contain The Candidate

- Pin exactly `wigolo@0.2.0` or an immutable image digest. Do not use unpinned `npx wigolo` instructions.
- Run it outside Telefire in an ephemeral container/namespace with no route to Telefire, memory, host, metadata, loopback services or private networks.
- Enforce DNS-aware egress at the network layer for HTTP, TLS and browser traffic, including redirects and subresources.
- Use a throwaway `HOME`/data volume per batch; wipe it after collection.
- Disable telemetry, plugins, cloud LLMs, auth, custom headers, browser actions and screenshots.
- Expose only `search` through a temporary harness. Do not expose `fetch`, `crawl`, `research`, `cache`, `agent`, `watch` or `diff` to Telefire.
- Begin with `include_content: false` and `search_depth: fast`; do not test balanced hydration until the network security gate passes.

### 2. Triage Before Any Shared Or Production Trial

- Resolve or document reachability and remediation for all five production audit advisories.
- Obtain AGPL deployment/modification guidance.
- Require an upstream fix for `WIGOLO_RERANKER_MODEL`, or evaluate an isolated experimental patch only after licensing review. A replacement candidate must support a proven multilingual model or language-aware routing. `WIGOLO_RERANKER=none` is a diagnostic mode, not the proposed production fix.
- Define process memory, disk, request, upstream-call, concurrency and timeout budgets from Telefire's actual production baseline.

### 3. Build A Representative Shadow Corpus

Use at least 100 sanitized, judged queries sampled from Telefire's real workload categories, with sufficient per-segment counts for:

- English and Chinese;
- explicit official-document intent;
- current/version-specific technical documentation;
- news/freshness;
- niche and ambiguous questions;
- domain and recency filters; and
- queries where no good result exists.

Run current Exa, Wigolo default, Wigolo no-rerank diagnostic, and any genuinely configurable multilingual candidate under controlled warm/cold/cache states. Randomize order and repeat queries to expose engine variance.

Measure top-1/top-3/top-5 judged relevance, reciprocal rank, official-domain rank, freshness, duplicate rate, empty/error rate, citation URL validity, excerpt fidelity, unsupported synthesis, p50/p95 wall time, internal stage time, RSS, disk growth, egress count and upstream request amplification. Keep the two existing queries as regression cases, not as the benchmark.

### 4. Run A Hostile-Network Suite

Cover literal and encoded IPv4/IPv6, credentials, local suffixes, hostnames with private or mixed DNS answers, DNS rebinding, HTTP/TLS/browser redirect chains, browser subresources, search-engine result poisoning, sitemap cross-origin targets, metadata endpoints, large/chunked/slow responses and cancellation. The network sandbox must show zero private/reserved egress even when application checks fail.

Add shared-state tests for two callers using the same URL with different auth/headers and for cache enumeration. A shared deployment requires zero cross-caller content disclosure.

### 5. Acceptance Gates

Do not consider replacement unless all of the following hold:

- no material regression against Exa in official-source ranking or judged top-k quality for both English and Chinese segments;
- a configurable multilingual reranker or validated language-aware route, with the documented setting proven effective;
- zero private/reserved network egress across the hostile suite;
- zero authenticated/header-dependent cache leakage across trust domains;
- citation URLs and excerpts meet a predeclared fidelity threshold and synthesized claims are auditable;
- production advisories are remediated or have reviewed, documented non-reachability;
- latency, resource, error and upstream amplification budgets pass under cold, warm, burst and multi-day soak conditions;
- licensing and privacy review approve the exact architecture; and
- rollback remains an immediate switch to the current Exa path.

## Uncertainties

- The direct quality evidence is only two queries plus a controlled reranker toggle. It is diagnostic, not statistically representative.
- Exa and Wigolo timing used different invocation/startup paths; no firm latency comparison is supported.
- Advisory exploitability and code-path reachability were not assessed.
- SSRF findings are static source findings; no exploit was attempted. Conversely, static absence of a guard does not prove every path is exploitable.
- Telefire's current DNS guard is a preflight and does not pin the subsequent connection's DNS result.
- No Telefire production query corpus, quality labels, latency distribution, cost profile or resource budget was available.
- Wigolo is changing rapidly; conclusions apply to commit `c0afc8354e776e30dc4e03a53648670856b15156`, not later releases.
- Exa's zero-key MCP availability, service policy and long-term operational terms were not evaluated here.
- License analysis requires counsel.

## Bottom Line

Wigolo is a credible **research candidate**, especially if Telefire values local metasearch, richer evidence and reduced dependence on one search provider. Wigolo v0.2.0 is not a credible **production replacement** yet. The current version loses important official-source ranking on the observed Chinese workload because of a verified hardcoded English reranker, cannot select the documented multilingual model, weakens Telefire's SSRF boundary, introduces shared-cache confidentiality concerns, and adds a much larger and younger operational surface without a functioning search-quality benchmark.

Keep Exa as the default. Run only the isolated, search-only shadow evaluation above, and revisit replacement after the multilingual, security, cache, dependency, license and quality gates are met.

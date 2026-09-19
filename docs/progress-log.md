# Progress Log

What happened each round, and what got adjusted along the way. Commit hashes point at the actual diff; this file is the narrative around it.

## 2026-09-17 08:27 — `6f0f2c0` Domain model and ADRs

Grilled the original plan (`rag-production-plan.txt`) into concrete decisions via `/grill-with-docs`. Wrote `CONTEXT.md` (glossary: Node, Document, ACL Group, Acting Role, Abstention, Guardrail, Golden Set, Eval Gate, Source Conflict, Dev/Validation Generator) and 5 ADRs (LlamaIndex over LangChain, pgvector over dedicated vector DB, resource-constrained local inference, Dev/Validation Generator split, ACL as metadata-filter simulation).

## 2026-09-17 08:30 — `ef3ee5d` Agent skills config

Ran `/setup-matt-pocock-skills`: GitHub Issues as the tracker, default triage labels, single-context domain docs.

## 2026-09-17 09:08 — `881e079` Ticket #2: Bootstrap infra

Docker Compose (Postgres+pgvector) + FastAPI `/health`.

**Adjustment:** Ollama removed from Docker Compose entirely — it's already running natively on the host, and Docker Desktop's Linux VM has no Metal GPU passthrough, so containerizing it would have lost the acceleration ADR-0003 relies on.

**Code-review fixes:** added a connect timeout, `/health` returns 503 (not a bare 500) when Postgres is down, added a Postgres healthcheck so `docker compose up -d --wait` isn't a race on a fresh volume.

## 2026-09-17 09:58 — `33e55af` Ticket #3: Ingest a Document

`POST /documents` → PyMuPDF parse → LlamaIndex `HierarchicalNodeParser` → bge-m3 embed → hand-written Postgres schema (not LlamaIndex's PGVectorStore, to keep it aligned with `CONTEXT.md` vocabulary).

**Code-review fixes:** store the real filename instead of the ephemeral temp path, return 400 instead of 500 on unparseable uploads, fixed a `DATABASE_URL` import that broke the monkeypatch convention from #2, batched embedding calls, batched node inserts/updates via `executemany`, streamed the upload instead of double-buffering it, added test cleanup between runs.

## 2026-09-17 10:17 — `31aa00b` Ticket #4: Answer a query

`POST /query` → embed → dense retrieval (pgvector cosine) → expand to parent context → Dev Generator (llama3.1:8b) → answer + citations + Abstention.

**Design decision:** Abstention signalled via a plain-text marker string, not JSON — the Dev Generator can't reliably produce structured output (this is why ADR-0004 exists). Documented that the Validation Generator (Claude) should switch to JSON once it's wired in.

**Code-review fixes:** dedupe retrieved context by parent (siblings were repeating the same passage), return a clean fixed message on Abstention instead of raw marker-laden text, consistent try/except-to-error-response pattern across endpoints, extracted the embedding call shared with `ingest.py` into `app/ollama.py`.

## 2026-09-17 10:31 — `80e049d` Fixture bug found via manual testing

Manually tested the MVP against real Kaggle ground-truth data (converted a CSV row's context into a PDF). Found a real Abstention bug — traced it to the *test PDF generation script*, not the pipeline: `page.insert_text()` silently drops text that overflows the page. The committed `tests/fixtures/sample.pdf` had the same flaw (1898 of 2982 source characters). Fixed by switching to `insert_textbox` with proper pagination.

## 2026-09-17 11:03 — `342978c` Ticket #6: Corpus + Golden Set

Kaggle CSV (`train.csv`) → 51 PDFs (one per unique context) + `golden_set.json` (200 entries).

**Adjustment:** switched `insert_textbox` → `insert_htmlbox` mid-ticket — `insert_textbox` turned out to have its own bug, silently mis-rendering characters outside its base font's encoding (an em-dash became `?`). `insert_htmlbox` renders Unicode correctly and auto-shrinks to fit any length on one page.

**Code-review fixes:** the round-trip check's first version stripped *all* whitespace to tolerate line-wrap artifacts, which also would have hidden a real word-merge bug (e.g. "New York" → "NewYork") — tightened to only collapse whitespace inserted after hyphens. Added explicit UTF-8 encoding (118/200 rows are non-ASCII), fixed a resource leak, committed `train.csv` itself since the build script now hard-depends on it.

## 2026-09-17 11:37 — `43173c6` Ticket #7: Eval harness

`run_eval()` resets the DB, ingests the corpus, answers every Golden Set item, scores with Ragas (faithfulness, answer relevancy, context precision) using the Dev Generator as judge via LangChain/Ollama wrappers.

**Adjustment:** had to pin `ragas==0.1.21` + `langchain-community==0.2.19` — the latest Ragas release has a broken import, and its own transitive pins don't tolerate current `langchain-core`.

**Found:** `faithfulness` consistently fails to parse with the Dev Generator (0/3, JSON mode forced or not) — same root cause as the Abstention marker-string decision. Left as `null` rather than dropped; documented in ADR-0004 as a known limitation to revisit with the Validation Generator.

**Code-review fixes:** fixed a real scoring bug — the eval harness was re-deriving context text from the DB by reading the cited *child* Node's own content, not the *parent's* wider content the Generator actually saw. Fixed by having `answer_query` return the actual context text it used (new `contexts` field on `Answer`), removing the eval-only DB lookup entirely. Also: shared `db.reset()` (was duplicated in `conftest.py`), derived Ragas metric names from the metric objects instead of a hand-maintained parallel list, guarded aggregation against an empty item list, added per-item error handling so one failed question doesn't discard a whole run.

## 2026-09-17 12:23 — `b9c2d20` Concurrency fix

First full 200-question eval run hit rampant `TimeoutError`s — Ragas defaults to 16 concurrent calls, which overwhelmed the single local Ollama instance. Fixed with `RunConfig(max_workers=2)`.

## 2026-09-17 14:54 — First full baseline: `eval/reports/2026-09-17T07:54:09.494971+00:00.json`

Full 200-question Golden Set run, post-concurrency-fix, no errors. This is the MVP's baseline (dense-only retrieval, no hybrid search/reranker/guardrail/ACL yet) — the "before" number for whatever gets built next.

**Overall:** faithfulness 0.80 (computed for only 34% of answered items — see ADR-0004 update below), answer_relevancy 0.66, context_precision 0.82, abstention_rate 22%, abstention_correctness 83%.

**Found:**
- `summary`-category questions score worst on context_precision (0.39) — dense retrieval against the question's own embedding doesn't suit "summarize this document" questions, whose relevant content is spread across the whole document rather than concentrated near a semantically-similar passage.
- `not_found_classification` abstention_correctness is 75%, not 100% — a quarter of genuinely unanswerable questions get answered anyway instead of triggering Abstention (hallucination risk).
- `math_basic` has the lowest faithfulness (0.47) and second-lowest abstention_correctness (70%).
- `faithfulness` computed for only 34% of answered items in practice (updated ADR-0004 with the real rate — the earlier note said "consistently fails," which undersold how often it actually does work).

**Decision:** `answer_relevancy` (0.66) and `context_precision` (weak specifically on `summary`) are the two reliably-computed metrics with the most room to improve, and both are exactly what hybrid search (BM25+RRF) and a reranker are supposed to help with — this points at hybrid search/reranker as the next feature, confirming rather than overriding the roadmap's existing next-up item.

## 2026-09-17 — Manual error analysis of the baseline run

Asked "how many questions did it actually get right" — a crude substring-match heuristic first said 50-54%, but manual review of every "wrong" item showed that heuristic was badly undercounting: most flagged failures were semantically correct answers in a different phrasing/format (e.g. "438,000" vs "438,000 jobs", "Two years" vs "Two-year term") — exactly why Ragas's semantic metrics (not string matching) are the numbers to trust, not a hand-rolled accuracy count.

**Real bugs found by reading through actual failures, not the heuristic:**
- **Cross-document contamination**: retrieval pulls top-5 Nodes from the *entire* 51-document corpus with no per-document scoping, so answers sometimes blend content from the wrong document even when the right document is also among the retrieved Nodes (e.g. a question about political appointees answered with names from an unrelated synthetic "rank people by age" document). Broader than the ~16/200 cases where *no* correct-document Node was retrieved at all — some "same document was cited" cases still leaked content from a co-retrieved wrong document into the generated answer.
- **Wrong row/column in tabular data**: e.g. "cost of goods sold in 2019" answered with a different year's figure from the same table.
- **Reasoning errors from the Dev Generator**: a self-contradictory price comparison (stated "Yes" while its own cited numbers said the opposite) and a wrong age calculation — consistent with `math_basic` already scoring lowest on faithfulness.

**Decision:** these three findings (contamination, table precision, reasoning) point at a reranker as the more targeted next feature over plain hybrid search — a reranker can suppress co-retrieved wrong-document Nodes and improve which specific chunk actually gets used, which is closer to the actual failure modes found than adding BM25 alone would address.

## 2026-09-17 — `0cb2701` Ticket #9: Reranker component

`rerank(query, candidates)` in a new `app/reranker.py`, cross-encoding with BGE-reranker-v2-m3 (ONNX-quantized) on CPU/Metal per ADR-0003. Standalone — not wired into the query pipeline yet (ticket #10).

**Research before coding:** found a working pre-quantized ONNX build (`EmbeddedLLM/bge-reranker-v2-m3-onnx-o3-cpu`) rather than exporting one ourselves. Hit one real gotcha: HuggingFace's default symlink cache breaks onnxruntime's external-data path validation (`External data path escapes model directory`) — fixed by downloading via `snapshot_download(local_dir=...)` into a real directory instead of the default cache. Measured latency for real before committing to the approach: ~3s one-time load, ~1.4s to rerank 20 candidates — within ADR-0003's own expectations.

**Code-review fixes:** skip the network call entirely once the model is already cached locally (it was hitting the Hub every process start even with a full local cache — breaks offline use); pinned the model to an exact commit hash, matching how every other dependency in this repo is pinned; added a lock around the lazy singleton so concurrent callers can't race into duplicate downloads; made the cache path absolute (derived from the module's own file location) instead of relative to the process's working directory; declared `huggingface_hub` as an explicit dependency instead of relying on a transitive pull; batched reranking calls (matching the pattern already established in `app/ollama.py`) instead of one unbounded forward pass; removed a dead code branch that could never execute given how the tensor squeeze was already written.

## 2026-09-17 — Ticket #10: reranker does not reliably fix keyword-stuffed contamination

Before wiring the reranker into retrieval, tried to reconstruct spec #8's planned regression test: a case where dense embedding confuses two Documents and reranking fixes it. Built several synthetic cases (a correct Document about "Alpha Corp" crowded out of the dense top-5 by distractor Documents that repeat "Alpha Corp" in a different context) — **the reranker got fooled by the same keyword-stuffing that fools dense embedding**, ranking the distractors above the correct Document in every variant tried, including a version modeled closely on the real "list of people mentioned" contamination case found in the baseline eval (corpus 03 vs corpus 40).

**This means the cross-encoder reranker is not a guaranteed fix for cross-document contamination** — it helps with genuine relevance judgment but isn't immune to lexical-overlap adversarial content, which is exactly the shape a lot of the baseline's contamination took (documents that mention the target entity's name without actually answering about it).

**Decision:** scoped ticket #10's regression test down to verifying the *wiring* is correct (over-fetch happens, `rerank()` is actually called with a wider pool than `TOP_K`, and the final result respects rerank's output order/truncation) using a mocked `rerank()`, rather than asserting a specific real-world semantic outcome this model doesn't reliably produce. Whether reranking measurably helps at all is a question for the next full Golden Set eval run, not something to fake confidence about via a cherry-picked passing example.

## 2026-09-17 — `643bee1` Ticket #12: Hybrid retrieval (dense + BM25)

`_fused_retrieve()` fuses the existing dense pgvector search with a fresh-built LlamaIndex `BM25Retriever` via `QueryFusionRetriever` (Reciprocal Rank Fusion). Bundled with the reranker (spec #8) intentionally, to measure both retrieval-quality changes in one eval run instead of two ~1.5-2hr runs — an explicit efficiency trade-off, accepting that the next eval can't cleanly attribute which of the two helped how much.

**Same lesson as the reranker, again:** tried to construct an "exact identifier that dense misses" test case (a made-up purchase-order code) to prove hybrid search's classic advantage — bge-m3 found it correctly every time, even without the code repeated in the query. Scoped the regression test down to wiring-correctness (same approach as ticket #10) rather than a semantic-superiority claim that, a third time now, wasn't reliably demonstrable in a small synthetic test.

**Architecture note:** chose to actually use LlamaIndex's `QueryFusionRetriever`/`BM25Retriever` (matching what ADR-0001 originally said LlamaIndex was chosen for) rather than hand-rolling BM25 via Postgres full-text search, which would have been simpler but left ADR-0001's stated reasoning inaccurate.

**Code-review fixes (all verified by direct reproduction, not just inspection):** `BM25Retriever.from_defaults()` throws on an empty corpus — previously dense-only degraded gracefully to an empty result and a normal Abstention; fixed by returning `[]` early instead of letting the exception surface as an unhandled 503. The parent-id metadata carried through for dedup was leaking into the actual BM25/embedding-indexed text (via `TextNode`'s default content rendering), so a query containing a hex-like word could spuriously match a node via its parent's UUID rather than its real content — fixed with `excluded_embed_metadata_keys`/`excluded_llm_metadata_keys`. Also: unified the two retrievers onto one Postgres connection (were previously racing on separate connections against concurrent ingestion), de-duplicated their shared SQL, and disabled `QueryFusionRetriever`'s async fan-out since neither retriever is actually async — it was paying event-loop overhead for no concurrency benefit.

**Next:** run the full 200-question Golden Set eval with both reranker + hybrid search shipped, compare against the `2026-09-17T07:54:09` baseline report.

## 2026-09-18 — `daa0e10` Ticket #16: ingest `.csv` files directly, no PDF conversion

`ingest_document()` dispatches extraction by file extension (`.pdf` unchanged via PyMuPDF, `.csv` via a new `csv.DictReader`-based extractor); `POST /documents` preserves the uploaded file's real extension instead of hardcoding `.pdf`. Scope was deliberately narrowed to just these two formats — LlamaIndex's `SimpleDirectoryReader` supports several more (docx, epub, hwp, ipynb, images, audio/video) out of the box, but none of them have any real use case in this project (the target corpus, PTT One Report, is always PDF), so adding them would be untested surface area with no one to use it.

**Code-review fixes:** `csv.DictReader` fills missing columns with `None` and collects extra columns under a `None` key, which was getting stringified into the indexed content as literal `"key: None"` garbage — filtered out. A later review pass also caught the same bug's other shape: a fully-present-but-blank field (e.g. a CSV row like `,,`) is an empty string, not `None`, and survived the first fix's `is not None` check — tightened to a truthiness check so both cases are excluded.

## 2026-09-18 — `e1ca576` Ticket #14: tag a Document with an ACL Group at ingest time

`documents.acl_group` added (nullable, `NULL` = public, matching every Document ingested so far — no migration needed for existing rows). `ingest_document()` accepts and stores it; `POST /documents` accepts an optional `acl_group` form field. This ticket only makes tagging possible — every query still sees every Document regardless of `acl_group`; retrieval-side filtering by Acting Role is ticket #15, separate on purpose (ACL Group and Acting Role are two different concepts per ADR-0005, and the split keeps each ticket a demoable vertical slice).

**Code-review fixes:** a blank `acl_group` form field (e.g. an HTML form left empty) was being stored as the literal empty string instead of `NULL`, which would have made that Document invisible to *every* Acting Role once #15 ships (matches neither the `NULL`-is-public case nor any real group) — normalized empty string to `None` before it reaches `ingest_document`. Also found and fixed: `docker/postgres/init.sql` only added the `acl_group` column via `CREATE TABLE IF NOT EXISTS`, a no-op on any Postgres volume that predates this change (dev machines, CI caches) — every `/documents` upload would 400 with `UndefinedColumn` hidden behind the broad exception handler, and a from-scratch `run_eval` would crash before processing a single item. Fixed with an idempotent `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` run unconditionally after the `CREATE TABLE`.

## 2026-09-18 — `2026-09-18T03:51:57` Hybrid + reranker eval run, compared against baseline

Full 200-question Golden Set run with hybrid retrieval (ticket #12) and reranker (tickets #9/#10) both shipped, no errors. Took ~2h59m end to end (dominated by the Ragas scoring phase, not the answer loop — see `eval/run_eval.py`'s new per-item progress printing added this round, so the next run's timing is actually observable instead of opaque).

**Overall:** faithfulness 0.79 (vs 0.80 baseline, flat), answer_relevancy 0.64 (vs 0.66, slightly down), context_precision 0.85 (vs 0.82, up), abstention_rate 24.5% (vs 22%), abstention_correctness 82.5% (vs 83%, flat).

**The one finding that matters most: `summary` category context_precision got *worse*, not better** — 0.37 vs the baseline's already-weak 0.39. Every other category improved (core 0.91→0.93, boolean 0.87→0.90, complex_qa 0.81→0.86, math_basic 0.71→0.79, not_found_classification 0.57→0.67), which is what pulled the *overall* context_precision average up despite the actual target failure mode being untouched. Hybrid search and reranking made retrieval better where it was already working, not where it was weakest — consistent with the reasoning already recorded when `summary` was first diagnosed (dense-similarity-to-one-chunk doesn't suit "summarize the whole document" queries, and neither BM25 fusion nor reranking changes *which* chunks get considered at that scale).

**Also worse:** `math_basic` faithfulness dropped 0.47→0.38, and `boolean` abstention_correctness dropped 75%→60%. Not investigated further this round — flagged here so a future regression check has a baseline to compare against.

**Decision:** hybrid search + reranker are not the fix for the project's most-diagnosed weakness. The next retrieval change under consideration is *document-scoped retrieval* (narrow to the likely-relevant Document before chunk-level search, rather than searching the whole corpus at once) — reasoning and cost/latency notes captured in `maybe.txt` (not yet a spec).

Report: `eval/reports/2026-09-18T03:51:57.398982+00:00.json`.

## 2026-09-18 — Manual error analysis of the hybrid + reranker run

Same method as the baseline's manual analysis: judge every answered item by hand/LLM against `expected_answer`, ignoring phrasing/formatting differences, instead of trusting a string-match heuristic. Also, unlike the baseline pass, combined this with the report's abstention data to score all 200 items rather than just the answered ones.

**Combined score across all 200 items: 141/200 (70.5%)** — a fully-correct outcome is either a substantively correct answer or a correct decision to abstain. Breaks down as: 124 correctly answered, 17 correctly abstained (`not_found_classification`), 24 answered incorrectly, 3 answered when should have abstained, and **32 abstained when the question was actually answerable** — over-refusal on answerable questions turns out to be a bigger contributor to the combined score than any single content-error pattern.

**Answer-only correctness (151 answered items): 124/151 (82.1%)**, and it's extremely uneven by category — `core` 96%, `boolean`/`complex_qa` 75%, `math_basic` 69%, **`summary` 36%**.

**89% of the 27 wrong answers (24/27) are cross-document contamination** — worse than the baseline's contamination finding, not better, despite the reranker shipping specifically to address it (ticket #8). Two shapes: whole-topic swaps (answer is fluent and internally consistent but about a completely different Document — concentrated almost entirely in `summary`, 9/9 of its wrong items) and single-fact swaps within an otherwise-correct answer (one number/fact from a different corpus, seen across `core`/`boolean`/`math_basic`). The remaining failures: 1 table row/column confusion (same bug shape as the baseline run) and 2 reasoning errors despite citing the correct facts.

Full breakdown, category tables, and illustrative examples: `docs/eval-comparison.md`.

**Decision:** this independently confirms and sharpens the same-day context_precision finding above — `summary`'s problem isn't retrieval precision at the margin, it's near-total topic contamination on broad/listy queries. Strengthens the case for document-scoped retrieval (`maybe.txt`) as the next thing to try, over any further reranker/hybrid tuning.

## 2026-09-18 — Ticket #17: eval corpus generation ingests CSV directly, drops PDF conversion step

`eval/build_corpus.py` no longer renders each Kaggle CSV context to a PDF before ingesting it — it writes a one-column `.csv` artifact per context and ingests that directly via ticket #16's native CSV support. `_write_pdf`/`_verify_roundtrip`/`_normalize` and their two dedicated regression tests are deleted along with the PDF path they existed to protect — CSV round-trips exactly via the stdlib `csv` module, so there's no rendering-lossiness class of bug left to guard against. `golden_set.json`'s shape and `corpus_id` linkage are unchanged.

**Code-review fixes (two real bugs caught by direct reproduction, not just inspection):**
- `_extract_csv_text` was prefixing every single-column CSV's content with its header (`"content: <text>"`), silently drifting the indexed text of all 51 eval-corpus Documents away from their PDF-extracted baseline. Fixed by special-casing the single-column case to return the bare value — a multi-column CSV (the general-purpose ingest path from #16) still gets `key: value` formatting, since that's real structure worth preserving there.
- The real `eval/corpus/` directory still held the old 51 `.pdf` files — never regenerated after `_ingest_corpus`'s glob changed to `*.csv` — so a real `run_eval` invocation would have silently ingested zero Documents and produced a fully-formed but garbage ~100%-abstention report with no exception raised. Regenerated the corpus via the updated `build()` and deleted the stale PDFs.

Also fixed in the same review pass (found while re-reviewing #14's already-shipped code, not new to this diff): `docker/postgres/init.sql`'s comment claimed an `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` "keeps existing volumes in sync," which is false — `docker-entrypoint-initdb.d` never re-runs init.sql against a non-empty data directory, so no SQL written there can ever reach an existing volume. Reverted to the simple inline column definition and pointed the comment at the README's actual documented answer (`docker compose down -v`) instead of claiming a fix that can't work. Also normalized whitespace-only `acl_group`/`acting_role` values to `None` (a single space previously survived the empty-string check added for #14 and would have produced a permanently-unreachable Document).

## 2026-09-18 — Ticket #19: Redis exact-match cache for `answer_query()`

New `app/cache.py` (`get_cached_answer`/`set_cached_answer`/`clear`) sits inside `answer_query()` — the same single seam already used for ACL/abstention/citations — so `/query`, the eval harness, and every existing caller need no changes. Cache key is a SHA-256 hash of `query` + `acting_role`, with Acting Role structurally part of the key rather than an optional extra: research done earlier this session found a real production incident where a semantic cache served one customer's cached response to a different customer's session, and exact-match-with-role-in-the-key avoids that entire failure class by construction — directly relevant now that ACL (#14/#15) exists. `db.reset()` and `ingest_document()` both flush the cache, since either can change what an already-cached answer should be; a Redis outage degrades to a normal (uncached) pipeline run rather than breaking `/query`.

**Code-review fixes (3 of 5 findings; two deferred, see below):**
- `ingest_document()` — reachable via `POST /documents` — didn't invalidate the cache, only `db.reset()` did. Uploading a new Document that would answer an already-cached (possibly abstained) question left the stale cached answer serving for up to the full TTL. Fixed by clearing the cache at the end of `ingest_document()` too.
- The Redis client had no socket timeout, so an unreachable-via-network-hang Redis (dropped packets, not an actively refused connection) would block a request on the OS-level TCP timeout instead of failing fast into the `RedisError` fallback — defeating the graceful-degradation guarantee for exactly the failure mode most likely in a real outage. Fixed with a 1-second `socket_connect_timeout`/`socket_timeout`.
- `json.loads()` on a cached value ran outside the `try/except RedisError` guard, so a corrupted cache entry would raise `JSONDecodeError` uncaught, past `answer_query`, surfacing as a 503 instead of the cache-miss fallback the feature is supposed to guarantee. Moved inside the guard, catching `json.JSONDecodeError` alongside `redis.RedisError`.

**Deferred (real findings, disproportionate to this project's current scale):** `db.reset()`'s truncate-then-clear-cache isn't atomic, so a request in flight across the reset could repopulate the cache with pre-reset data; and there's no request-coalescing lock, so a burst of identical concurrent cache misses each pay the full pipeline cost independently. Both are genuine production concerns but need a distributed lock to fix properly — out of scope for a single-dev/portfolio-stage cache, noted here for whenever real concurrent traffic becomes a thing.

## 2026-09-19 — Ticket #21: document-scoped retrieval

New `_select_documents(query, query_embedding, acting_role, limit)` in `app/query.py` ranks Documents by fusing dense (pgvector, `MIN` distance per Document) and lexical (BM25, same aggregation) signals via hand-rolled Reciprocal Rank Fusion (k=60, matching `QueryFusionRetriever`'s own default), narrows to the top-3, and `_retrieve` scopes the existing chunk-level dense+BM25+rerank+dedup pipeline to only those Documents' Nodes — instead of searching all Nodes across the whole corpus in one pass. Directly targets the manual review's finding (89% of wrong answers were cross-document contamination) at its actual source (wrong Document entering the candidate pool) rather than trying to filter it out after the fact.

Design settled via a grilling session before writing the spec (#20) and ticket (#21): no new schema (ranking computed on the fly from existing `nodes.embedding`/`nodes.content`), hybrid dense+BM25 at the Document-selection stage too (not dense-only — a pure-dense stage would reintroduce the exact keyword-stuffing weakness that made the reranker unreliable, ticket #10), ACL filtering applied at Document-selection as well as chunk-level, `MIN` aggregate (best-matching child Node represents its Document — explicitly accepted as tuned toward factoid questions, may not fully fix `summary`-category contamination on its own), and RRF hand-rolled directly rather than forcing `QueryFusionRetriever` to operate on Documents (would need a synthetic per-Document "Node" concatenating all child content, which changes the ranking semantics away from the agreed `MIN` design). Verification is wiring-level only per this ticket's acceptance criteria; whether this measurably reduces contamination is a separate follow-up eval run, not asserted here.

**Code-review fixes (2 serious, found by direct reproduction — the first would have silently defeated the feature's whole point):**
- The BM25 half of the Document-level RRF fusion used each Document's best-matching Node's *node-level* rank (drawn from the full corpus's node population, e.g. hundreds) directly as its `bm25_rank`, while `dense_rank` was already aggregated to the much smaller Document population. The two rank scales weren't comparable, so BM25's contribution to the fused score was diluted to near-nothing relative to dense — recreating, one level up, the exact dense-alone weakness Document-scoped retrieval was meant to avoid. Fixed by collapsing BM25 results to one best-node-per-Document first, then re-ranking within that Document population before fusing. Added `test_select_documents_gives_real_weight_to_the_bm25_signal` (mirrors the existing chunk-level hybrid test's rare-literal-token pattern) as a regression test — it does not pass against the pre-fix version.
- The BM25 half of Document selection was built from each Node's own raw content instead of `COALESCE(parent.content, node.content)`, the parent-expanded text chunk-level retrieval actually searches — a Document whose distinguishing keywords live only in parent-level context could rank low enough to be excluded before chunk-level retrieval ever ran. Fixed to match `_CANDIDATE_JOIN_SQL`'s existing content source.

**Other fixes:** the ACL predicate was duplicated a third time inline instead of reusing the existing shared constant — extracted to `_ACL_FILTER_SQL`, now referenced by both `_CANDIDATE_JOIN_SQL` and `_select_documents`. `_select_documents`'s two queries ran as separate statements with no snapshot guarantee, so a Document ingested between them could get a real BM25 rank while missing from the dense ranking (or vice versa) — pinned to `REPEATABLE READ`. `_text_node()` gained a generic `metadata` param (was hardcoded to `group_key` only) so `_select_documents` could reuse it instead of duplicating the metadata-exclusion pattern inline. Removed a defensive missing-rank fallback that was dead code once both rankings are guaranteed (by the isolation-level fix) to cover the same Document set.

**Deferred (real, disproportionate to current scale):** `_select_documents` and `_fused_retrieve` each open their own connection and build their own BM25 index from scratch, so every `_retrieve()` call now pays two full-corpus BM25 rebuilds and two connections where there was one before this ticket — invisible at the current corpus size, but worth merging into a single connection/pass if the corpus grows. Noted, not fixed this round.

**Next:** run a fresh Dev Generator Golden Set eval and compare against `docs/eval-comparison.md`'s hybrid+reranker report — this is the real test of whether contamination actually dropped, per this ticket's own acceptance criteria (wiring-only, no outcome asserted yet).

## 2026-09-19 — Spec #22 + tickets: retiring Ollama for Voyage AI + Gemini

Grilled through switching the deploy target off Ollama entirely (embedding, reranker, generator, Ragas judge) to Voyage AI + Gemini, then `/mattpocock-skills:to-spec`'d it as #22 and `/mattpocock-skills:to-tickets`'d it into 5 vertical slices, each an independent seam: #24 embedding, #25 reranker, #26 generator + structured Abstention, #27 Ragas judge, #28 docs/ADR cleanup (blocked by all four). Cost tracking (#23, drafted earlier the same session) re-pointed to block on #24/#26/#27 specifically instead of the whole #22 umbrella, once those existed.

## 2026-09-19 — Ticket #24: embedding provider swap (Voyage AI)

New `app/embeddings.py` (replaces `app/ollama.py`) calls Voyage AI's `voyage-3` (general-purpose multilingual — confirmed with the user over finance-domain and "run a comparison eval first" alternatives; picked to match CONTEXT.md's bilingual Thai/English requirement, matching #22's Implementation Decision that this choice needs a real Golden Set comparison before finalizing, not assumed here) instead of Ollama/`bge-m3`. Same `embed()` signature, so `ingest.py`/`query.py` needed no changes beyond the import. `voyage-3` returns 1024-dim vectors — matches the existing pgvector schema exactly, no migration needed (verified via a new direct test, `tests/test_embed.py`, added specifically because embedding-quality/shape claims in this project have repeatedly needed real verification rather than assumption). Reranker and generator untouched (own tickets, #25/#26).

**Real account blocker, not a code bug:** the dev Voyage AI account has no payment method on file, capping it at 3 RPM — made even this ticket's own test suite (13-45 `embed()` calls per file batch) fail intermittently on `RateLimitError`. Full test suite runs now take ~29 minutes purely from this ceiling (200-question eval would add ~100 minutes just for embedding, on top of the existing multi-hour Ragas scoring time). User chose to add retry+backoff (`voyageai.Client(max_retries=10)`, the SDK's own tenacity-based backoff) rather than fix the account tier — documented as a deliberate, informed deviation via comments on #24 and #22, since it contradicts #22's own "no new fallback/resilience logic" Implementation Decision.

**Code-review fixes (`mattpocock-skills:code-review`, Standards + Spec axes):**
- **Real bug, found by direct reproduction:** the Voyage client was constructed at module import time — `default_api_key()` raises immediately if `VOYAGE_API_KEY` is unset, so a missing key crashed app startup instead of failing per-request through the existing `/query`/`/documents` 503/400 path (violating this ticket's own acceptance criteria). Fixed with a lazy singleton behind a lock, matching `app/reranker.py`'s existing pattern exactly.
- **Missing AC caught, then done for real:** the eval corpus (51 Documents) had not actually been reset+re-ingested under the new embedding provider — an acceptance criterion, not just a test-suite concern. Ran it for real (`db.reset()` + `eval.run_eval._ingest_corpus`), confirmed 51 documents / 105 embedded Nodes in Postgres afterward.
- **Scope note, not a bug:** `eval/run_eval.py`'s Ragas judge used to build its embeddings wrapper from the pipeline's `EMBED_MODEL` constant — now `"voyage-3"`, which would silently break `OllamaEmbeddings`. Decoupled into a local `JUDGE_EMBED_MODEL = "bge-m3"` so the judge (ticket #27's job to swap) keeps working exactly as before; code review confirmed this is in-scope, not scope creep.

**Next:** ticket #25 (reranker → Voyage rerank) or #26 (generator → Gemini) — both still unblocked, independent of each other and of #24.

## 2026-09-19 — Ticket #25: reranker provider swap (Voyage AI rerank)

`app/reranker.py` calls Voyage AI's `rerank-2` instead of the self-hosted ONNX BGE-reranker-v2-m3 — same `rerank()` signature, so `_retrieve()` needed no changes. Lazy singleton client behind a lock, same pattern as #24's fix (and #24's own `_get_client()`) — avoids repeating the import-time-crash bug found in #24's code review. Explicitly re-sorts by `relevance_score` rather than trusting the API's own result order. `warm_up()`/the FastAPI `lifespan` hook removed entirely (nothing else used it), along with the now-unused `optimum`/`transformers`/`huggingface_hub` dependencies and their mypy overrides.

Unlike #24, **no retry/backoff was added** — this ticket's own acceptance criteria explicitly say "no new fallback/resilience logic," and the rerank endpoint didn't hit the 3 RPM wall the embed endpoint did (existing reranker tests ran in ~1.4s, no rate-limit errors). `voyageai.Client()` here intentionally uses SDK defaults, unlike `app/embeddings.py`'s `max_retries=10` — the two modules disagree on retry policy on purpose, not by oversight.

**Code-review (`mattpocock-skills:code-review`, Standards + Spec axes):** Spec axis found nothing — every AC met, no scope creep, lazy-init correctly avoided repeating #24's bug. Standards axis flagged one real gap (fixed): `rerank-2` had no version-pin comment, unlike the removed ONNX build's pinned `MODEL_REVISION` — added a comment explaining Voyage's hosted models aren't exposed as pinnable snapshots, so there's nothing to pin. Also flagged, correctly deferred to #28 (not this ticket's scope): ADR-0003's local-inference rationale and README's setup/architecture description are now stale for the reranker.

**Gemini budget note:** the user has a hard cap of ~500 Gemini requests before quota reset, and wants dev/test work for #26+#27 combined to stay under ~200 of that, saving the rest for the eventual real eval run (~880 requests estimated for one full Golden Set pass with Gemini as both generator and judge). This ticket (#25) used zero Gemini requests — Voyage-only.

**Next:** ticket #26 (generator → Gemini) or #27 (Ragas judge → Gemini + Voyage) — budget-conscious this round; minimize redundant full-suite reruns compared to #24/#25's pattern.

## 2026-09-19 — Ticket #26: generator provider swap (Gemini) + structured Abstention

New `app/generator.py` (`gemini-3.1-flash-lite`, chosen by the user — a low-cost/low-latency tier matching the retired Dev Generator's original cost/iteration-speed rationale) replaces the inline Ollama HTTP call that used to live in `app/query.py`. Structured JSON output via `google-genai`'s `response_schema` (a small Pydantic model with `answer`/`abstained` fields, accessed via `response.parsed`) replaces the plain-text `ABSTENTION_MARKER` string-matching hack, per ADR-0004's own stated plan for when a cloud generator capable of reliable structured output is wired in. `/query`'s response shape is unchanged — existing tests already asserted only the outer `Answer["abstained"]` bool, never the marker string, so no test changes were needed beyond `test_cache.py`'s two tests that monkeypatched the now-renamed `_generate`→`generate` function.

**Naming cleanup, a direct consequence of retiring `DEV_GENERATOR_MODEL`:** it split three ways, not one — `app/generator.py`'s `GENERATOR_MODEL` (the real call), `app/query.py`'s `_FUSION_LLM_PLACEHOLDER_MODEL` (an unrelated LlamaIndex `QueryFusionRetriever` constructor requirement that's never actually invoked, since `num_queries=1` disables the only path that would call it), and `eval/run_eval.py`'s `JUDGE_LLM_MODEL` (the Ragas judge, deliberately still Ollama until ticket #27 — same `JUDGE_EMBED_MODEL` pattern #24 already established).

Budget note: this ticket used ~40 real Gemini requests total (1 exploratory API-shape check + targeted regression runs + one full-suite run + a 3-test spot check after review fixes) — well inside the ~200 the user set aside for #26+#27 combined before the eval run.

**Code-review (`mattpocock-skills:code-review`, Standards + Spec axes):** Spec axis found nothing — all 5 AC met, no scope creep, correctly followed #24/#25's lazy-client-init pattern and added no retry/fallback logic (matching this ticket's own AC, unlike #24's authorized deviation). Standards axis: two small fixes applied (a rationale comment on `GENERATOR_MODEL`, and a diagnostic message on the `assert isinstance(parsed, _GeneratedAnswer)` check — kept as a real runtime assert rather than switching to `embeddings.py`'s `cast()` convention, since `parsed` can genuinely be `None` here if Gemini fails to return parseable JSON, unlike the `cast()` case which narrows a type that can't actually vary at runtime). Also flagged, correctly deferred to #28 (not this ticket's scope): ADR-0004 and CONTEXT.md still describe the now-collapsed Dev/Validation Generator split.

**Next:** ticket #27 (Ragas judge → Gemini + Voyage AI) — the last provider-swap ticket before #28 (docs/ADR cleanup) unblocks. Remaining Gemini budget: ~460 of the ~500 total, ~160 of the ~200 set aside for #26+#27's dev/test work.

## 2026-09-19 — Ticket #27: Ragas judge swap (Gemini + Voyage AI)

`eval/run_eval.py`'s `_score_answered_items()` now judges with Gemini (`ChatGoogleGenerativeAI`) and Voyage AI (`VoyageEmbeddings`) instead of `ChatOllama`/`OllamaEmbeddings` — and, now that the judge and pipeline are on the same providers, it directly reuses `app.generator.GENERATOR_MODEL`/`app.embeddings.EMBED_MODEL` rather than the separate `JUDGE_LLM_MODEL`/`JUDGE_EMBED_MODEL` constants #24/#26 introduced specifically to keep the judge decoupled while it was still stuck on Ollama — that decoupling no longer serves a purpose, per #22's "provider consistency" Implementation Decision, so it was removed.

**Two real bugs found and fixed, not design choices:**
- **The ~20-minute "hang" that triggered a mid-session debugging detour:** `langchain-google-genai==1.0.10` (the only version compatible with this project's pinned `langchain-core==0.2.43`; newer releases need `langchain-core>=1.6`, a breaking upgrade) forwards the `temperature` kwarg Ragas always injects per call straight into the raw `GenerativeServiceClient`, which doesn't accept it — `TypeError` on every single judge call, silently retried up to Ragas's own default `max_retries=10` with up to 60s backoff each. What looked like an indefinite hang was actually a guaranteed-to-fail call retried for ~20+ minutes before giving up. Found via a standalone diagnostic script with `log_tenacity=True` and a reduced retry budget, run outside pytest for immediate, unbuffered visibility into what was actually failing — pytest's own output buffering had been hiding the real error the first two times this was attempted inside the test suite. Fixed with a `_JudgeLLM(ChatGoogleGenerativeAI)` subclass overriding `_generate`/`_agenerate` to drop the stray kwarg before calling `super()`, relying on the temperature set at construction (0) instead.
- **`VoyageEmbeddings`'s default retry budget too thin for this account:** the judge's own embedding calls (`answer_relevancy`) compete for the same 3-RPM-capped Voyage AI account as the pipeline's own embedding calls that just ran. The default `max_retries=6`/10s-wait-cap wasn't enough to survive that combined load reliably (reproduced twice, consistent failure, not transient timing). Bumped to `max_retries=10` — the same category of deviation from #22's "no new fallback/resilience logic" decision already accepted for `app/embeddings.py` in #24, documented the same way via an issue comment on #27 this time (a gap code review caught: #24 got both a code comment and an issue comment, this one initially only got the code comment).

**Code-review (`mattpocock-skills:code-review`, Standards + Spec axes):** Spec axis found nothing missing, no scope creep — both fixes above were judged necessary to make the ticket's AC achievable at all, and reusing `GENERATOR_MODEL`/`EMBED_MODEL` matches #22's spec precisely. One process gap flagged and fixed: the retry deviation hadn't been recorded as an issue comment on #27 (added). Standards axis: no violations; one comment-quality gap flagged and fixed (explain *why* the judge no longer needs decoupled model constants, not just delete the old comment silently).

Gemini budget note: real API calls this ticket stayed modest despite the debugging detour, since the `TypeError` bug failed client-side before any network request — no Gemini quota was actually burned by the ~20-minute hang.

**Next:** ticket #28 (Retire Ollama — ADRs, glossary, docs) unblocks now that #24/#25/#26/#27 are all shipped. After that, the real Golden Set eval run (deferred until the Voyage/Gemini quota resets, per earlier budget discussion) is the actual test of whether this migration holds up at scale — the `_JudgeLLM`/retry fixes here were only validated against tiny fixtures (1-2 items), not the full 200-question corpus.

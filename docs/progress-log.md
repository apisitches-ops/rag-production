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

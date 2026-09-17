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

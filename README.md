# rag-production

A retrieval-augmented generation (RAG) system for Thai/English financial PDFs (PTT
One Report and related corpora) — built to actually exercise the parts a toy RAG
demo skips: hybrid dense+lexical retrieval, a reranker, ACL-restricted retrieval,
Abstention instead of a confident guess, an exact-match cache, and a Golden Set
eval harness that scores answers instead of eyeballing them. Every claim about
what works and what doesn't below is backed by a real measured run, not assumed —
see [Engineering notes](#engineering-notes) and [`docs/progress-log.md`](docs/progress-log.md)
for the receipts.

```
POST /documents  →  ingest a PDF/CSV, chunk it, embed it, store it
POST /query       →  ask a question, get an answer with citations, or an honest
                      "I don't know" — never a confident guess
```

## Why this exists

Portfolio RAG projects usually stop at "call an embedding API, call a vector DB,
call an LLM API, done." This one was built to find out what breaks past that point,
and the answer was: a lot, and not always what intuition predicts. A hybrid
dense+BM25 retriever and a cross-encoder reranker — the textbook fix for retrieval
quality — shipped and got manually reviewed against a 200-question Golden Set, and
**89% of the wrong answers were still cross-document contamination**, the exact
failure mode reranking was supposed to fix. Root-causing that (not just patching the
symptom) led to document-scoped retrieval instead — narrow to the likely-relevant
Documents *before* chunk-level search, rather than trusting a reranker to sort out
contamination after the fact. See [Engineering notes](#engineering-notes) for how
that was actually diagnosed, including two "obvious" fixes that were tried and
measurably didn't work.

The project also migrated its entire embedding/reranking/generation stack from
local Ollama models to cloud APIs (Voyage AI + Gemini) mid-project — not because the
local setup was broken, but because the deploy target has no GPU/Ollama available at
all, and because the switch surfaced its own real bugs (a stale third-party library
incompatibility that looked exactly like an infinite hang until it wasn't). See
[ADR-0006](docs/adr/0006-cloud-hosted-embedding-rerank-generation.md).

## How it works

**Ingestion** (`POST /documents`):

```
PDF or CSV → extract text (PyMuPDF for PDF, stdlib csv for CSV)
           → chunk into parent/child Nodes (LlamaIndex HierarchicalNodeParser —
             child Nodes are embedded/retrieved, parent Nodes give the Generator
             wider surrounding context)
           → embed child Nodes (Voyage AI voyage-3)
           → store Node text + embedding + parent link in Postgres/pgvector,
             tagged with an optional ACL Group
```

**Query** (`POST /query`):

```
question → check Redis exact-match cache (query + Acting Role) → hit? return it

         → embed the question (Voyage AI)
         → rank Documents by fusing dense (pgvector) + lexical (BM25) similarity
           via Reciprocal Rank Fusion, narrow to the top 3 — filtered to Documents
           whose ACL Group is unset or matches the Acting Role
         → within just those Documents: fuse dense + BM25 again at the chunk
           level, over-fetch a wide candidate pool, dedupe by parent
         → rerank the pool with Voyage AI's rerank-2, keep the top 5
         → ask the Generator (Gemini, structured JSON output) to answer using
           only that context — or signal Abstention if it's not enough
         → cache the result, return answer + citations + the actual context used
```

Document-scoping happens *before* chunk-level retrieval, not as a post-hoc filter —
see [Engineering notes](#engineering-notes) for why that ordering specifically was
the fix, not the reranker.

## Getting started

**Prerequisites:** Docker Desktop, and API keys for [Voyage AI](https://dash.voyageai.com)
(embedding + reranking) and [Gemini](https://aistudio.google.com) (generation + the
eval harness's Ragas judge).

```bash
# 1. Start Postgres (pgvector) + Redis
docker compose up -d --wait

# 2. Install and run
python3.11 -m venv .venv
.venv/bin/pip install -e ".[dev]"
DATABASE_URL=postgresql://rag:rag@localhost:5432/rag \
  VOYAGE_API_KEY=... GEMINI_API_KEY=... \
  .venv/bin/uvicorn app.main:app --reload
```

Then open **http://localhost:8000/** — a local-only single-page UI for uploading
Documents, seeing what's ingested and its ACL Group, and asking questions under
different Acting Roles with citations resolved back to their source Document. Not
a deployment (no hosting/auth/containerization) — just the fastest way to see the
pipeline's real behavior without hand-crafting HTTP requests. `GET /health` and the
raw JSON endpoints (`POST /documents`, `GET /documents`, `POST /query`) also have
interactive docs at `/docs`.

`docker compose down -v` picks up a schema change on an existing Postgres volume
(there's no migration tooling yet — `docker-entrypoint-initdb.d` only runs against
an empty volume).

## Tests

Every test touching ingestion, `/query`, or the eval harness calls the real Voyage
AI/Gemini APIs — no mocking, deliberately, because mocked retrieval/reranking tests
have already been proven to hide real bugs on this project (see Engineering notes).
That means running the suite costs a small amount of real API spend and inherits
the dev account's rate limits (3 RPM on Voyage AI without a payment method on file)
— expect tens of minutes for a full run, not seconds.

```bash
DATABASE_URL=postgresql://rag:rag@localhost:5432/rag \
  VOYAGE_API_KEY=... GEMINI_API_KEY=... .venv/bin/pytest
```

## Eval

`eval/golden_set.json` + `eval/corpus/` (51 Documents, 200 questions across 6
categories, derived from the Kaggle Financial/Legal Evaluation Dataset via
`eval/build_corpus.py` — see [`train.csv`](eval/data/train.csv)) is the Golden Set, standing
in for the real PTT One Report corpus until that's the actual ingestion target.
Running it wipes and re-ingests the database:

```bash
DATABASE_URL=postgresql://rag:rag@localhost:5432/rag \
  VOYAGE_API_KEY=... GEMINI_API_KEY=... .venv/bin/python -m eval.run_eval
```

Scores faithfulness, answer relevancy, and context precision (Ragas, judged by the
same Generator/Embedder as the pipeline itself), plus abstention rate/correctness.
A report lands in `eval/reports/<timestamp>.json`.

**Historical results** (Dev Generator era, local Ollama — before the Voyage
AI/Gemini migration; see [`docs/eval-comparison.md`](docs/eval-comparison.md) for
the full breakdown):

| Run | Combined correctness (manual review) | context_precision | abstention_correctness |
|---|---|---|---|
| Dense-only baseline | ~50-54% (naive heuristic — see Engineering notes) | 0.82 | 83% |
| Hybrid + reranker | **141/200 (70.5%)** | 0.85 | 82.5% |

The hybrid+reranker run's own manual review is what found the 89% cross-document-
contamination figure above, and is why document-scoped retrieval (the current
retrieval architecture) exists. **A fresh Golden Set run under the current
architecture — document-scoped retrieval + the Voyage AI/Gemini stack — hasn't
been published yet**; it's the next real milestone, deferred until the Voyage
AI/Gemini API quota resets (see Known limitations).

## Engineering notes

Real problems found and fixed, not just "it works":

- **A naive accuracy check said 50-54% correct; the real number was 70.5%.** A
  crude substring-match heuristic ("does the expected answer appear literally in
  the response?") badly undercounted correct answers that were just phrased or
  formatted differently ("438,000" vs "438,000 jobs"). Manually reviewing every
  flagged "wrong" answer — not trusting the heuristic — is what surfaced the real
  number, and is exactly why Ragas' semantic metrics, not string matching, are the
  ones this project reports.
- **Both textbook fixes for cross-document contamination — hybrid search and a
  cross-encoder reranker — were tested against a synthetic keyword-stuffing
  adversarial case before being trusted, and both failed it.** A distractor
  Document repeating a target entity's name (without actually answering about it)
  beat the correct Document in every variant tried, for *both* mechanisms. Both
  still shipped (they measurably helped other categories), but the regression
  tests for them were deliberately scoped down to wiring-correctness, not a
  semantic-superiority claim the synthetic testing couldn't back up — twice
  learning the same lesson before document-scoped retrieval was built as the
  actual fix.
- **Document-scoped retrieval's first version had a bug that would have silently
  defeated its own point.** Its Document-level Reciprocal Rank Fusion combined a
  BM25 rank drawn from the full corpus's node population (hundreds) with a dense
  rank already aggregated to a much smaller Document population — the two scales
  weren't comparable, so BM25's contribution was diluted to near-nothing,
  recreating the exact dense-alone weakness the feature existed to avoid. Caught
  by code review, fixed, and pinned with a regression test that fails against the
  pre-fix code specifically.
- **A ~20-minute apparent hang turned out to be a guaranteed-to-fail call, not slow
  I/O.** Migrating the eval harness's judge to Gemini, every scoring call
  `TypeError`'d instantly on a stray kwarg a pinned LangChain version couldn't
  handle — but the eval framework's own default retry policy (10 attempts, up to
  60s backoff each) silently retried the identical failure for ~20 minutes before
  giving up. `pytest`'s output buffering hid the real error twice; a standalone
  script with verbose logging and a *smaller* retry budget surfaced it in seconds.
- **PDF fixture generation silently dropped text past the page boundary**
  (`PyMuPDF`'s `insert_text` truncates without warning), and its first fix
  (`insert_textbox`) traded that bug for a different one — mis-rendering non-ASCII
  characters outside its base font's encoding (an em dash became `?`). Both were
  caught by round-tripping generated fixtures back through extraction and diffing
  against the source text, not by inspection.

## Project structure

```
app/
  main.py         FastAPI app: /health, /documents (POST+GET), /query, and the
                   local UI at /
  db.py           Postgres connection + reset
  ingest.py       PDF/CSV → chunk → embed → store
  query.py        retrieval (document-scoped hybrid fusion, rerank) + caching
  embeddings.py   Voyage AI embedding client
  reranker.py     Voyage AI rerank client
  generator.py    Gemini generation client, structured Abstention
  cache.py        Redis exact-match query cache
  static/         the local testing UI (single static HTML+JS page)
eval/
  build_corpus.py  Kaggle CSV → eval/corpus/ + golden_set.json
  run_eval.py      runs the Golden Set, scores with Ragas, writes a report
  data/train.csv   Kaggle source data build_corpus.py depends on directly
  corpus/          51 eval Documents
  golden_set.json  200 questions across 6 categories
tests/             pytest — no mocking of external APIs, see Tests above
docs/
  adr/                ADRs — architectural decisions and what superseded them
  progress-log.md     narrative log of every round of work and what was found
  eval-comparison.md  full breakdown of the historical eval runs
  rag-production-plan.txt  original planning doc, grilled into CONTEXT.md/ADRs
CONTEXT.md         project glossary — Document, Node, ACL Group, Acting Role,
                    Generator, Abstention, Guardrail, Golden Set, Eval Gate, ...
```

## Known limitations

- **The current retrieval architecture (document-scoped retrieval) hasn't had its
  own full Golden Set validation run yet** — it fixed a real, diagnosed bug in
  wiring-verified tests, but whether it measurably reduces the 89% contamination
  figure at full scale is the next thing to actually measure, not assume.
- `summary`-category questions are the known weak point (36% correctness in the
  last manual review) — the root cause (similarity-to-one-chunk retrieval doesn't
  suit "summarize this document" queries) is diagnosed but not yet fixed.
- No CI / automated Eval Gate yet — the Golden Set run is manual.
- ACL is a simulated metadata filter (per [ADR-0005](docs/adr/0005-acl-as-metadata-filter-simulation.md)),
  not real authentication — `acting_role` is a self-declared string, not a login.
- The Guardrail (prompt-injection/unsafe-content scan) named in `CONTEXT.md` isn't
  built yet.
- No cost/token-usage tracking yet (deferred — spec exists, not implemented).
- The local UI (`/`) is a dev-only testing tool, not a public deployment — no
  hosting, auth, or protection against running up the Voyage AI/Gemini bill.

## Tech stack

Python 3.11+ · [FastAPI](https://fastapi.tiangolo.com/) · Postgres +
[pgvector](https://github.com/pgvector/pgvector) · Redis · [LlamaIndex](https://www.llamaindex.ai/)
(`HierarchicalNodeParser`, `QueryFusionRetriever`, `BM25Retriever`) ·
[Voyage AI](https://www.voyageai.com/) (`voyage-3` embeddings, `rerank-2`
reranking) · [Gemini](https://ai.google.dev/) (`gemini-3.1-flash-lite` generation,
structured output) · [Ragas](https://docs.ragas.io/) (eval scoring) ·
[PyMuPDF](https://pymupdf.readthedocs.io/) · `psycopg` · `pytest` + `mypy`.

All dependency versions are pinned in [`pyproject.toml`](pyproject.toml), including
two version pins forced by real compatibility bugs found along the way (see
[`pyproject.toml`](pyproject.toml)'s own comments).

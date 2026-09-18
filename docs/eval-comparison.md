# Eval run comparison

Running summary of every full 200-question Golden Set eval run against the Kaggle-derived corpus, so a run-over-run comparison doesn't require reading through `progress-log.md` in full. Update this file after every full `run_eval` run. Full narrative (root causes, code-review fixes, decisions) stays in `progress-log.md`; this file is the at-a-glance table only.

All runs so far use the Dev Generator (`llama3.1:8b`, local) as both the pipeline's answer Generator and the Ragas judge, per ADR-0004. None of these are the Validation Generator (Claude) numbers that ADR-0004 says should be the ones actually reported.

## Overall metrics

| Run | Date | Report | faithfulness | answer_relevancy | context_precision | abstention_rate | abstention_correctness |
|---|---|---|---|---|---|---|---|
| Baseline (dense-only) | 2026-09-17 | `2026-09-17T07:54:09.494971+00:00.json` | 0.80 | 0.66 | 0.82 | 22% | 83% |
| Hybrid + reranker | 2026-09-18 | `2026-09-18T03:51:57.398982+00:00.json` | 0.79 | 0.64 | 0.85 | 24.5% | 82.5% |

**What changed between these two runs:** hybrid retrieval shipped (`643bee1` — dense + BM25 fused via `QueryFusionRetriever`, Reciprocal Rank Fusion) and the reranker shipped (`7b30291`/`0cb2701` — BGE-reranker-v2-m3 ONNX, over-fetch → rerank → truncate to `TOP_K`). Corpus, Golden Set, Dev Generator, and Ragas judge configuration are unchanged between the two runs — the only pipeline difference is the retrieval path.

## By category — context_precision

The metric most directly owned by retrieval, so the one to watch per-feature.

| Category | Baseline | Hybrid + reranker | Delta |
|---|---|---|---|
| core | 0.91 | 0.93 | +0.02 |
| boolean | 0.87 | 0.90 | +0.03 |
| complex_qa | 0.81 | 0.86 | +0.05 |
| math_basic | 0.71 | 0.79 | +0.08 |
| not_found_classification | 0.57 | 0.67 | +0.10 |
| **summary** | 0.39 | **0.37** | **-0.02 (regressed)** |

`summary` is the project's most-diagnosed weak spot (dense similarity to one chunk doesn't suit "summarize the whole document" queries) and the one hybrid+reranker was most hoped to help. It's the only category that got worse. Every other category improved, which is what pulled the *overall* context_precision average up (0.82 → 0.85) despite the actual target failure mode going untouched.

## Other notable per-category regressions (not yet investigated)

| Category | Metric | Baseline | Hybrid + reranker |
|---|---|---|---|
| math_basic | faithfulness | 0.47 | 0.38 |
| boolean | abstention_correctness | 75% | 60% |

## Decision log

- **2026-09-18:** hybrid search + reranker are not the fix for `summary`-type questions. Next candidate under consideration: document-scoped retrieval (narrow to the likely-relevant Document before chunk-level search). Reasoning and cost/latency notes in `maybe.txt` (not yet a spec — informal scratch notes, not tracked docs).

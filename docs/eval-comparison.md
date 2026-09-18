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

## Manual correctness review (hybrid + reranker run only)

Ragas's triad metrics don't include a pass/fail correctness score, and a prior string-match attempt (baseline run) badly undercounted correct answers due to phrasing differences — see `progress-log.md`'s baseline error-analysis entry. This pass judged every item by hand/LLM (query + expected_answer + actual answer, semantic judgment) instead, and combines it with the report's abstention data to score all 200 items, not just the answered ones.

**Combined score: 141/200 = 70.5%** (a fully-correct outcome is either a substantively correct answer, or a correct decision to abstain).

| Outcome | Count |
|---|---|
| Answered correctly (non-`not_found_classification`) | 124 |
| Answered incorrectly (non-`not_found_classification`) | 24 |
| Abstained correctly (`not_found_classification`, 17/20 of that category) | 17 |
| Answered when should have abstained (`not_found_classification`, 3/20) | 3 |
| **Abstained when should have answered (over-refusal, other categories)** | **32** |

Correctness-of-answer alone (151 answered items, ignoring the 49 abstentions): **124/151 = 82.1%**, broken down by category:

| Category | Answered | Correct | % correct |
|---|---|---|---|
| core | 93 | 89 | 96% |
| boolean | 12 | 9 | 75% |
| complex_qa | 16 | 12 | 75% |
| math_basic | 13 | 9 | 69% |
| **summary** | 14 | 5 | **36%** |

**Failure patterns among the 27 wrong answers — 89% (24/27) is cross-document contamination:**
- *Whole-topic swaps* (mostly `summary`, 9/9 of its wrong items): fluent, internally-consistent answer about a completely different Document than the one asked about (e.g. an invoice-fields question answered with a full NVIDIA earnings summary; a "list all people mentioned" question answered with 19 names pulled from three unrelated corpora).
- *Single-fact swaps* (`core`/`boolean`/`math_basic`): an otherwise-correct-looking answer with one number/fact substituted from a different corpus (e.g. a Nasdaq boolean question answered using a different corpus's Nasdaq figure).
- *Table row/column confusion* — 1/27, the same bug shape as the baseline run (cost-of-goods-sold question answered with that table's net-sales figure instead).
- *Reasoning error despite correct facts* — 2/27 (correctly cited both prices being compared, then stated the wrong conclusion).

This independently confirms and sharpens the `summary`-category diagnosis above: not a precision-tuning problem, but near-total topic contamination on broad/listy queries — the case for document-scoped retrieval as the next fix.

## Decision log

- **2026-09-18:** hybrid search + reranker are not the fix for `summary`-type questions, confirmed two ways (context_precision regression above, and the manual correctness review's contamination pattern). Next candidate under consideration: document-scoped retrieval (narrow to the likely-relevant Document before chunk-level search). Reasoning and cost/latency notes in `maybe.txt` (not yet a spec — informal scratch notes, not tracked docs).

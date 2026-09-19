# RAG Production

Production-pattern RAG system over Thai/English financial PDFs (PTT One Report and related corpora), built as a portfolio project.

## Language

### Access Control

**Document**:
A single ingested source file (e.g. one PDF) before chunking. The unit at which an ACL Group is assigned.
_Avoid_: file, source

**ACL Group**:
A label attached to a whole Document restricting which Acting Role may have it appear in Retrieved Context. Assigned per-Document, not per-Node, for the MVP.
_Avoid_: permission group, tag, access level

**Acting Role**:
The role a query declares itself as being made on behalf of, for testing ACL-restricted retrieval. Not a real authenticated user.
_Avoid_: user, permission, identity

### Retrieval & Indexing

**Node**:
A unit of text produced by chunking a Document. Parent Nodes carry wider surrounding text for the Generator; child Nodes are the ones embedded and retrieved against.
_Avoid_: chunk, segment

**Retrieved Context**:
The set of Nodes a query's retrieval step returns, before being passed to the Generator.
_Avoid_: context (ambiguous with a bounded context)

### Generation

**Generator**:
The model that produces answers from Retrieved Context — Gemini, used identically in development, in every Eval Gate run, and in the final run reported for the portfolio (see [ADR-0006](adr/0006-cloud-hosted-embedding-rerank-generation.md), which retired the earlier Dev Generator/Validation Generator split described in [ADR-0004](adr/0004-dev-and-validation-generator.md)).
_Avoid_: generation model, LLM, Dev Generator, Validation Generator

**Abstention**:
The Generator's decision, made at generation time, that the Retrieved Context does not contain enough information to answer the query — returned instead of a guessed answer.
_Avoid_: insufficient_context, refusal, no-answer, unanswerable response

**Guardrail**:
The prompt-injection / unsafe-content scan a query or retrieved content passes through before reaching the Generator.
_Avoid_: filter, safety check

### Evaluation

**Golden Set**:
The reference set of questions with known-correct answers and citations, used to score the pipeline deterministically. Its category proportions reflect only what the pipeline currently supports — a category is added once the pipeline can answer it, not before.
_Avoid_: eval set, test set

**Eval Gate**:
The CI step that runs the Golden Set through the pipeline and reports (and later, once the pipeline is stable, blocks merges on) regression against a baseline.
_Avoid_: eval check, test gate

**Source Conflict**:
A case where two or more Documents (e.g. One Reports from different years) give differing answers to the same question, used to test whether the Generator surfaces the discrepancy instead of picking one silently.
_Avoid_: contradiction, inconsistency

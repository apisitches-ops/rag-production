# Use LlamaIndex instead of LangChain

The previous project (`~/rag-project`) was built on LangChain, and its eval learnings (77%→93% accuracy, with root-cause diagnosis) carry over conceptually to this rebuild. We chose LlamaIndex instead because its built-in `HierarchicalNodeParser` and `QueryFusionRetriever` implement the parent-child chunking and dense+BM25 hybrid retrieval this project needs directly, rather than assembling them from lower-level LangChain primitives.

## Consequences

No code or abstractions carry over from the old project. The two codebases share domain knowledge (Thai/English financial PDF handling, eval findings) but not implementation — this is a rebuild, not a migration.

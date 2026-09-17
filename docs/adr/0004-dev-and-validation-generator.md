# Separate Dev Generator and Validation Generator

Cloud LLM calls (Claude) cost money and are made frequently during iterative development and repeated Eval Gate runs. We use a local model as the Dev Generator throughout development and every Eval Gate run, and only switch to Claude as the Validation Generator for a final run once the pipeline's results have stabilized.

## Consequences

Metrics produced against the Dev Generator during development are not directly comparable to the final numbers reported for the portfolio — they measure pipeline behavior (retrieval quality, chunking, etc.) under a weaker generator, not final answer quality. Only the Validation Generator's run should be published as the project's reported results.

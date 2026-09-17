# Separate Dev Generator and Validation Generator

Cloud LLM calls (Claude) cost money and are made frequently during iterative development and repeated Eval Gate runs. We use a local model as the Dev Generator throughout development and every Eval Gate run, and only switch to Claude as the Validation Generator for a final run once the pipeline's results have stabilized.

## Consequences

Metrics produced against the Dev Generator during development are not directly comparable to the final numbers reported for the portfolio — they measure pipeline behavior (retrieval quality, chunking, etc.) under a weaker generator, not final answer quality. Only the Validation Generator's run should be published as the project's reported results.

The two Generators also need different Abstention protocols. The Dev Generator (llama3.1:8b) signals Abstention via a plain-text marker string in its response, because an 8B local model can't reliably produce well-formed structured output (the reasoning behind this ADR in the first place). When the Validation Generator (Claude) is wired in, its Abstention signal should move to structured JSON output instead, since that's exactly the native structured-output capability this ADR chose Claude for — the marker-string approach is a Dev Generator-specific workaround, not the long-term protocol.

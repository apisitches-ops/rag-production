# Resource-constrained local inference on the dev machine

The dev machine is a 16GB M1 Pro with no NVIDIA GPU, so components that would normally run on GPU-backed cloud infrastructure in production must run locally on CPU/Metal instead. We use the ONNX-quantized build of BGE-reranker-v2-m3 (rather than the raw PyTorch model) and Llama Guard 3 1B (rather than 8B) specifically to keep memory and latency workable on this hardware — running the 8B guard model alongside the rest of the stack was estimated to push RAM usage to 14-17GB, risking swap.

## Consequences

Llama Guard 1B is measurably less accurate on some unsafe-content categories (e.g. code-interpreter abuse) than the 8B model. This is an accepted trade-off for a resource-constrained dev machine, not a claim that 1B is the right choice at production scale with real GPU infrastructure.

# Postgres + pgvector instead of a dedicated vector database

Production RAG blueprints typically call for a dedicated vector database (e.g. Qdrant) once corpus size grows, but pgvector is documented as suitable up to roughly 10-50M vectors. This project's corpus (a handful of annual reports plus synthetic/eval Documents) sits far below that threshold, so vector search and the relational registry run in one Postgres instance instead of standing up a second service.

## Considered Options

- **Qdrant** — rejected: adds an operational component with no accuracy or latency benefit at this corpus scale.

## Consequences

If the corpus later grows toward the tens-of-millions-of-vectors range, this decision should be revisited.

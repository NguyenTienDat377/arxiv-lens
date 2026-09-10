# Demo artifacts

Real responses from the running system, captured with `scripts/demo.sh`, so the
output is visible without standing the stack up.

| file | request | notes |
| --- | --- | --- |
| `api-stats.json` | `GET /api/stats` | The full graph, by entity label and relation type. Travels REST -> gRPC -> Neo4j -> back. No LLM call. |
| `api-query-ltn.json` | `POST /api/query` — *What builds on Logic Tensor Networks?* | Three `EXTENDS` edges, each fact tagged with the arXiv id that asserts it. The prose is written by Claude from those facts and nothing else. |
| `api-query-hallucination.json` | `POST /api/query` — *Which methods address hallucination?* | Eleven methods, eleven citations. The point: every claim in the answer traces to a paper. |

## Screenshots

`screenshots/` is where browser captures go. Worth grabbing, in order of value:

1. **Grafana** (`localhost:3000`, dashboard *arxiv-lens - query service*) after a
   few `demo.sh query` calls — the cache hit-ratio panel dropping and recovering
   is the Kafka invalidation made visible.
2. **Neo4j Bloom / browser** (`localhost:7474`) showing the neighbourhood of
   `first-order logic` — one formalism connecting dozens of papers that never
   cite each other.
3. **Swagger UI** (`localhost:8082/swagger-ui.html`) — the API surface.
4. **Prometheus targets** (`localhost:9090/targets`) — `query-service` UP.

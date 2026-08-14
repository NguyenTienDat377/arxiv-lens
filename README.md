# arxiv-lens

> A production GraphRAG system that tracks how knowledge evolves — automatically detecting when your research corpus drifts and rebuilding the knowledge graph before your answers go stale.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Java](https://img.shields.io/badge/Java-21-orange.svg)](https://openjdk.org/)
[![Python](https://img.shields.io/badge/Python-3.11-blue.svg)](https://www.python.org/)
[![Spring Boot](https://img.shields.io/badge/Spring%20Boot-3.x-brightgreen.svg)](https://spring.io/projects/spring-boot)

---

## What is this?

Most GraphRAG demos stop at "build the graph, query it." **arxiv-lens** asks the harder question: _what happens when the corpus changes?_

This project builds a fully observable, drift-aware GraphRAG pipeline over arXiv papers in the neuro-symbolic AI subfield. When new papers arrive and the embedding distribution shifts beyond a configurable threshold, an automated pipeline re-extracts entities, rebuilds the knowledge graph, validates consistency with a Z3-backed checker, runs a quality gate against a golden QA set, and promotes the new graph version — all without manual intervention.

The system is split into two microservices connected by Kafka (async events) and gRPC (synchronous queries), served publicly via a Spring Boot REST API.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    graph-pipeline (Python)               │
│                                                         │
│  Prefect DAGs                                           │
│  ┌──────────────┐    ┌──────────────┐    ┌───────────┐ │
│  │ Corpus       │───▶│ Drift        │───▶│ Re-extract│ │
│  │ ingestion    │    │ detector     │    │ pipeline  │ │
│  └──────────────┘    └──────────────┘    └─────┬─────┘ │
│                       (evidently AI)            │       │
│                                                 ▼       │
│  ┌──────────────┐    ┌──────────────┐    ┌───────────┐ │
│  │ Z3           │◀───│ Graph        │◀───│ Entity &  │ │
│  │ consistency  │    │ diff + merge │    │ relation  │ │
│  │ checker      │    │ (Neo4j)      │    │ extraction│ │
│  └──────┬───────┘    └──────────────┘    └───────────┘ │
│         │ pass                                          │
│         ▼                                               │
│  ┌──────────────┐    ┌──────────────┐                  │
│  │ Quality gate │───▶│ MLflow graph │                  │
│  │ (golden QA)  │    │ versioning   │                  │
│  └──────────────┘    └──────┬───────┘                  │
│                             │ graph.updated (Kafka)     │
└─────────────────────────────┼───────────────────────────┘
                              │
          ┌───────────────────┘
          ▼  Kafka
┌─────────────────────────────────────────────────────────┐
│                   query-service (Java / Spring Boot)     │
│                                                         │
│  REST API (OpenAPI)                                     │
│  ┌──────────────┐    ┌──────────────┐    ┌───────────┐ │
│  │ /api/query   │───▶│ QueryUseCase │───▶│ gRPC stub │ │
│  │ /api/health  │    │ (application)│    │ outbound  │ │
│  │ /api/stats   │    └──────────────┘    │ adapter   │ │
│  └──────────────┘                        └─────┬─────┘ │
│                                                │ gRPC  │
│  Kafka consumer                                │       │
│  ┌──────────────┐                              ▼       │
│  │ graph.updated│    ┌──────────────┐    graph-pipeline│
│  │ → cache      │    │ Prometheus   │                  │
│  │   invalidate │    │ /actuator    │                  │
│  └──────────────┘    └──────────────┘                  │
└─────────────────────────────────────────────────────────┘
```

### Communication patterns

| Channel   | Used for                                 | Why                                                |
| --------- | ---------------------------------------- | -------------------------------------------------- |
| **Kafka** | `graph.updated`, `drift.detected` events | Async — pipeline doesn't wait for the API layer    |
| **gRPC**  | `QueryRequest` / `QueryResponse`         | Sync — low-latency query execution at request time |
| **REST**  | Public API surface                       | Standard — what clients actually call              |

---

## Tech stack

| Layer                         | Technology                             |
| ----------------------------- | -------------------------------------- |
| Knowledge graph               | Neo4j                                  |
| Pipeline orchestration        | Prefect                                |
| Experiment / graph versioning | MLflow                                 |
| Drift detection               | evidently AI                           |
| Formal consistency checking   | Z3 (SMT solver)                        |
| Entity extraction             | spaCy + LLM (Claude / local Ollama)    |
| Embeddings                    | sentence-transformers                  |
| Message broker                | Apache Kafka                           |
| API framework                 | Spring Boot 3 (Java 21)                |
| Build tool                    | Gradle                                 |
| Observability                 | Prometheus + Grafana                   |
| Container orchestration       | Docker Compose (dev) · k3s (prod-like) |

---

## Project structure

```
arxiv-lens/
├── proto/
│   └── graphrag.proto          # gRPC contracts (QueryRequest, QueryResponse, GraphStats)
├── infra/
│   ├── docker-compose.yml      # Neo4j, Kafka, Zookeeper, Prometheus, Grafana
│   └── monitoring/             # Prometheus config, Grafana dashboards
├── k8s/
│   ├── graph-pipeline-deployment.yml
│   ├── query-service-deployment.yml
│   ├── kafka-statefulset.yml
│   └── neo4j-statefulset.yml
├── graph-pipeline/             # Python service
│   ├── pipeline/
│   │   ├── models.py           # Paper schema (Pydantic) — shared by every stage
│   │   ├── ingest_arxiv.py     # arXiv API → dated corpus snapshot
│   │   ├── extract_entities.py
│   │   ├── build_graph.py
│   │   ├── drift_detector.py   # evidently AI embedding drift
│   │   └── consistency_checker.py  # Z3 relation validator
│   ├── data/raw/<snapshot-id>/ # Immutable corpus snapshots (JSONL, gitignored)
│   ├── graph/                  # Neo4j client, graph diff utilities
│   ├── retrieval/              # GraphRAG query logic
│   ├── eval/                   # Golden QA set, quality gate
│   ├── kafka/                  # Kafka producers
│   ├── mlflow/                 # Graph version tracking
│   ├── requirements.txt
│   └── Dockerfile
├── query-service/              # Java Spring Boot service
│   ├── src/main/java/.../
│   │   ├── domain/             # GraphQuery, QueryResult models
│   │   ├── application/        # QueryUseCase, port interfaces
│   │   └── adapter/
│   │       ├── inbound/        # REST controllers (OpenAPI spec)
│   │       ├── outbound/       # gRPC stub → graph-pipeline
│   │       └── kafka/          # graph.updated consumer → cache invalidation
│   ├── src/main/resources/
│   │   └── application.yml
│   ├── src/test/               # Unit + integration tests
│   ├── build.gradle
│   └── Dockerfile
├── .github/
│   └── workflows/
│       └── ci.yml              # Build, test, lint both services
├── .gitignore
├── LICENSE
└── README.md
```

---

## Key engineering decisions

### Why immutable corpus snapshots?

Ingestion writes a dated, append-only JSONL snapshot to disk, and every downstream stage reads from a snapshot id rather than from the network. This is what makes drift detection meaningful: drift is defined as a shift between the embedding distribution of one snapshot and the next, so both must still exist, byte-for-byte, when the detector fires. Streaming arXiv results straight into extraction would leave nothing to compare against and no way to answer "which papers moved the distribution?" It also makes the pipeline replayable — extraction can be re-run against a fixed corpus while prompts are tuned, without re-querying arXiv.

Only abstracts are ingested, not PDF full text. Abstracts carry enough concept and method signal to build a meaningful knowledge graph, while full-text parsing (layout, math, references) is a substantially larger problem that would dominate the project.

### Why two microservices?

The Python/Java boundary is a genuine language mismatch — not microservices for the sake of it. The graph pipeline is Python because the entire ML/NLP ecosystem (spaCy, sentence-transformers, Z3 Python bindings, evidently, MLflow) lives there. The query service is Java because Spring Boot's production-grade HTTP server, Kafka client, and observability tooling are significantly more mature than Python equivalents for API serving.

### Why Kafka + gRPC, not just one?

Two different communication patterns serving two different needs. `graph.updated` events are fire-and-forget — the pipeline doesn't need to wait for the API layer to acknowledge. Query execution is synchronous — a client waits for an answer. Kafka for events, gRPC for request-response is the architecturally honest split.

### Why Z3 for consistency checking?

LLM-extracted relations are probabilistic. Z3 gives deterministic validation of structural constraints on the knowledge graph — e.g. detecting cyclic `is_part_of` chains, contradictory relation types, or schema violations. This is the neuro-symbolic layer: probabilistic extraction feeding a formal verifier.

### Why hexagonal architecture in the Spring Boot service?

The `outbound/` gRPC adapter can be swapped for an in-memory mock in tests without changing the domain or application layer. This is the textbook motivated use of hexagonal architecture, not architecture for its own sake.

---

## Roadmap

- [ ] `graph-pipeline` — shared paper schema (Pydantic)
- [ ] `graph-pipeline` — arXiv ingestion via API → immutable corpus snapshots
- [ ] `graph-pipeline` — entity/relation extraction (spaCy + LLM)
- [ ] `graph-pipeline` — Neo4j graph builder
- [ ] `graph-pipeline` — embedding drift detector (evidently AI)
- [ ] `graph-pipeline` — Z3 consistency checker
- [ ] `graph-pipeline` — golden QA eval harness
- [ ] `graph-pipeline` — MLflow graph versioning
- [ ] `graph-pipeline` — Kafka producers
- [ ] `proto/` — gRPC contract definition
- [ ] `query-service` — Spring Boot REST API (hexagonal)
- [ ] `query-service` — gRPC outbound adapter
- [ ] `query-service` — Kafka consumer + cache invalidation
- [ ] `infra/` — Docker Compose full stack
- [ ] `infra/` — Prometheus + Grafana dashboards
- [ ] `k8s/` — k3s manifests

---

## Getting started

> 🚧 Under active development. Instructions will be updated as services are completed.

**Prerequisites**

- Docker + Docker Compose
- Java 21
- Python 3.11+
- `protoc` (Protocol Buffers compiler)

```bash
# Clone
git clone https://github.com/your-username/arxiv-lens.git
cd arxiv-lens

# Start infra (Neo4j, Kafka, Prometheus, Grafana)
docker compose -f infra/docker-compose.yml up -d

# Run graph-pipeline
cd graph-pipeline
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# (pipeline instructions coming soon)

# Run query-service
cd ../query-service
./gradlew bootRun
```

---

## License

MIT — see [LICENSE](LICENSE)

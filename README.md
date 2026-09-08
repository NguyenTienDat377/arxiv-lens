# arxiv-lens

> A GraphRAG system that tracks how knowledge evolves — detecting when the research corpus drifts, and refusing to promote a knowledge graph that shouldn't ship.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Java](https://img.shields.io/badge/Java-21-orange.svg)](https://openjdk.org/)
[![Python](https://img.shields.io/badge/Python-3.11-blue.svg)](https://www.python.org/)
[![Spring Boot](https://img.shields.io/badge/Spring%20Boot-3.x-brightgreen.svg)](https://spring.io/projects/spring-boot)

---

## What is this?

Most GraphRAG demos stop at "build the graph, query it." **arxiv-lens** asks the harder questions: _what happens when the corpus changes, and how do you know the graph is any good?_

This project builds a drift-aware GraphRAG pipeline over arXiv papers in the neuro-symbolic AI subfield. Extraction is constrained by a hand-derived ontology rather than left open-ended, so the schema can be enforced — violating edges are repaired mechanically where the ontology forces a single answer, and reported where it doesn't. Snapshots are compared before promotion, and the assembled graph is checked by an SMT solver for contradictions no single paper contains.

The system is split into two services connected by Kafka (async events) and gRPC (synchronous queries), served through a Spring Boot REST API.

---

## Current status

Both services run and talk to each other. A REST call to the Java service crosses gRPC
into the Python pipeline, traverses Neo4j, and comes back with cited facts. What remains
is the Kafka link between them, and deployment.

| Stage | Status |
| ----- | ------ |
| arXiv ingestion → immutable snapshots | ✅ 300 papers |
| Ontology (6 entity types, 6 relations, OWL properties) | ✅ |
| Entity/relation extraction (structured outputs) | ✅ 2047 entities, 1250 relations |
| Ontology repair + validation | ✅ 11 violations remaining (0.9%) |
| Canonicalization (name merge, type resolution) | ✅ 1808 → 1754 names |
| Drift gate | ✅ blocks promotion, non-zero exit |
| Neo4j graph builder | ✅ 2054 nodes, ~3300 edges, idempotent |
| Z3 consistency checker | ✅ minimal unsat cores |
| GraphRAG retrieval with citations | ✅ 6 traversal intents, hybrid entity linking |
| Golden QA eval | ✅ 18 cases, 12 positive and 6 negative |
| Vector-RAG baseline and comparison | ✅ graph 100% vs vector 61% paper recall |
| Docker Compose (Neo4j + Kafka) | ✅ |
| gRPC contract + Python server | ✅ `GraphRagService`: Query, GetGraphStats |
| Spring Boot service (hexagonal) | ✅ REST → gRPC → graph, end to end |
| Caffeine cache on query results | ✅ awaiting Kafka-driven eviction |
| Kafka producer/consumer, MLflow, k8s | ⬜ not started |

The path a request actually takes:

```
POST /api/query  →  QueryController  →  QueryUseCase (cached)
                 →  GraphRagGrpcAdapter  ──gRPC──▶  Python GraphRagService
                                                 →  Neo4j traversal
                                                 →  cited facts + generated answer
```

```json
{ "answer": "The graph holds no facts matching that traversal.",
  "facts": [], "intent": "LINEAGE",
  "linkedEntities": ["ARTEMIS"], "unresolvedEntities": [],
  "status": "NO_FACTS", "snapshotId": "2026-08-16T04-37-21Z" }
```

`status` carries what HTTP codes cannot: `ENTITY_NOT_FOUND` means the name resolved to no
node, `NO_FACTS` means it resolved and nothing is asserted about it. Both return 200,
because the query succeeded — "nothing is connected to that" is an answer.

A representative query the graph answers today: *`first-order logic` connects 66 distinct pairs of papers* — a relationship no single abstract contains and no vector search over chunks would surface.

---

## The ontology

The design decision the rest of the project rests on. Six entity types and six relations, derived by open coding a sample of the corpus rather than adopted from an existing vocabulary.

| Relation | Domain → Range | Properties |
| -------- | -------------- | ---------- |
| `EXTENDS` | {METHOD, FORMALISM} → {METHOD, FORMALISM} | transitive, irreflexive, asymmetric |
| `USES` | {METHOD, FORMALISM} → {METHOD, MODEL, FORMALISM} | irreflexive, asymmetric |
| `COMPILES_TO` | FORMALISM → FORMALISM | transitive, irreflexive, asymmetric |
| `COMBINES` | {METHOD, FORMALISM} → {METHOD, FORMALISM} | symmetric, irreflexive |
| `EVALUATED_ON` | METHOD → DATASET | — |
| `ADDRESSES` | METHOD → {TASK, PROBLEM} | — |

Entity types: `METHOD`, `TASK`, `FORMALISM`, `MODEL`, `DATASET`, `PROBLEM`.

This table is not documentation. It is used four times:

1. **As a decoding constraint** — the closed enums become the JSON schema for structured outputs, so the model cannot invent a seventh relation type.
2. **As a repair rule** — when an edge violates domain/range, the ontology is queried for which predicates *would* accept those endpoint types. Exactly one → rewrite. Zero → drop. Several → leave it and report, because guessing would fabricate a claim.
3. **As a tiebreak during canonicalization** — when one name is typed differently by different papers, the constraints of the edges it participates in vote before mention counts do.
4. **As logic for Z3** — `transitive + irreflexive + asymmetric` on `EXTENDS` means a cycle is a contradiction, and cycles are what the solver finds.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                    graph-pipeline (Python)                    │
│                                                              │
│  ┌──────────────┐    ┌──────────────┐    ┌────────────────┐ │
│  │ arXiv        │───▶│ Immutable    │───▶│ Extraction     │ │
│  │ ingestion    │    │ snapshots    │    │ (structured    │ │
│  └──────────────┘    │ data/raw/    │    │  outputs)      │ │
│                      └──────────────┘    └───────┬────────┘ │
│                                                  │          │
│                       ┌──────────────┐           ▼          │
│                       │ Ontology     │──▶┌────────────────┐ │
│                       │ RELATION_    │   │ validate()     │ │
│                       │ SPECS        │──▶│ repair()       │ │
│                       └──────┬───────┘   └───────┬────────┘ │
│                              │                   ▼          │
│                              │           ┌────────────────┐ │
│                              └──────────▶│ Canonicalize   │ │
│                                          │ names + types  │ │
│                                          └───────┬────────┘ │
│                                                  ▼          │
│  ┌──────────────┐   fail    ┌──────────────────────────┐   │
│  │ Drift gate   │◀──────────│  snapshot N vs N+1        │   │
│  │ exit 1       │           └───────────┬──────────────┘   │
│  └──────────────┘                       │ pass             │
│                                         ▼                  │
│  ┌──────────────┐    ┌──────────────┐  ┌────────────────┐  │
│  │ Z3           │◀───│ Neo4j        │◀─│ Graph builder  │  │
│  │ consistency  │    │ + provenance │  │ (MERGE, idem-  │  │
│  │ checker      │    │              │  │  potent)       │  │
│  └──────┬───────┘    └──────────────┘  └────────────────┘  │
│         │ pass                                             │
│         ▼                                                  │
│  ┌──────────────┐    ┌──────────────┐   ⬜ planned          │
│  │ Quality gate │───▶│ MLflow graph │                      │
│  │ (golden QA)  │    │ versioning   │                      │
│  └──────────────┘    └──────┬───────┘                      │
│                             │ graph.updated (Kafka)  ⬜     │
└─────────────────────────────┼──────────────────────────────┘
                              │
          ┌───────────────────┘
          ▼  Kafka
┌──────────────────────────────────────────────────────────────┐
│         query-service (Java / Spring Boot)                    │
│                                                              │
│  REST API (OpenAPI)                                          │
│  ┌──────────────┐    ┌──────────────┐    ┌────────────────┐ │
│  │ /api/query   │───▶│ QueryUseCase │───▶│ gRPC outbound  │ │
│  │ /api/health  │    │ (application)│    │ adapter        │ │
│  │ /api/stats   │    └──────────────┘    └───────┬────────┘ │
│  └──────────────┘                                │ gRPC     │
│  Kafka consumer                                  ▼          │
│  ┌──────────────┐    ┌──────────────┐    graph-pipeline     │
│  │ graph.updated│    │ Prometheus   │                       │
│  │ → cache      │    │ /actuator    │                       │
│  │   invalidate │    └──────────────┘                       │
│  └──────────────┘                                           │
└──────────────────────────────────────────────────────────────┘
```

### Communication patterns

| Channel   | Used for                                 | Why                                                |
| --------- | ---------------------------------------- | -------------------------------------------------- |
| **Kafka** | `graph.updated`, `drift.detected` events | Async — pipeline doesn't wait for the API layer    |
| **gRPC**  | `QueryRequest` / `QueryResponse`         | Sync — low-latency query execution at request time |
| **REST**  | Public API surface                       | Standard — what clients actually call               |

---

## Tech stack

| Layer                         | Technology                             | Status |
| ----------------------------- | -------------------------------------- | ------ |
| Knowledge graph               | Neo4j 5                                | ✅ |
| Entity extraction             | Claude structured outputs + Batch API  | ✅ |
| Schema / validation           | Pydantic, closed `StrEnum` ontology    | ✅ |
| Formal consistency checking   | Z3 (SMT solver)                        | ✅ |
| Corpus source                 | arXiv API                              | ✅ |
| Experiment / graph versioning | MLflow                                 | ⬜ |
| Embeddings (baseline + linking) | sentence-transformers (MiniLM)       | ✅ |
| Embedding drift detection     | evidently AI                           | ⬜ |
| Message broker                | Apache Kafka (broker up, no producer)  | ◐ |
| API framework                 | Spring Boot 4 (Java 21)                | ✅ |
| Service contract              | gRPC + protobuf                        | ✅ |
| Build tool                    | Gradle                                 | ✅ |
| Observability                 | Prometheus + Grafana                   | ⬜ |
| Container orchestration (dev) | Docker Compose                         | ✅ |
| Container orchestration (prod-like) | k3s                              | ⬜ |

Drift detection is currently **structural** — a diff of the extracted graph between snapshots, used as a promotion gate. Distributional (embedding) drift is a separate, planned addition; the two measure different things and are not alternatives.

---

## Project structure

```
arxiv-lens/
├── graph-pipeline/                  # Python service
│   ├── pipeline/
│   │   ├── ontology.py              # entity + relation types, domain/range, OWL properties
│   │   ├── models.py                # Paper, Entity, Relation, PaperExtraction (Pydantic)
│   │   ├── snapshots.py             # atomic snapshot write/read, extraction reuse index
│   │   ├── ingest_arxiv.py          # arXiv API → dated corpus snapshot
│   │   ├── extract_entities.py      # structured outputs, batch, validate(), repair()
│   │   ├── canonicalize.py          # name merging, type resolution, post-merge repair
│   │   ├── drift_detector.py        # snapshot diff → promotion gate (exit 1)
│   │   ├── build_graph.py           # Neo4j loader, idempotent MERGE + provenance
│   │   └── consistency_checker.py   # Z3 encoding of the ontology's properties
│   ├── data/raw/<snapshot-id>/      # immutable corpus snapshots (gitignored)
│   ├── data/extracted/<snapshot-id>/# extraction results, keyed by (arxiv_id, version)
│   ├── retrieval/
│   │   ├── graph_rag.py             # plan → link → traverse → cited answer
│   │   └── vector_rag.py            # conventional RAG baseline over abstracts
│   ├── eval/
│   │   ├── golden.json              # questions with ground truth from the abstracts
│   │   ├── run_eval.py              # scores retrieval, exits 1 below threshold
│   │   └── compare.py               # graph vs vector on the same questions
│   ├── requirements.txt
│   └── Dockerfile
├── proto/graphrag.proto             # GraphRagService: Query, GetGraphStats
├── infra/docker-compose.yml         # Neo4j + Kafka (KRaft), named volumes
├── query-service/                   # Spring Boot 4, hexagonal
│   ├── build.gradle                 # protobuf plugin points at ../proto
│   └── src/main/java/com/arxivlens/queryservice/
│       ├── domain/                  # records and enums, no framework, no proto
│       ├── application/             # GraphPort (the port), QueryUseCase
│       ├── adapter/out/grpc/        # the only place protobuf types appear
│       ├── adapter/in/web/          # REST controller, request DTO, error handling
│       └── config/                  # Caffeine cache
├── k8s/                             # ⬜ k3s manifests
├── docs/comment.md                  # design rationale notes
├── LICENSE
└── README.md
```

---

## Key engineering decisions

### Why a hand-derived ontology instead of schema-free extraction?

Schema-free GraphRAG lets the model name its own entity and relation types per document. Across 300 abstracts that produces `USES`, `uses`, `utilizes`, `LEVERAGES`, and `IS_BUILT_ON` as five distinct relationship types, and the resulting graph cannot be queried — no single traversal catches "method depends on component."

A closed ontology turns the schema into a decoding constraint, and then into something enforceable. The trade is coverage for consistency, which is the right trade when the graph exists to be traversed.

### Why cache extractions by `(arxiv_id, version)`?

The extractor is not deterministic. Re-running the same abstract yields a different graph — entities appear and vanish between runs. This is not fixable through sampling parameters.

So reproducibility is achieved architecturally rather than statistically: an abstract is extracted once, keyed by paper id and version, and reused verbatim forever after. This is what makes drift measurable at all — a diff between snapshots is only meaningful if unchanged inputs produce unchanged outputs.

### Why immutable corpus snapshots?

Every downstream stage reads from a snapshot id, never from the network. Drift is defined as a difference between snapshot N and N+1, so both must still exist, byte-for-byte, when the detector runs. It also makes the pipeline replayable — extraction can be re-run against a fixed corpus while prompts are tuned, without re-querying arXiv.

Only abstracts are ingested, not PDF full text. Abstracts carry enough method and concept signal to build a meaningful graph, while full-text parsing (layout, math, references) is a substantially larger problem that would dominate the project.

### Why canonicalize before building the graph?

Traversal is exact. `LLM` and `LLMs` as separate nodes means every query about language models returns half its answers. Canonicalization merges surface variants, then resolves the type conflicts that merging creates — a name typed `METHOD` by one paper and `FORMALISM` by another needs one label before Neo4j will accept it.

Type resolution is a cascade: the ontology's domain/range constraints vote first, then mention counts, then a fixed precedence order. Decisions made by precedence alone are logged for review rather than hidden.

### Why is the drift detector a gate rather than a dashboard?

It exits non-zero, so `drift_detector && build_graph` refuses to load a bad snapshot. Churn is measured only on papers present in *both* snapshots at the same version — those are served from cache and cannot legitimately change, so any difference means something upstream moved (an edited prompt, a changed ontology). New and removed papers are explained by corpus growth and excluded from the measurement.

### Why Z3 for consistency checking?

`EXTENDS` is transitive, irreflexive, and asymmetric. If three separate papers assert `A EXTENDS B`, `B EXTENDS C`, and `C EXTENDS A`, transitivity closes that into `A EXTENDS A`, which irreflexivity forbids. No single edge is wrong; the contradiction exists only in their conjunction, and it spans three papers no human read together.

A cycle-detecting traversal would find that one case. The solver earns its place when constraints interact — asymmetry, type disjointness, and domain/range simultaneously — where hand-rolled checks multiply and a solver simply takes the conjunction. It also returns a **minimal unsat core**: the specific edges that cannot coexist, rather than "something is wrong."

### Why Kafka for roughly one event a day?

A graph rebuild emits one `graph.updated` event, and rebuilds happen daily at most.
That is nowhere near the volume Kafka is built for, and an HTTP call from the pipeline
to the query service would move the same byte.

The reason is coupling, not throughput. With a direct call the pipeline has to know who
consumes its output, how many consumers there are, and whether they are up — and a
rebuild would fail, or silently drop the notification, because an unrelated service was
restarting. With a log, the pipeline appends and stops caring. A consumer that was down
for an hour resumes from its offset and catches up; a second consumer can be added later
without the pipeline changing at all.

That argument holds at one message a day exactly as it holds at a million a second,
because it is about who depends on whom rather than how much data moves. The volume here
does not justify the operational weight on its own, and this README would rather say so
than imply a scale that does not exist.

### Why a vector baseline sits in the repo

`retrieval/vector_rag.py` is a conventional RAG implementation over the same corpus — one
abstract per chunk, MiniLM embeddings, top-k cosine — and `eval/compare.py` runs the same
golden questions through both. Keeping a baseline you might lose to is the only way the
graph's cost is answerable rather than asserted.

On the current golden set the graph recovers every expected paper and the baseline
recovers 61%. That number is directional at best: the questions were written against the
ontology and the expected-paper lists are incomplete, both of which favour the graph.

The result that does not depend on question wording is the negative cases. Top-k
retrieval always returns k documents; there is no similarity threshold at which "nothing
here is relevant" is the output. Asked how two unrelated papers relate, the baseline
returns five plausible neighbours and the graph returns nothing, because nothing is
asserted. Absence of an edge is information a vector index has no way to represent.

### Why two services?

The boundary is a genuine language mismatch. The pipeline is Python because the ML/NLP ecosystem (Z3 bindings, MLflow, evidently, the Anthropic SDK) lives there. The query service is Java because Spring Boot's HTTP server, Kafka client, and observability tooling are more mature for API serving.

### Why hexagonal architecture in the Spring Boot service?

`domain/` and `application/` import nothing from protobuf and nothing from Spring — the
generated `QueryResponse`, `Fact` and enum types appear in exactly one class,
`GraphRagGrpcAdapter`, which translates them into the service's own records.

That constraint is the whole point. Without it the gRPC contract silently becomes the
domain model, and changing transport means rewriting the application. With it,
`GraphPort` is a two-method interface that an in-memory fake can satisfy in a test with no
Spring context and no running Python service.

The enum mapping is by **name**, never ordinal: `RELATION_TYPE_COMPILES_TO` →
`COMPILES_TO`. Ordinal mapping would silently return the wrong constant the day either
enum is reordered. It caught a real typo the first time it ran.

---

## Roadmap

**graph-pipeline**

- [x] shared paper schema (Pydantic)
- [x] arXiv ingestion via API → immutable corpus snapshots
- [x] ontology: entity/relation types with domain, range, OWL properties
- [x] entity/relation extraction via structured outputs + Batch API
- [x] ontology validation and mechanical repair
- [x] canonicalization: name merging + type resolution
- [x] structural drift detector as a promotion gate
- [x] Neo4j graph builder (idempotent, with provenance)
- [x] Z3 consistency checker
- [x] alias table (`LLM` ≡ `Large Language Models`)
- [x] retrieval: question → traversal → answer with citations
- [x] hybrid entity linking (exact match, then embedding fallback above a calibrated threshold)
- [x] vector-RAG baseline and a graph-vs-vector comparison
- [x] golden QA eval harness (positive and negative cases)
- [ ] embedding drift detection (evidently AI)
- [ ] MLflow graph versioning
- [ ] Kafka producers

**query-service and infra**

- [x] `proto/` — gRPC contract definition
- [x] `graph-pipeline` — gRPC server exposing retrieval and graph stats
- [x] `query-service` — Spring Boot REST API (hexagonal)
- [x] `query-service` — gRPC outbound adapter
- [ ] `query-service` — Kafka consumer + cache invalidation
- [x] `infra/` — Docker Compose (Neo4j + Kafka, KRaft, named volumes)
- [ ] `infra/` — Prometheus + Grafana dashboards
- [ ] `k8s/` — k3s manifests
- [ ] CI: run ingest → extract → drift gate → build on every push

---

## Getting started

> 🚧 Under active development. Only the Python pipeline runs today.

**Prerequisites**

- Docker
- Python 3.11+
- An Anthropic API key

```bash
git clone https://github.com/your-username/arxiv-lens.git
cd arxiv-lens/graph-pipeline

python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env     # then fill in ANTHROPIC_API_KEY and NEO4J_* values

docker compose -f ../infra/docker-compose.yml up -d   # Neo4j + Kafka
```

**Run the pipeline**

```bash
python -m pipeline.ingest_arxiv --max-results 300     # arXiv → data/raw/
python -m pipeline.extract_entities --limit 0 --batch # → data/extracted/ (Batch API)
python -m pipeline.canonicalize                       # inspect merges and type conflicts
python -m pipeline.drift_detector && \
python -m pipeline.build_graph                        # gate, then load into Neo4j
python -m pipeline.consistency_checker                # Z3 over the assembled graph
```

**Ask it something**

```bash
python -m retrieval.graph_rag "What builds on Logic Tensor Networks?" --verbose
python -m eval.run_eval --offline    # score retrieval, no API calls
python -m eval.compare               # graph vs the vector baseline
```

**Serve it over gRPC**

Stubs are generated rather than committed, so regenerate them after cloning or
after editing the contract:

```bash
python -m grpc_tools.protoc -I ../proto \
  --python_out=generated --grpc_python_out=generated ../proto/graphrag.proto

python -m retrieval.grpc_server --port 50051
```

Extraction is cached by `(arxiv_id, version)`, so re-running it only calls the API for
papers that are new or revised. The Neo4j browser is at <http://localhost:7474>.

**Run the API**

```bash
cd ../query-service
./gradlew bootRun        # http://localhost:8082

curl -s localhost:8082/api/stats
curl -s -X POST localhost:8082/api/query \
  -H 'Content-Type: application/json' \
  -d '{"question":"What builds on Logic Tensor Networks?"}'
```

Sending `intent` and `entities` alongside the question skips the planner entirely — no
model call, deterministic, which is what the integration tests use:

```bash
-d '{"question":"...","intent":"LINEAGE","entities":["Logic Tensor Networks"]}'
```

Port 8082 rather than 8080 because Docker Desktop holds 8080 and 8081 on the development
machine; change `server.port` in `application.yml` if yours are free. The gRPC stubs are
generated by the Gradle protobuf plugin from `../proto`, so both services always build
from one contract.

Compose reads `${NEO4J_USERNAME}` and `${NEO4J_PASSWORD}` from an `.env` beside the
compose file, falling back to `neo4j`/`password`. Either create `infra/.env` with those
two values or pass `--env-file`; don't point it at `graph-pipeline/.env`, which also
holds the Anthropic key and has no business inside a database container.

---

## License

MIT — see [LICENSE](LICENSE)

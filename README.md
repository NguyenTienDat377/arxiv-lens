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

The Python pipeline runs end to end. The Java service has not been started.

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
| Retrieval, eval, MLflow, Kafka, query-service | ⬜ not started |

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
│         query-service (Java / Spring Boot)      ⬜ planned    │
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
| Embedding drift detection     | evidently AI, sentence-transformers    | ⬜ |
| Message broker                | Apache Kafka                           | ⬜ |
| API framework                 | Spring Boot 3 (Java 21)                | ⬜ |
| Build tool                    | Gradle                                 | ⬜ |
| Observability                 | Prometheus + Grafana                   | ⬜ |
| Container orchestration       | Docker Compose (dev) · k3s (prod-like) | ⬜ |

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
│   ├── retrieval/                   # ⬜ GraphRAG query logic
│   ├── eval/                        # ⬜ golden QA set, quality gate
│   ├── kafka/                       # ⬜ Kafka producers
│   ├── mlflow/                      # ⬜ graph version tracking
│   ├── requirements.txt
│   └── Dockerfile
├── proto/                           # ⬜ gRPC contracts
├── infra/                           # ⬜ docker-compose, Prometheus, Grafana
├── k8s/                             # ⬜ k3s manifests
├── query-service/                   # ⬜ Java Spring Boot service (hexagonal)
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

### Why two services?

The boundary is a genuine language mismatch. The pipeline is Python because the ML/NLP ecosystem (Z3 bindings, MLflow, evidently, the Anthropic SDK) lives there. The query service is Java because Spring Boot's HTTP server, Kafka client, and observability tooling are more mature for API serving.

### Why hexagonal architecture in the Spring Boot service?

The outbound gRPC adapter can be swapped for an in-memory mock in tests without touching the domain or application layer. Textbook-motivated, not architecture for its own sake.

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
- [ ] alias table (`LLM` ≡ `Large Language Models`)
- [ ] retrieval: question → traversal → answer with citations
- [ ] golden QA eval harness
- [ ] embedding drift detection (evidently AI)
- [ ] MLflow graph versioning
- [ ] Kafka producers

**query-service and infra**

- [ ] `proto/` — gRPC contract definition
- [ ] `query-service` — Spring Boot REST API (hexagonal)
- [ ] `query-service` — gRPC outbound adapter
- [ ] `query-service` — Kafka consumer + cache invalidation
- [ ] `infra/` — Docker Compose full stack
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

docker run -d --name neo4j \
  -p 7474:7474 -p 7687:7687 \
  -v arxiv_lens_neo4j_data:/data \
  -e NEO4J_AUTH=neo4j/password \
  --restart unless-stopped \
  neo4j:5
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

Extraction is cached by `(arxiv_id, version)`, so re-running it only calls the API for papers that are new or revised. The Neo4j browser is at <http://localhost:7474>.

---

## License

MIT — see [LICENSE](LICENSE)

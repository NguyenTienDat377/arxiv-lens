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
| GraphRAG retrieval with citations | ✅ 6 traversal intents, hybrid entity linking |
| Golden QA eval | ✅ 18 cases, 12 positive and 6 negative |
| Vector-RAG baseline and comparison | ✅ graph 100% vs vector 61% paper recall |
| Docker Compose (Neo4j + Kafka) | ✅ |
| Kafka producers, MLflow, query-service | ⬜ not started |

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
│  ┌──────────────┐    ┌──────────────┐             │
│  │ Quality gate │───▶│ MLflow graph │                      │
│  │ (golden QA)  │    │ versioning   │                      │
│  └──────────────┘    └──────┬───────┘                      │
│                             │ graph.updated (Kafka)      │
└─────────────────────────────┼──────────────────────────────┘
                              │
          ┌───────────────────┘
          ▼  Kafka
┌──────────────────────────────────────────────────────────────┐
│         query-service (Java / Spring Boot)         │
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
| Embedding drift detection     | Evidently (permutation null)            | ✅ |
| Message broker                | Apache Kafka (KRaft), producer + consumer | ✅ |
| API framework                 | Spring Boot 4 (Java 21)                | ✅ |
| Build tool                    | Gradle                                 | ✅ |
| Observability                 | Prometheus + Grafana                   | ⬜ |
| Service contract              | gRPC + protobuf                        | ✅ |
| Container images              | Docker, multi-stage, non-root          | ✅ |
| CI                            | GitHub Actions (lint + tests)          | ✅ |
| Container orchestration (dev) | Docker Compose                         | ✅ |
| Container orchestration (prod-like) | Kubernetes (minikube locally)    | ✅ |

Drift detection is of two kinds, measuring different things and gating differently: **structural** drift is a diff of the extracted graph between snapshots and exits non-zero, **semantic** drift compares abstract embeddings and reports. See [Two kinds of drift](#two-kinds-of-drift).

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
│   │   ├── consistency_checker.py   # Z3 encoding of the ontology's properties
│   │   ├── events.py                # publishes graph.updated to Kafka
│   │   ├── embeddings.py            # shared MiniLM cache for baseline, linking, drift
│   │   └── embedding_drift.py       # semantic drift between two sets of abstracts
│   ├── tests/                       # repair, canonicalization, drift gate
│   ├── data/raw/<snapshot-id>/      # immutable corpus snapshots (gitignored)
│   ├── data/extracted/<snapshot-id>/# extraction results, keyed by (arxiv_id, version)
│   ├── retrieval/
│   │   ├── graph_rag.py             # plan → link → traverse → cited answer
│   │   ├── vector_rag.py            # conventional RAG baseline over abstracts
│   │   └── grpc_server.py           # serves retrieval over the proto contract
│   ├── eval/
│   │   ├── golden.json              # questions with ground truth from the abstracts
│   │   ├── run_eval.py              # scores retrieval, exits 1 below threshold
│   │   └── compare.py               # graph vs vector on the same questions
│   ├── requirements.txt
│   ├── requirements-dev.txt
│   └── Dockerfile                   # multi-stage; context is the repo root
├── proto/graphrag.proto             # GraphRagService: Query, GetGraphStats
├── infra/docker-compose.yml         # Neo4j + Kafka (KRaft), named volumes
├── query-service/                   # Java Spring Boot service (hexagonal)
│   └── src/main/java/.../queryservice/
│       ├── domain/                  # records + enums, no framework imports
│       ├── application/             # GraphPort, QueryUseCase (@Cacheable)
│       ├── adapter/in/web/          # REST controller, ProblemDetail handler
│       ├── adapter/in/kafka/        # graph.updated listener → cache eviction
│       └── adapter/out/grpc/        # the only class that imports protobuf
│   └── Dockerfile                   # gradle build stage → JRE runtime stage
├── .dockerignore                    # root, because both builds share that context
├── .github/workflows/ci.yml         # lint + tests for both services
├── k8s/                             # Kubernetes manifests
│   ├── 00-namespace.yml
│   ├── 01-secrets.example.yml       # template; the real Secret is gitignored
│   ├── 02-neo4j.yml                 # StatefulSet + PVC + headless Service
│   ├── 03-kafka.yml                 # StatefulSet + PVC + headless Service
│   ├── 04-graph-pipeline.yml        # PVC + Deployment + ClusterIP Service
│   ├── 05-query-service.yml         # Deployment + NodePort Service
│   └── 06-build-graph-job.yml       # one-shot Job: snapshots → Neo4j
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

It exits non-zero, so `drift_detector && build_graph` refuses to load a bad snapshot. Churn is measured only on papers present in *both* snapshots at the same version — those are served from cache and cannot legitimately change, so any difference means something upstream moved (an edited prompt, a changed ontology). New and removed papers are explained by corpus growth and excluded from the measurement. The semantic counterpart deliberately does not gate — see [Two kinds of drift](#two-kinds-of-drift).

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

The outbound gRPC adapter can be swapped for an in-memory mock in tests without touching the domain or application layer. Textbook-motivated, not architecture for its own sake.

---

## Two kinds of drift

`drift_detector.py` asks a structural question: given the same papers at the
same versions, did extraction produce a different graph? That is a promotion
gate and it exits 1, because the only honest cause is the extractor changing
under you.

`embedding_drift.py` asks a semantic one: are the papers themselves still about
the same things? It reports rather than gates. A corpus that moves into new
topics is the normal life of a research field, not a defect — but it does
invalidate things calibrated on the old distribution, specifically entity
linking's 0.80 threshold and the golden eval's ground truth. `--fail-on-drift`
exists for a caller that wants it to gate.

Two signals have to be read together:

```
classifier AUC   0.482 (> 0.6 means separable)
centroid cosine  0.0084
```

The domain classifier tries to tell reference papers from current ones; 0.5 is
"indistinguishable" and anything it can learn is drift in some direction. The
centroid distance only catches a shift in the mean. Split the corpus at its
median publication date and neither fires. Split it into papers that do and do
not mention vision, and both do (AUC 0.86), which is how the detector was
verified — "no drift found" is indistinguishable from a broken detector until
you make it find something.

**Why the centroid null is hand-rolled.** Evidently bootstraps this threshold by
resampling both halves from the reference set with replacement, so the two
samples overlap and their centroids sit closer than two independent sets would,
and it estimates a 95th percentile from 100 draws. Over 40 pairs drawn from an
identical distribution it reported drift 22 times, while detecting a real shift
in only 9 of 20. A permutation test — pool both sides, shuffle, split, repeat —
scored 3/40 and 20/20 on the same data. Evidently still runs the classifier and
renders the HTML report; the centroid null is computed here.

---

## Tests and CI

```
graph-pipeline   ruff + 25 pytest cases   graph-pipeline/tests/
query-service    31 JUnit cases           ./gradlew test
```

Both run on every push ([.github/workflows/ci.yml](.github/workflows/ci.yml)).

The Python tests cover the three places where a silent mistake would corrupt
the graph rather than crash the build: the repair rule's three-way lookup, name
canonicalization, and the drift gate's two ratios. Several are regression tests
for bugs that actually shipped — `CLIPS` being merged into `CLIP` by the plural
rule, lost edges being divided by the number of changed papers instead of the
shared edge count, and ontology violations being counted rather than rated so
that simply extracting more papers looked like degradation.

The Java tests need no Docker: the Kafka listener is exercised against an
in-process broker (`@EmbeddedKafka`) and the gRPC adapter against a real
in-process gRPC server, so enum mapping is verified by name rather than by
ordinal.

**What CI deliberately does not do.** The roadmap originally called for
`ingest → extract → drift gate → build` on every push. It cannot: ingestion
depends on the arXiv API, extraction costs money and is non-deterministic, the
drift gate needs two snapshots that are not in the repository, and the builder
needs a populated Neo4j. Running that per-push would make the pipeline's cost
and the arXiv API's availability into gates on unrelated commits. It belongs on
a schedule with secrets, which is the remaining roadmap item.

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
- [x] unit tests for repair, canonicalization and the drift gate
- [x] embedding drift detection (Evidently, with a permutation null)
- [ ] MLflow graph versioning
- [x] Kafka producers (`graph.updated` published after a successful build)

**query-service and infra**

- [x] `proto/` — gRPC contract definition
- [x] `graph-pipeline` — gRPC server exposing retrieval and graph stats
- [x] `query-service` — Spring Boot REST API (hexagonal)
- [x] `query-service` — gRPC outbound adapter
- [x] `query-service` — Kafka consumer + cache invalidation
- [x] `infra/` — Docker Compose (Neo4j + Kafka, KRaft, named volumes)
- [ ] `infra/` — Prometheus + Grafana dashboards
- [x] `k8s/` — Kubernetes manifests (StatefulSets, PVCs, Secret, Job, NodePort)
- [x] CI: lint and tests for both services on every push
- [ ] CI: scheduled pipeline run (ingest → extract → drift gate → build) behind secrets

---

## Getting started

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

**Run the query service**

```bash
cd ../query-service
./gradlew bootRun            # :8082, talks to the gRPC server above

curl -s localhost:8082/actuator/health
curl -s -X POST localhost:8082/api/query \
  -H 'Content-Type: application/json' \
  -d '{"question": "What builds on Logic Tensor Networks?"}'
curl -s localhost:8082/api/stats
```

**Or run the whole stack in containers**

```bash
cd infra
docker compose --profile app up --build -d
```

Without `--profile app` compose starts Neo4j and Kafka only, which is the loop above:
services on the host, infrastructure in Docker. With it, both services are built from
the repository root — they share `proto/`, so neither can be built from its own
directory — and every address switches from `localhost` to a service name
(`neo4j:7687`, `kafka:9092`, `graph-pipeline:50051`). Each is an environment variable
with a localhost default, so the same images serve both modes.

**Or run it on Kubernetes**

```bash
minikube start --cpus 4 --memory 6g --disk-size 40g
kubectl apply -f k8s/00-namespace.yml
kubectl config set-context --current --namespace=arxiv-lens

# Images are built locally, so they have to be loaded into the cluster's own
# image store — a cluster cannot see your Docker daemon.
minikube image load arxiv-lens-graph-pipeline:latest
minikube image load arxiv-lens-query-service:latest

# The Secret is created from .env, never committed. See 01-secrets.example.yml.
PW=$(grep NEO4J_PASSWORD graph-pipeline/.env | cut -d= -f2)
kubectl create secret generic arxiv-lens-secrets \
  --from-literal=ANTHROPIC_API_KEY="$(grep ANTHROPIC_API_KEY graph-pipeline/.env | cut -d= -f2)" \
  --from-literal=NEO4J_PASSWORD="$PW" \
  --from-literal=NEO4J_AUTH="neo4j/$PW"

kubectl apply -f k8s/
```

The cluster's volumes start empty, unlike the bind mount compose uses, so the
snapshots are copied in once and the graph is built by a Job:

```bash
POD=$(kubectl get pod -l app=graph-pipeline -o jsonpath='{.items[0].metadata.name}')
kubectl cp graph-pipeline/data/raw       $POD:/app/data/raw
kubectl cp graph-pipeline/data/extracted $POD:/app/data/extracted

kubectl apply -f k8s/06-build-graph-job.yml
kubectl logs -f job/build-graph        # +300 papers, +1742 entities, +1244 relations

kubectl port-forward svc/query-service 8083:8082
curl -s localhost:8083/api/stats
```

Every address is unchanged from Docker Compose — `bolt://neo4j:7687`,
`kafka:9092`, `static://graph-pipeline:50051` — because a Kubernetes Service
provides the same name-based discovery the compose network did. That only works
because each one is an environment variable rather than a literal in the code.

Extraction is cached by `(arxiv_id, version)`, so re-running it only calls the API for
papers that are new or revised. The Neo4j browser is at <http://localhost:7474>.

Compose reads `${NEO4J_USERNAME}` and `${NEO4J_PASSWORD}` from an `.env` beside the
compose file, falling back to `neo4j`/`password`. Either create `infra/.env` with those
two values or pass `--env-file`; don't point it at `graph-pipeline/.env`, which also
holds the Anthropic key and has no business inside a database container.

---

## License

MIT — see [LICENSE](LICENSE)

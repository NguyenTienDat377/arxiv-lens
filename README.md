# arxiv-lens

> A GraphRAG system that tracks how knowledge evolves — detecting when the research corpus drifts, and refusing to promote a knowledge graph that shouldn't ship.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Java](https://img.shields.io/badge/Java-21-orange.svg)](https://openjdk.org/)
[![Python](https://img.shields.io/badge/Python-3.11-blue.svg)](https://www.python.org/)
[![Spring Boot](https://img.shields.io/badge/Spring%20Boot-4.1-brightgreen.svg)](https://spring.io/projects/spring-boot)
[![Kubernetes](https://img.shields.io/badge/Kubernetes-manifests-326ce5.svg)](k8s/)

---

## What is this?

Most GraphRAG demos stop at "build the graph, query it." **arxiv-lens** asks the harder questions: _what happens when the corpus changes, and how do you know the graph is any good?_

This project builds a drift-aware GraphRAG pipeline over arXiv papers in the neuro-symbolic AI subfield. Extraction is constrained by a hand-derived ontology rather than left open-ended, so the schema can be enforced — violating edges are repaired mechanically where the ontology forces a single answer, and reported where it doesn't. Two different drift questions are asked and answered differently: *did the
extractor change* is structural, measured on papers that cannot legitimately have
changed, and it blocks promotion with a non-zero exit; *did the corpus change* is
semantic, measured on abstract embeddings, and it reports rather than blocks
because a field moving into new topics is normal. The assembled graph is then
checked by an SMT solver for contradictions no single paper contains.

The system is split into two services connected by Kafka (async events) and gRPC
(synchronous queries), served through a Spring Boot REST API. Both are
containerised and deploy to Kubernetes; a rebuilt graph announces itself on Kafka
so the API never serves a stale answer, every build is recorded as an MLflow run,
and the running service is scraped by Prometheus into a provisioned Grafana
dashboard.

---

## Current status

Both services run end to end on Docker Compose, and on a local Kubernetes
cluster from the manifests in `k8s/`. A question entering the REST API is answered from the graph with per-fact citations, and a
rebuilt graph invalidates the API's caches over Kafka without a deploy.

All 28 roadmap items are done. The pipeline itself runs on a schedule behind
secrets rather than on every push, because extraction costs money — see
[Scheduled pipeline run](#scheduled-pipeline-run).

| Stage | Status |
| ----- | ------ |
| arXiv ingestion → immutable snapshots | ✅ 300 papers |
| Ontology (6 entity types, 6 relations, OWL properties) | ✅ |
| Entity/relation extraction (structured outputs) | ✅ 2047 entity mentions, 1245 relations |
| Ontology repair + validation | ✅ 13 repaired, 11 remaining (0.9%) |
| Canonicalization (name merge, type resolution) | ✅ 1808 → 1742 distinct names |
| Drift gate | ✅ blocks promotion, non-zero exit |
| Neo4j graph builder | ✅ 2042 nodes, 3291 edges, idempotent |
| Z3 consistency checker | ✅ minimal unsat cores |
| GraphRAG retrieval with citations | ✅ 6 traversal intents, hybrid entity linking |
| Golden QA eval | ✅ 18 cases, 12 positive and 6 negative |
| Vector-RAG baseline and comparison | ✅ graph 100% vs vector 61% paper recall |
| Docker Compose (Neo4j + Kafka) | ✅ |
| Kafka producer + consumer | ✅ `graph.updated` → cache eviction |
| query-service (Spring Boot, hexagonal) | ✅ REST → gRPC → Neo4j, 31 tests |
| Embedding drift monitor | ✅ Evidently classifier + permutation null |
| MLflow graph versioning | ✅ one run per build, params/metrics/commit |
| Docker images + Kubernetes | ✅ both services, non-root, StatefulSets + Job |
| Prometheus + Grafana | ✅ 5 provisioned panels |
| CI | ✅ lint + 60 tests on every push |
| Scheduled pipeline | ✅ gated cron, cost guard, drift gate blocks the build |

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
| Experiment / graph versioning | MLflow (SQLite backend)                | ✅ |
| Embeddings (baseline + linking) | sentence-transformers (MiniLM)       | ✅ |
| Embedding drift detection     | Evidently (permutation null)            | ✅ |
| Message broker                | Apache Kafka (KRaft), producer + consumer | ✅ |
| API framework                 | Spring Boot 4 (Java 21)                | ✅ |
| Build tool                    | Gradle                                 | ✅ |
| Observability                 | Prometheus + Grafana (provisioned)     | ✅ |
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
│   │   ├── embedding_drift.py       # semantic drift between two sets of abstracts
│   │   └── tracking.py              # one MLflow run per graph build
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
├── infra/
│   ├── docker-compose.yml           # Neo4j + Kafka, and two opt-in profiles
│   ├── prometheus/prometheus.yml    # scrape config
│   └── grafana/
│       ├── provisioning/            # datasource + dashboard provider
│       └── dashboards/              # the dashboard JSON, in git not in a volume
├── query-service/                   # Java Spring Boot service (hexagonal)
│   └── src/main/java/.../queryservice/
│       ├── domain/                  # records + enums, no framework imports
│       ├── application/             # GraphPort, QueryUseCase (@Cacheable)
│       ├── adapter/in/web/          # REST controller, ProblemDetail handler
│       ├── adapter/in/kafka/        # graph.updated listener → cache eviction
│       ├── adapter/out/grpc/        # the only class that imports protobuf
│       └── config/                  # Caffeine cache, Micrometer histograms
│   └── Dockerfile                   # gradle build stage → JRE runtime stage
├── .dockerignore                    # root, because both builds share that context
├── .github/workflows/
│   ├── ci.yml                       # lint + tests for both services, every push
│   └── pipeline.yml                 # ingest → extract → gate → build, scheduled
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

## What the dashboards show

Five panels, provisioned from [infra/grafana/dashboards/arxiv-lens.json](infra/grafana/dashboards/arxiv-lens.json)
rather than clicked together, so the dashboard is reviewable in a diff:

| panel | query |
| --- | --- |
| Request rate | `sum(rate(http_server_requests_seconds_count[5m])) by (uri, status)` |
| Latency p95 | `histogram_quantile(0.95, sum(rate(http_server_requests_seconds_bucket[5m])) by (le, uri))` |
| Cache hit ratio | `sum(rate(cache_gets_total{result="hit"}[5m])) by (cache) / …` |
| JVM heap | `sum(jvm_memory_used_bytes{area="heap"})` |
| Cache evictions | `sum(cache_evictions_total) by (cache)` |

The cache panels are the ones specific to this system. A cold `/api/stats` takes
**4.1 s** — gRPC to Python, a traversal, a count over every label — and the same
call served from Caffeine takes **8 ms**. The hit ratio falling to zero and
climbing back is a `graph.updated` event arriving from Kafka and the listener
evicting, made visible.

Two things had to be turned on for any of it to work. Caffeine records no
statistics unless asked, so `recordStats()` is required or every cache meter
reports zero forever; and Micrometer exports timers as count/sum/max, so the
`_bucket` series `histogram_quantile()` needs comes from a `MeterFilter` bean
([MetricsConfig.java](query-service/src/main/java/com/arxivlens/queryservice/config/MetricsConfig.java)) —
Spring Boot 4 removed the `management.metrics.distribution.*` properties that
did this in Boot 3.

---

## Comparing builds

Each graph build records one MLflow run ([pipeline/tracking.py](graph-pipeline/pipeline/tracking.py)):

```
PARAMS   snapshot_id, extraction_model, papers_in_snapshot, the three drift thresholds
METRICS  papers/entities/mentions/relations created, repairs, unresolved_types, violation_rate
TAGS     git_commit
```

Snapshots and the `(arxiv_id, version)` extraction cache already made a build
*reproducible*; this makes builds *comparable*, which is the question that
actually gets asked — did anything move, and at which commit.

`violation_rate` rather than a violation count, for the same reason the drift
gate measures a rate: a larger corpus has more violations without being any
worse.

Tracking never fails a build. The graph is committed to Neo4j before the run is
logged, so a tracking outage prints a warning and the build still exits 0 — the
rule the Kafka producer follows. That was not hypothetical: MLflow 3 put the
`./mlruns` file backend into maintenance mode and refuses to write to it, the
first call raised, and the build carried on. The store is SQLite now.

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
graph-pipeline   ruff + 29 pytest cases   graph-pipeline/tests/
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

**What per-push CI deliberately does not do.** `ingest → extract → drift gate →
build` is not on `ci.yml`. Ingestion depends on the arXiv API, extraction costs
money and is non-deterministic, the drift gate needs two snapshots that are not
in the repository, and the builder needs a populated Neo4j. Running it per-push
would make the pipeline's cost and arXiv's availability into gates on unrelated
commits. It runs on a schedule instead.

## Scheduled pipeline run

[.github/workflows/pipeline.yml](.github/workflows/pipeline.yml) runs the four
stages against the live Neo4j. It differs from `ci.yml` in every way that
matters: it spends money, it writes to a real database, and it is stateful.

Setup, once:

| Where | Name | Value |
| ----- | ---- | ----- |
| Environment `production` → secrets | `ANTHROPIC_API_KEY` | extraction |
| | `NEO4J_URL` | `neo4j+s://<id>.databases.neo4j.io` |
| | `NEO4J_USERNAME` / `NEO4J_PASSWORD` | Aura credentials |
| Repository → variables | `PIPELINE_SCHEDULE_ENABLED` | `true` to arm the cron |

The `production` environment scopes the credentials to this one job and is where
a required reviewer goes, if you want a human to approve anything that spends.

**The cron is committed but inert.** The job's `if` requires either a manual
dispatch or `PIPELINE_SCHEDULE_ENABLED == 'true'`, so the schedule is turned on
and off from the settings page without a commit. A workflow that starts billing
the moment it is merged is not one you want to merge.

**Cost control is the reuse index, and the guard exists because it can vanish.**
`load_extraction_index()` reuses any `(arxiv_id, version)` already extracted, so
a normal week pays for the handful of new papers. That index lives in
`data/extracted/`, which is gitignored, so the workflow carries it between runs
in an `actions/cache`. Caches are best-effort — evicted after seven days unused,
dropped when the repository's 10 GB fills — and on a cache miss every paper looks
new. So `extract_entities` takes `--max-new`: above that many pending papers it
exits non-zero *before* submitting the batch. Nothing that costs money is allowed
to depend on a cache being there.

It refuses rather than truncating on purpose. A truncated 40-paper snapshot would
**pass** the drift gate, which measures papers that changed, not papers that are
missing — and would then be loaded and recorded in MLflow as the state of the
corpus. Exiting is the only behaviour that cannot lie.

**The cache is saved after the build, not in a post-step.** `actions/cache` saves
even when the job failed. If a drift-blocked run saved its state, the next run
would compare against the snapshot that was just rejected — the gate would
quietly re-baseline onto its own failure. `cache/restore` and `cache/save` are
split so a blocked run leaves the baseline alone; the rejected snapshot still
uploads as an artifact for reading.

Two integrations are absent by design: no Kafka broker is reachable from a
runner, so `publish_graph_updated()` reports "not published" and the build
continues, which is what that module's blanket `try/except` is for; and MLflow
writes to a throwaway SQLite file that is uploaded as an artifact.

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
- [x] MLflow graph versioning (params, metrics, git commit per build)
- [x] Kafka producers (`graph.updated` published after a successful build)

**query-service and infra**

- [x] `proto/` — gRPC contract definition
- [x] `graph-pipeline` — gRPC server exposing retrieval and graph stats
- [x] `query-service` — Spring Boot REST API (hexagonal)
- [x] `query-service` — gRPC outbound adapter
- [x] `query-service` — Kafka consumer + cache invalidation
- [x] `infra/` — Docker Compose (Neo4j + Kafka, KRaft, named volumes)
- [x] `infra/` — Prometheus + Grafana dashboards (provisioned from files)
- [x] `k8s/` — Kubernetes manifests (StatefulSets, PVCs, Secret, Job, NodePort)
- [x] CI: lint and 60 tests for both services on every push
- [x] CI: scheduled pipeline run (ingest → extract → drift gate → build) behind secrets

---

## Getting started

### The 90-second demo

```bash
git clone https://github.com/NguyenTienDat377/arxiv-lens.git
cd arxiv-lens
cp graph-pipeline/.env.example graph-pipeline/.env   # add ANTHROPIC_API_KEY

./scripts/demo.sh up      # build + start everything, wait for health
./scripts/demo.sh seed    # load the 300-paper graph into Neo4j (once)
./scripts/demo.sh check   # graph stats through REST -> gRPC -> Neo4j (free)
./scripts/demo.sh query "What builds on Logic Tensor Networks?"   # cited answer (a few cents)
./scripts/demo.sh down
```

`up` prints every URL: the REST API on `:8082`, Swagger on `/swagger-ui.html`,
the Neo4j browser on `:7474`, Grafana on `:3000`, Prometheus on `:9090`.

Sample responses are checked in under [`docs/demo/`](docs/demo/) so the output
is visible without running anything:

- [`api-stats.json`](docs/demo/api-stats.json) — 300 papers, 1742 entities, 1244 relations, broken out by type
- [`api-query-ltn.json`](docs/demo/api-query-ltn.json) — *"What builds on Logic Tensor Networks?"* answered from three `EXTENDS` edges, each citing its arXiv id
- [`api-query-hallucination.json`](docs/demo/api-query-hallucination.json) — eleven methods, eleven citations, no fact without a paper behind it

### Everything the manual way

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

**Watch it**

```bash
# Dashboards: Prometheus scrapes query-service, Grafana is provisioned from files.
cd infra
docker compose --profile app --profile observability up -d

open http://localhost:9090/targets     # query-service should read UP
open http://localhost:3000             # admin / admin, folder "arxiv-lens"
```

```bash
# Build history: one MLflow run per graph build, with the git commit attached.
cd graph-pipeline
mlflow ui --backend-store-uri sqlite:///mlflow.db
```

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

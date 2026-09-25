# System walkthrough

`README.md` explains *what was decided and why*. `comment.md` explains each module in
isolation. Neither answers a simpler question: if you pick one paper and one question and
follow them through the whole system in order, what actually happens, and why does each
handoff exist rather than the stage before it just doing more?

This is that trace. Two journeys, because the system genuinely has two of them running at
different times, on different triggers, for different reasons:

1. **A paper's journey** — arXiv → an assertion in Neo4j. Offline, batched, expensive,
   runs on a schedule.
2. **A question's journey** — an HTTP request → a cited answer. Online, one request at a
   time, cheap enough to run per click, runs on demand.

They only meet at Neo4j. The pipeline never talks to the query service directly, and the
query service never touches arXiv, an LLM extraction call, or a snapshot file. That
separation is the first thing worth understanding, because almost every design decision
downstream of it exists to preserve it.

---

## Journey 1 — a paper becomes a graph edge

### Why this has to be a pipeline of *stages*, not one script

Six things happen to a paper before it's a queryable fact: fetch it, extract entities from
it, merge its names with everyone else's, check whether anything just changed, load it into
Neo4j, and check the assembled graph for contradictions. Each of those is a `python -m
pipeline.X` command in the README, run in sequence, each reading and writing files rather
than passing objects in memory within one process.

The reason isn't modularity for its own sake — it's that **stage 4, drift detection, needs
stage 2's output to exist twice**: once from the previous run, once from this one, both on
disk, byte-for-byte unchanged from when they were written. If ingestion→extraction→build
were one script, "the previous run's extraction" wouldn't be a thing you could point at —
it would already be overwritten or garbage-collected. Splitting into stages that read/write
snapshot ids is what makes "did anything change?" an answerable question at all.

### Step 1 — `ingest_arxiv.py`: fetch, and stop

This is the *only* module in the whole system that calls a network API arXiv doesn't
control (later stages call the Anthropic API, but never arXiv again). It fetches papers
matching a neuro-symbolic AI query, deduplicates them by `arxiv_id` keeping the highest
`version` (`snapshots.py`'s `dedupe`), and writes the result as a **snapshot**:

```
data/raw/2026-08-14T09-27-10Z/
├── papers.jsonl
└── metadata.json
```

Why a snapshot and not a live query every time downstream code needs papers? Because drift
is *defined* as a difference between two snapshots — comparing "the corpus as of Tuesday" to
"the corpus as of Thursday" only means something if both of those are still sitting on disk,
unchanged, when you ask the question weeks later. A live re-query would give you today's
corpus twice.

The write itself is atomic — built in a temp directory, moved into place with one `rename` —
specifically so a network blip mid-fetch can never leave a *partial* snapshot on disk. A
half-written snapshot wouldn't error; it would look like 200 papers instead of 800, and the
drift detector two stages later would read that as a massive, false content shift. The
atomicity closes a failure mode that would otherwise surface as a false alarm in a completely
different module.

### Step 2 — `extract_entities.py`: constrained, cached, and idempotent by construction

Each paper's abstract goes to Claude with a structured-output schema built from
`ontology.py`'s six entity types and six relations. "Structured output" here means something
specific: the closed `StrEnum`s become the JSON schema's `enum` field, so the model is not
merely asked to use one of six relation names — it is decoding-constrained to. It cannot
emit `IMPROVES_UPON` any more than it can emit a token outside its vocabulary. This is the
difference between this project and schema-free extraction (Microsoft's GraphRAG-style
approach): across 300 abstracts, schema-free extraction of "X uses Y" independently produces
`USES`, `uses`, `utilizes`, `LEVERAGES`, `IS_BUILT_ON` — five relation types for one relation
— and the resulting graph has no single traversal that means "depends on." A closed
vocabulary is what makes the graph queryable at all; the cost is that a genuinely novel
relation the model encounters just doesn't get expressed, which is why `ontology.py` gets
revisited (see the `USES` widening in `comment.md`) rather than treated as fixed forever.

Two things make this stage's output trustworthy enough for a *diff* to mean anything three
stages later:

- **`validate()` then `repair()`.** The model still gets domain/range wrong sometimes — an
  abstract that says "we demonstrate through case studies" reads as `EVALUATED_ON` to the
  model even with no dataset named. `repair()` asks the ontology which predicates *would*
  legally connect the two endpoint types it actually produced: exactly one candidate →
  rewrite (forced, not guessed); zero → drop (no legal name for that shape); several → leave
  it for a human, because picking one would fabricate a specific claim nobody made. This is
  mechanical repair, not model-in-the-loop correction — it's why the ontology table in the
  README is described as doing four jobs, not one.
- **The `(arxiv_id, version)` cache.** The same prompt against the same abstract produces a
  *different* extraction on a second run — entities appear and disappear between calls. This
  isn't a temperature/sampling artifact you can tune away (Sonnet 5's structured-output mode
  rejects those parameters); it's inherent nondeterminism in the extraction itself. So instead
  of chasing determinism statistically, the system enforces it architecturally: extract an
  abstract exactly once, key the result on `(arxiv_id, version)`, and reuse that result
  forever after, even across pipeline runs months apart. This single decision is what makes
  drift detection possible — a diff between two snapshots is only informative if "the same
  paper produces the same extraction" is actually true, and the cache is what makes that true
  by construction rather than by luck.

### Step 3 — `canonicalize.py`: many surface forms, one node

Extraction happens per-abstract, so "LLM," "LLMs," and "large language models" can all
appear as distinct strings across different papers' outputs. Left alone, that means a query
for "what uses LLMs" silently misses every paper that happened to say "LLM." Vector search
tolerates this for free — nearby strings embed to nearby vectors — but graph traversal is
exact string matching against node identities, so this problem has to be solved before
anything reaches Neo4j, not papered over at query time.

`canonicalize.py` normalizes each name into a grouping key (lowercase, punctuation stripped,
plurals removed) purely to *decide which names are the same concept* — the key itself never
becomes a node name; the most-mentioned real surface form does, so the graph reads "LLM," not
"llm." Merging surface forms then creates a second problem: `Chain-of-Thought` might be typed
`METHOD` by six papers and `FORMALISM` by three, and Neo4j needs exactly one label. That's
resolved by a cascade of decreasing certainty — first the ontology's domain/range constraints
vote (an edge that only makes sense if this node has a particular type is stronger evidence
than a mention count), then plain majority, then a fixed precedence order as a last resort.
Only that last, weakest tier gets logged as "unresolved" for review, because the first two
tiers have an actual justification and the third doesn't.

This stage also runs `repair()` a second time, because merging can make a per-paper-legal
edge illegal: a name typed `TASK` by the one paper that used it becomes legal under
`ADDRESSES`, but if canonicalization resolves that same name to `FORMALISM` corpus-wide,
the edge is now illegal by the exact rule that made it legal a moment ago. The ontology gets
the last word after every merge, not just after every extraction.

### Step 4 — `drift_detector.py`: the gate, not a dashboard

This is where the two prior design choices — snapshots and the extraction cache — pay off.
Given snapshot N and N+1, papers split into three groups: added, removed, and shared. Added
and removed are corpus growth and are *ignored* — 150 new papers is not drift, it's a bigger
corpus. Only the **shared** papers matter, because those were served from the cache
untouched — meaning if their extraction differs between N and N+1, the only possible cause
is something upstream changing (an edited prompt, a changed ontology, `--no-reuse`), never
the corpus itself. That's the whole insight: drift here isn't "did the data change," it's "did
the *thing that reads the data* change out from under us."

It's implemented as a gate — `sys.exit(1)` on excess churn — not a chart, because
`drift_detector.py && build_graph.py` is how a bad snapshot is mechanically refused
promotion rather than optimistically loaded and noticed later. The threshold itself is
0, not some tolerance band, because churn on cached papers was measured to be bimodal in
practice: a cache hit produces exactly 0% change and a cache miss produces near-100% change,
with nothing in between. There's no continuous curve to pick a cutoff on — any nonzero
number here already means something broke.

### Step 5 — `build_graph.py`: Neo4j, idempotently

Papers, entities, and relations get `MERGE`d into Neo4j — matched on identity (`arxiv_id`
for papers, `(name, label)` for entities), with everything else applied via `SET` afterward
so a corrected title doesn't spawn a duplicate node. Re-running this stage against an
unchanged snapshot should create zero new nodes and zero new edges; that's verified against
Neo4j's own write counters, not assumed.

Every edge also carries `first_seen` / `last_seen` / `snapshots: [...]`, populated so that
re-running the builder against a *growing* corpus accumulates history instead of silently
overwriting it. That provenance is what lets "the graph as it stood at snapshot N" remain an
answerable question after ten more snapshots have loaded — which matters because it's
exactly the same question drift detection needs answered.

Paper-to-paper edges are deliberately **not** stored, even though "paper A builds on paper B"
is derivable from the entity graph (if A cites concepts B introduced). arXiv doesn't return
citation data at all, and the naive entity-overlap heuristic produces mostly noise (two
papers both mentioning "LLMs" is not a citation). Materializing an inferred relationship as
if it were an extracted fact would make the drift gate report churn in something nobody
actually said, and would make the next stage verify the pipeline's own guesses instead of
the papers' claims. It's derived at query time in `graph_rag.py` instead, kept clearly
labeled as an inference rather than an assertion.

### Step 6 — `consistency_checker.py`: Z3 over the whole graph at once

Every check up to this point operates on one paper, or one merge, or one snapshot diff.
None of them can catch a contradiction that only exists across three unrelated papers: paper
1 says `A EXTENDS B`, paper 2 says `B EXTENDS C`, paper 3 says `C EXTENDS A`. No single edge
is wrong. `EXTENDS` being declared transitive in `ontology.py` means those three edges
together imply `A EXTENDS A`, and `EXTENDS` being declared irreflexive means that's
forbidden — the contradiction lives only in the conjunction, and no one paper's author
could have seen it.

A solver is the right tool here specifically because the constraints *interact* —
transitivity, irreflexivity, asymmetry, and domain/range disjointness all apply
simultaneously, and a hand-rolled check for each interaction multiplies combinatorially. Z3
just takes the conjunction and returns either "satisfiable" or a **minimal unsat core**: the
smallest set of edges that cannot all be true together, which is what turns "something is
wrong" into "these three specific edges, from these three specific papers, are wrong." The
real corpus is too sparse for this to fire today (33 `EXTENDS` edges over 300 papers), which
is exactly why it was verified by deliberately injecting a 3-cycle rather than trusted on the
strength of a clean run — a checker that has never found anything is indistinguishable from
a broken one until it's made to find something on purpose.

At this point a paper has gone from an arXiv abstract to one or more asserted, type-checked,
contradiction-free edges in Neo4j, and `pipeline/events.py` publishes `graph.updated` to
Kafka — the only thing that crosses from Journey 1 into Journey 2.

---

## Journey 2 — a question becomes a cited answer

### Why the pipeline publishes an event instead of calling the API directly

`graph.updated` fires once a day at most, which is nowhere near the throughput Kafka exists
for — an HTTP call from the pipeline straight to the query service would move the same
single byte just as fast. The reason for the broker isn't volume, it's **decoupling**: with a
direct call, the pipeline has to know the query service exists, is currently up, and how many
instances of it there are — and a graph rebuild would fail, or silently drop its
notification, because an unrelated service happened to be restarting at that moment. With a
log, the pipeline appends an event and moves on without caring who, if anyone, is listening;
a consumer that was offline for an hour resumes from its last offset and catches up on its
own. That argument is about *who depends on whom*, not about how much data moves, which is
why it holds just as well at one message a day as it would at a million a second.

### Step 1 — the request lands in Spring Boot

`POST /api/query` hits `query-service`'s REST controller, which is a thin translation layer
over `QueryUseCase` — the actual application logic lives one layer down, behind a port
(`GraphPort`), so the use case has no idea whether it's talking to a real gRPC server or, in
a test, an in-memory fake. That's the hexagonal architecture the README mentions: not
adopted for its own sake, but because it's what lets `GraphRagGrpcAdapterTest` exercise the
gRPC mapping logic against a real in-process gRPC server, and `QueryUseCaseTest` exercise the
caching and orchestration logic against a fake port, without either test needing Docker.

### Step 2 — the cache, and why it exists at all

Before anything crosses the network, `QueryUseCase` checks a Caffeine cache keyed on the
question. This exists because of a concrete, measured cost: a cold `/api/stats` call — gRPC
to Python, a graph traversal, a count over every label — takes **4.1 seconds**; the same call
served from cache takes **8 milliseconds**. That's over 500× faster for the exact same
answer, and most questions asked against a fixed graph don't need to be re-answered from
scratch.

The cache is what makes the Kafka event from Journey 1 load-bearing rather than decorative.
`graph.updated` is consumed by a Kafka listener in `query-service` whose entire job is to
evict the cache — so a rebuilt graph is served correctly on the *next* request without
anyone redeploying the query service. Without that eviction, a rebuilt Neo4j graph would sit
behind a stale cache indefinitely; without the cache, every request would pay the 4.1-second
cost regardless of how often the graph actually changes. The two exist as a pair.

### Step 3 — across the gRPC boundary, into `graph_rag.py`

A cache miss goes out over gRPC to the Python retrieval service. The `.proto` contract is
compiled independently into a Java client and a Python server that share the wire format and
otherwise know nothing about each other — Java never sees `graph_rag.py`'s code, and Python
never sees Spring Boot's.

Inside `graph_rag.py`, the LLM is deliberately used at the two ends of the pipeline and
nowhere in the middle:

```
question → plan()      LLM:  language → QueryPlan(intent, entities)
         → link()      code: entity names → actual graph node names
         → traverse()  code: a whitelisted Cypher template, parameterized
         → answer()    LLM:  retrieved facts → prose, with citations
```

`plan()` doesn't write a Cypher query — it picks one of six fixed intents (`TRAVERSALS` is a
closed dict of templates) and extracts entity names from the question, the same
closed-vocabulary discipline that shaped extraction applied to retrieval this time. The model
never gets to construct arbitrary graph traversals; it selects from a small, reviewed set.

`link()` maps the question's wording onto the graph's actual node names — "neural nets" needs
to resolve to a node literally called "neural network." It tries the free, exact match first
(the same normalization key `canonicalize.py` used to merge names, reused here so linking and
graph-building agree on what counts as the same name), then falls back to nearest-neighbor
search over entity name embeddings — but only above `LINK_THRESHOLD = 0.80`. That threshold
is the difference between "I don't know that entity" and a wrong, overconfident answer:
`argmax` over a 1742-name vocabulary *always* returns something, however unrelated, so
without a floor, a nonsense phrase like "Quantum Banana Framework" would silently link to
whatever entity name happens to be least dissimilar, and the system would then traverse and
answer as if that link were correct. 0.80 isn't a nice round default — it was picked because
every genuinely correct match in the vocabulary scores above it and every wrong one scores
below, with the nearest actual miss ("neural nets" → "deep learning") landing at 0.68.

### Step 4 — the two places the answer can legitimately be "nothing"

```python
if missing:    return "Not in the graph: ..."
if not facts:  return "The graph holds no facts matching that traversal."
```

Both of these return *before* the second LLM call, deliberately — there's nothing to
compose into prose when the entity didn't resolve or the traversal found nothing, and
skipping the call means those paths cost nothing and can be tested without an API key.

This is the structural difference from the vector-RAG baseline sitting alongside it in the
repo. `vector_rag.py`'s retrieval is `vectors @ query`, sort, take the top k — and `top-k`
*always* returns k documents, however irrelevant. Asked whether two unrelated papers relate,
the baseline hands back five plausible-looking neighbors and lets the model construct a
connection between them; the graph, correctly, returns nothing, because nothing was ever
asserted. "These two things are unrelated" is a real, useful answer that only a system
capable of returning *no facts* can give — which is also why the golden eval's negative test
cases (`expect_empty: true`) exist as a category: an empty `expect_entities` list on a
positive case scores 100% recall by doing nothing, so a negative case needs its own explicit
assertion or it passes by accident.

### Step 5 — the answer, and what crosses the wire with it

`answer()` is the second and last LLM call, and what it receives is the literal set of
`(subject, predicate, object, paper_citations)` facts the traversal returned — the same facts
cross the gRPC boundary back to Java, not just the resulting prose. That's a proto design
choice, not an afterthought: if only the final sentence crossed the wire, the REST layer
could never show *which paper* asserted a given claim without re-querying the graph. Every
fact in the answer traces back to a specific `arxiv_id`, which is the entire point of doing
this with a graph instead of loose retrieval — an unsupported claim is something the system
structurally cannot produce, because there's no path from "answer text" back to "no evidence"
that doesn't pass through an explicit, empty fact list first.

The REST controller renders that into the response, `query-service`'s static `index.html`
escapes it before selectively reintroducing markup (it's untrusted model output going into
`innerHTML`, so it's treated exactly like any other untrusted string would be), and the
question that started as free text is now an answer with a name, a predicate, and an arXiv
id attached to every clause in it.

---

## Where the two journeys actually touch

Once more, explicitly, because it's easy to lose in the detail above: **the only contact
between these two systems is Neo4j and one Kafka topic.** The pipeline writes to Neo4j and
announces it; the query service reads from Neo4j (via gRPC to Python, never directly) and
listens for the announcement. Neither one calls the other's code, shares a process, or shares
a deploy. That's what makes it true that a graph can be rebuilt — hours of extraction, a
drift check, a Z3 pass — without the query service ever going down, and what makes it
possible to run the pipeline entirely offline (`--offline` eval, a stubbed planner, fixtures
already on disk) while iterating on the query service, or vice versa, without either half
needing the other half's expensive, non-deterministic parts running at the same time.

# Pipeline notes

Personal reference — the reasoning that used to sit in code comments.

**Ingestion:** `models.py`, `ingest_arxiv.py`, `snapshots.py`
**Graph:** `ontology.py`, `extract_entities.py`, `canonicalize.py`, `drift_detector.py`, `build_graph.py`, `consistency_checker.py`
**Serving:** `proto/graphrag.proto`, `retrieval/grpc_server.py`

---

## `models.py` — the shared schema

**Pure data, no I/O.** Every downstream stage (extraction, embedding, drift, eval, graph builder) imports `Paper`. Keeping this module free of the arXiv client means importing the schema never drags in a network dependency.

### Why Pydantic, not a dataclass

We're parsing untrusted external data. Pydantic validates and coerces at the boundary, so bad data is rejected on arrival rather than discovered three stages later inside a Neo4j write. It also gives JSONL serialization free via `model_dump_json()` / `model_validate_json()`.

`BaseModel` is not a normal base class you extend — the metaclass reads the annotations and *generates* `__init__`, validators and serializers. Never write your own `__init__`: it overrides the generated one, skips validation entirely, and leaves Pydantic's internal state uninitialized.

### `parse_short_id`

arXiv short ids look like `2301.01234v2`:

```
2301.01234v2
└──┬──┘ └─┬─┘ └┬┘
   │      │    └── version: 2nd revision
   │      └─────── sequence number within that month
   └────────────── YYMM: submitted January 2023
```

`arxiv_id` is the whole `2301.01234`; the dot is part of the ID format (`YYMM.NNNNN`), not a separator between meaningful values.

**ID and version are stored as separate fields** so a revised paper stays the *same* node in the knowledge graph. Keying on the versioned string would make `2301.01234v2` a second, unrelated paper next month — which the drift detector would read as a corpus shift that never happened.

The regex `^(?P<arxiv_id>.+?)v(?P<version>\d+)$` anchors on the trailing `v<digits>`. Non-greedy `.+?` keeps the dot inside the ID, and it also handles pre-2007 ids (`hep-th/9901001v1`) that use a slash. Unversioned ids fall through to version 1 rather than raising.

> Splitting on `"."` is silently wrong: `"2301.01234v2".split(".")` → `["2301", "01234v2"]`. Every paper submitted the same month would collapse onto the ID `"2301"`.

### Field types

| Field | Type | Why |
|---|---|---|
| `arxiv_id` | `str` | not an int — has a dot, and `0704.0001` has meaningful leading zeros |
| `version` | `int = 1` | defaults so callers can omit it |
| `authors` | `list[str]` | API returns `Author` objects → map with `[a.name for a in ...]` |
| `categories` | `list[str]` | papers are genuinely cross-listed |
| `published` / `updated` | `datetime` | `updated` is a revision *timestamp*, not a bool |
| `doi` | `str \| None` | most preprints have none |

Real `datetime` (not `str`) matters because snapshots are time-windowed — "papers submitted since the last snapshot" is a constant query, and string dates sort correctly only by luck of format. Pydantic converts to/from ISO strings in both directions, so JSONL round-trips work anyway.

Abstracts only, no PDF full text — a deliberate scope decision. Full-text parsing (layout, math, references) is a much larger problem and abstracts carry enough concept/method signal for the graph.

---

## `ingest_arxiv.py` — the only module that touches the network

Nothing downstream calls this. Later stages read persisted snapshots, so the corpus stays fixed while extraction and drift detection are re-run against it.

### Details worth remembering

- `result.get_short_id()` is a **method**. Without the `()` you stringify a bound-method object.
- `result.summary` is the abstract. The schema uses the domain term so nothing downstream has to know arXiv's naming.
- `authors=[author.name for author in result.authors]` — the API returns `Author` objects.
- `DEFAULT_QUERY` searches **both spellings**: `abs:"neuro-symbolic" OR abs:"neurosymbolic"`. Searching one loses roughly half the subfield.
- `dedupe()` is called before returning, so nothing downstream has to know the API returns overlapping results across pages.

### Running it

```bash
python -m pipeline.ingest_arxiv --max-results 300
```

Use `-m`, not `python pipeline/ingest_arxiv.py`. The `-m` form puts the package root on `sys.path` so `from .models import ...` resolves; running the file directly makes it a top-level script and the relative import fails.

Expect ~1 minute for 300 — the client sleeps 3s between pages to respect arXiv's rate limit (1 req / 3s). That's not a hang.

---

## `snapshots.py` — immutable corpus storage

```
data/raw/2026-08-14T09-27-10Z/
├── papers.jsonl
└── metadata.json
```

**This module has no arXiv dependency.** Every stage reads snapshots; only ingestion writes them.

### Why snapshots at all

This is the project's central design decision. Drift is defined as a shift between the embedding distribution of one snapshot and the next — so both must still exist, byte-for-byte, when the detector fires. Streaming arXiv results straight into extraction would leave nothing to compare against and no way to answer *"which papers moved the distribution?"* It also makes the pipeline replayable: extraction can be re-run against a fixed corpus while prompts are tuned, without re-querying arXiv.

### Snapshot id = UTC timestamp

Format `%Y-%m-%dT%H-%M-%SZ`. Colons are stripped because they're illegal in Windows filenames and this will be containerized.

A *date* would collide on two runs in one day and silently overwrite. The ISO-ish shape means **lexicographic sort == chronological sort**, so "the previous snapshot" is `sorted(ids)[-2]` with no date parsing. The drift detector leans on that constantly.

Use `datetime.now(timezone.utc)`, not `utcnow()` — the latter returns a naive datetime and is deprecated since 3.12.

### `metadata.json`

Records `query`, `created_at`, `paper_count`. When drift fires weeks later the first question is always *"did the corpus change, or did I change the query?"* — unanswerable unless the query is persisted next to the data. `load_metadata()` exists so no stage hand-rolls a `json.load` on a path it built itself.

### JSONL, not one JSON array

Append-friendly (write as you page, no need to hold everything in memory), streamable line by line, and a truncated file from a crashed run is still partially readable.

`load_snapshot` streams with a `for` loop rather than `read().split("\n")` — identical now, but survives a 50k-paper corpus. The `if line.strip()` guard skips a trailing blank line, the classic naive-JSONL crash.

### The atomic write ⚠️

The snapshot is built in a `mkdtemp` directory and moved into place with a single `rename`, which is atomic within a filesystem. So a snapshot directory **either exists complete or doesn't exist at all**.

This matters more here than in a typical script. If a fetch dies halfway — network blip, rate limit, Ctrl-C — a non-atomic write leaves 200 of 800 papers with nothing signalling a problem. Feed that to the drift detector and it reports a large distribution shift: *exactly* the alert the whole system exists to emit. You'd be debugging a fake drift event caused by a partial write. Atomic rename makes that impossible rather than rare.

**`tempfile.mkdtemp(dir=root_path)` — the `dir=` is load-bearing.** `rename` is only atomic within a single filesystem. Staging in `/tmp` and renaming into the project raises `OSError: [Errno 18] Invalid cross-device link` the moment `/tmp` is a separate mount — which it is in most containers.

### `list_snapshots` filters by id pattern

`_SNAPSHOT_ID_RE` whitelists the format we produce, which also skips `tmp*` staging directories left behind by an interrupted run. Whitelisting the known shape is more robust than blacklisting `tmp`.

Returns `[]` when `data/raw` doesn't exist — a fresh clone has no data dir, and that isn't an error.

### `dedupe` is a max-reduce, not a seen-set

Keyed on `arxiv_id`; the map value is replaced only when the incoming `version` is higher. So it collapses duplicates *and* resolves which one wins in one pass. A `set` can't do this — `Paper` v1 and v2 aren't equal, so you'd keep both.

`dict` preserves insertion order, so the API's newest-first ordering survives.

---

## Environment gotchas

- **venv:** `python3.11 -m venv .venv` — must be `python3.11`; plain `python3` is pyenv 3.10.11 on this machine, and a venv permanently inherits its creating interpreter.
- **Pylance "could not be resolved":** editor problem, not Python. The venv lives at `graph-pipeline/.venv` but the workspace root is `arxiv-lens/`, so VS Code's auto-discovery misses it. Fix with **Python: Select Interpreter** → `./graph-pipeline/.venv/bin/python`, or pin `python.defaultInterpreterPath` in `.vscode/settings.json`.
- **`data/`** is already gitignored (line 638). Snapshots are regenerable and will reach hundreds of MB.
- **Dependency pinning:** add each package in the commit that first imports it, so a resolution failure is attributable to one change. Cap majors (`arxiv>=4.0.1,<5`) — an unbounded `>=` means a future 5.0 with a renamed API breaks a build on a commit that changed nothing. Before containerizing, `pip freeze > requirements.lock`; an unpinned Dockerfile build produces a different image every time, which undermines the reproducibility the drift story depends on.

---

## Verified behaviour

```
snapshots (chronological): ['2026-08-14T09-26-57Z', '2026-08-14T09-27-10Z']
round-tripped: 15 papers -> Paper
  published=datetime(2026, 8, 13, 8, 44, 48, tzinfo=TzInfo(0))
  metadata: {'query': 'cat:cs.AI AND (abs:"neuro-symbolic" ...)', 'paper_count': 15}
dedupe 3 copies -> [('2608.12961', 3)]
```

---

# Graph stages

## `ontology.py` — the schema everything rests on

Six entity types, six relations, each with domain, range, and OWL-style properties. Derived by open coding a sample of the corpus: entity types from recurring noun phrases, relation types from verbs connecting two already-tagged nouns.

### Deriving relations from verbs

The filter that makes it tractable: **both ends must be one of the six entity types.** Verbs whose subject is the authors — *we introduce, we show, we demonstrate* — are rhetorical, not relations between entities. That one rule removes most of the noise from a verb tally.

`COMPILES_TO` came from noticing that *compiles* kept landing in two buckets because it belonged to neither. A knowledge-compilation edge (a formalism translated into another representation) is genuinely distinct from `USES` and from `COMBINES`.

`ADDRESSES` absorbed a candidate `APPLIED_TO`. Fewer, sharper relations beat more, blurrier ones — and the object's *type* still carries the distinction, since `ADDRESSES → TASK` and `ADDRESSES → PROBLEM` remain different claims.

PROBLEM and TASK stayed separate on one operational test: **can you describe what a solution takes in and puts out?** Yes → TASK. No → PROBLEM.

### Why the properties matter

`transitive + irreflexive + asymmetric` together forbid cycles. That trio is what makes an SMT solver defensible rather than decorative — see `consistency_checker.py` below.

### Gotchas

> **`EXTENDS: "EXTENDS"` creates zero enum members.** A colon is an *annotation*, not an assignment. The class imports cleanly, has no members, and the failure surfaces far away when structured outputs receive an empty enum in the JSON schema. Use `=`.

> **A trailing space in an enum value is invisible and fatal.** `COMPILES_TO = "COMPILES_TO "` becomes the literal in the JSON schema, so the model is asked to emit a value with a trailing space, and every `== "COMPILES_TO"` comparison silently fails.

`StrEnum` (3.11+) rather than `Enum`: members *are* strings, so they serialize to plain JSON with no custom encoder and compare equal to string literals.

### The `USES` widening

`USES` originally had domain `{METHOD}`. Eight papers independently produced FORMALISM subjects — `ULLER USES first-order logic`, `probabilistic logic programming USES distribution semantics`. A formalism built out of another formalism is a true thing to say, so the domain was widened to `{METHOD, FORMALISM}`.

Seven `COMPILES_TO` violations with METHOD subjects were **not** widened. Those split into two bugs wearing the same costume: the rule-8 error (naming the system rather than the representation) and entity mistyping. Widening would have legitimized both and deleted the rule's purpose.

> The general lesson: when data violates the schema, decide whether the schema is too narrow or the data is wrong. Consistency isn't proof of correctness — eight papers agreeing about `USES` is a schema gap; seven papers making the same extraction error is still an error.

Widening is backward-compatible with stored data; narrowing is not. A widened schema only makes previously-illegal edges legal, so nothing needs re-extracting.

---

## `extract_entities.py` — constrained extraction

### Why closed enums

`RelationType` as a closed `StrEnum` becomes a **decoding constraint** through structured outputs, not a prompt suggestion. The model cannot emit `IMPROVES_UPON` — the token isn't in the allowed set.

Schema-free extraction (Microsoft's GraphRAG, `LLMGraphTransformer`) lets the model name its own types per document. Over 300 abstracts that yields `USES`, `uses`, `utilizes`, `LEVERAGES`, `IS_BUILT_ON` as five distinct relationship types, and the graph becomes unqueryable — no single traversal catches "method depends on component."

`ConfigDict(extra="forbid")` makes Pydantic emit `additionalProperties: false`, which structured outputs require.

### Why relation endpoints are strings, not nested `Entity` objects

Nesting invites the model to invent a second, near-duplicate entity mid-relation. Strings force it to refer back to something it already listed, and `validate()` then checks every endpoint appears in `entities`.

### Prompt rules earned from real failures

Rules 4–8 all came from reading actual output, not from imagination: LaTeX in entity names, clause-length names, generic categories typed METHOD, `EVALUATED_ON` pointing at tasks, `PyTorch` typed MODEL, `COMPILES_TO` naming the system rather than the representation.

Rule 7 (`EVALUATED_ON` needs a named dataset) **never worked** — the model reads "demonstrate through case studies" as evaluation and commits to the predicate before checking the object's type. That failure is what motivated `repair()`.

### `repair()` — the ontology acts instead of reporting

When an edge violates domain/range, ask the ontology which predicates *would* accept those endpoint types:

| candidates | action |
|---|---|
| exactly one | rewrite the predicate — forced, not guessed |
| zero | drop the edge — no legal name for that shape |
| several | keep it, let `validate()` report — guessing would fabricate a claim |

Five of eight legal type-pairs admit exactly one predicate, so most violations are mechanically fixable. `METHOD → TASK` admits only `ADDRESSES`; `METHOD → METHOD` admits `EXTENDS`, `USES`, and `COMBINES`, which are three genuinely different claims.

The order of checks matters: missing endpoint → drop, irreflexive self-loop → drop, already legal → keep untouched (this branch is what makes it idempotent), then the candidate lookup.

> The honest limit: repair assumes the entity *types* are right and only the predicate is wrong. Keep the rewrite log — it's the evidence that it isn't laundering garbage.

### Nondeterminism and the reuse cache

**The same prompt and the same abstract produce different graphs across runs.** Entities appear and vanish; `Gene Ontology` present in one run, absent the next. Not fixable through sampling parameters — Sonnet 5 rejects `temperature`/`top_p`/`top_k` alongside `output_config`.

So reproducibility is architectural: extract once, key on `(arxiv_id, version)`, reuse verbatim forever. This is what makes drift measurable at all — a diff between snapshots is only meaningful if unchanged inputs produce unchanged outputs.

> Open hole: the cache doesn't know about the prompt. Edit `ONTOLOGY_PROMPT` and it serves stale extractions from a different prompt. `--no-reuse` is the interim guard; hashing the prompt into extraction metadata is the fix.

### API gotchas

- **`max_tokens` caps thinking + response text together.** Adaptive thinking is on by default, so 4096 was consumed before the JSON closed, surfacing as `ValidationError: EOF while parsing a string` — a *truncation*, not malformed JSON. Fixed with 8192, `effort: "low"`, and a `stop_reason == "max_tokens"` guard so truncation reports itself.
- **Prompt caching is prefix-matched.** Any byte change invalidates everything after it. Minimum cacheable prefix is 512 tokens on Opus, 1024 on Sonnet. Verify with `usage.cache_read_input_tokens`.
- **Batch `custom_id` must match `^[a-zA-Z0-9_-]{1,64}$`** — arXiv ids contain a `.`, older ones a `/`. Sanitize forward and rebuild the reverse map from the submitted papers. Getting this wrong *silently* would be worse than the 400: results keyed `2608_12961` would never match a paper again, and the reuse cache would miss on every paper forever.
- **Batch results arrive in arbitrary order**, keyed by `custom_id`.
- `@functools.cache def _client()` defers construction, so importing the module doesn't require an API key.

---

## `canonicalize.py` — one node per concept

Traversal is exact. `LLM` and `LLMs` as separate nodes means every query about language models returns half its answers. This matters far more for a graph than for vector RAG, where embeddings put those strings near each other anyway.

### `normalize()` output is a key, never a name

Aggressive normalization (lowercase, punctuation → space, strip plurals) produces a **grouping key only**. The name that reaches the graph is the most-mentioned real surface form. If normalized forms became names, the graph would read `llm`, `chain of thought`, `deepseek r1`.

Winner selection is `min` on a three-part tuple: `(-count, len, alphabetical)`. Three levels because the first two genuinely tie, and a tie broken by dict order would give a different graph each run.

> **`CLIPS` → `CLIP` was a real false merge.** CLIPS is an expert system shell; CLIP is OpenAI's vision model. The plural rule stripped `s` from a 5-letter acronym. Fixed by skipping all-uppercase words — `LLMs` has a lowercase `s` and is a plural, `CLIPS` is an acronym outright. **A bad merge is worse than a missed merge**, because it fabricates edges between unrelated things.

### Type resolution is a cascade

Merging names creates type conflicts — `Chain-of-Thought` was METHOD in six papers and FORMALISM in three, and Neo4j needs one label per node. Evidence strongest first:

1. **Edge fit** — the ontology's domain/range constraints vote (resolved 8 of 22)
2. **Majority** — mention counts, over survivors only (1 more)
3. **Precedence** — `DATASET > MODEL > FORMALISM > METHOD > TASK > PROBLEM`, most concrete to most abstract (12, all logged for review)

Stage 3 fires most on `TASK vs PROBLEM`, and there it isn't arbitrary — all six of those have definable I/O, so the I/O test says TASK.

Only stage 3 appends to `unresolved`. That list is a **review queue**, not an error report: anything resolved by stages 1–2 has a justification, these have only a convention.

### Repair runs again after merging

Global type resolution can invalidate an edge that was legal under per-paper types. `ILP` typed TASK locally made `ADDRESSES` valid; resolved to FORMALISM corpus-wide, the same edge became illegal. Two edges on the current corpus. The ontology gets the last word after the merge.

### Still unsolved

String rules cannot connect an acronym to its expansion. `LLMs` (degree 20) and `Large Language Models` (degree 17) remain separate nodes — the graph's two biggest hubs, one concept. Needs a hand-curated alias table; ~20 pairs covers it, and only ~114 names appear in more than one paper anyway.

---

## `drift_detector.py` — a gate, not a dashboard

### Explained vs unexplained churn

Papers are partitioned into **added**, **removed**, and **shared**. Only shared papers at the same version count, because those are reused verbatim from the cache and *cannot legitimately change*. A difference there means something upstream moved: an edited prompt, `--no-reuse`, a changed ontology.

Corpus growth is therefore invisible to the gate, which is correct — 150 new papers is not drift.

A v1 → v2 paper is skipped entirely (`continue` before `shared += 1`), so revisions don't inflate the denominator. The cost: a revised paper's extraction is never checked at all.

### Both snapshots are canonicalized together

`canonicalize(previous + current)` then split by slice. Run separately, a shifted mention count could elect `LLM` in one snapshot and `LLMs` in the other, and *every* edge touching it would look changed.

> The slice depends on `canonicalize()` preserving input order. Nothing enforces that — worth an `assert` on the id sequence, since this is a gate whose whole job is catching silent changes.

### The whole diff is set difference

Relations become `set[tuple[str, str, str]]`. Tuples are hashable and compare by value; two `Relation` model objects would compare by identity and every edge would look changed. Then `before - after` and `after - before` is the entire algorithm.

### Thresholds are policy, kept at the top of the file

Two metric bugs found by testing against known answers:

- **`lost_edge_share` had the wrong denominator** — dividing by total churn made 3 losses with 0 gains read as 100%, blocking a build over 3 edges out of 1246. Now divided by the edges that existed to lose.
- **Violations were counted, not rated** — growing the corpus 150 → 300 flagged "3 new violations", but a corpus twice the size carries twice the violations at identical quality. Now compared per-relation.

> A gate that fires on healthy input is worse than no gate, because people learn to ignore it.

### The thresholds are not empirical

`changed_share` is bimodal, not continuous: cache hit → exactly 0%, cache miss → near 100% (the model rewrites most papers). No run lands in between, so 5% sits in a dead zone. The honest value is **0** — any change to a cached paper is a bug worth investigating.

`sys.exit(1)` is what makes it enforcement rather than monitoring: `drift_detector && build_graph`.

---

## `build_graph.py` — loading Neo4j

### Cypher cannot parameterize labels or relationship types

`MERGE (a)-[r:$predicate]->(b)` is a syntax error; only property *values* can be parameters. So labels and types are interpolated into the query string.

That is safe **only** because both come from closed enums — `LABELS.values()` and `RelationType`. Nothing model-generated ever reaches those slots; `name` travels as a bound `$` parameter. The enum built for structured outputs is now doing injection-safety duty.

### `MERGE` on identity, `SET` afterwards

```cypher
MERGE (p:Paper {arxiv_id: row.arxiv_id})   -- identity only
SET p.title = row.title                    -- everything else
```

`MERGE` matches the *entire* pattern given. With `title` inside the map, a corrected title next snapshot creates a second paper node.

### Constraints before loading

Each uniqueness constraint creates a backing index. **`MERGE` without an index is a full label scan** — with 1754 entities that's the difference between seconds and minutes. This is the most common reason people report Neo4j as slow.

### Provenance turns accumulation into a feature

```cypher
ON CREATE SET r.first_seen = $snapshot, r.snapshots = [$snapshot]
SET r.last_seen = $snapshot,
    r.snapshots = CASE WHEN $snapshot IN r.snapshots
                       THEN r.snapshots ELSE r.snapshots + $snapshot END
```

`ON CREATE` fires once; the bare `SET` fires every time. The `CASE` prevents a re-run from appending the same snapshot id twice.

With that in place, merging into an existing graph gives history rather than a leak:

- **current graph** = `last_seen = <latest>`
- **graph at N** = `N IN r.snapshots`
- **drift** = set difference between those filters
- **confidence** = `size(r.snapshots)` — an edge seen in five snapshots is more trustworthy than one seen once, which partly mitigates the nondeterminism

Without provenance, wipe-and-rebuild would be the better choice.

### `reconcile_labels` prevents split entities

Resolved types are corpus-dependent — `Lean` came out FORMALISM on a 1–1 vote by precedence alone. If it flips next snapshot, `MERGE` would create a *second* node under the new label and split the edges. So existing labels are read once, compared in Python, and mismatches are relabelled (`REMOVE n:Formalism SET n:Method`) grouped by `(old, new)`.

### `UNWIND` batching

Send a list of dicts as one parameter and let the server loop: one round trip, one transaction, one query plan, instead of ~5000 separate calls. `collect()` turns rows into a list; `UNWIND` is its inverse.

Relations are grouped by `(subject_label, predicate, object_label)` for two reasons at once — each query can name its labels literally, *and* `MATCH (a:Method {name: …})` hits the unique index. An unlabeled match would scan the whole database per row.

### Idempotency is verified, not assumed

`result.consume().counters.nodes_created` is the server's own count. A second run reporting `+0` across the board is evidence.

### Paper nodes

Chosen over entities-only because 25% of entity mentions have no relation edge and would be unreachable. `MENTIONED_IN` rescues them, adds provenance to every answer, and gives a second connectivity route — which matters when 94% of entity names appear in only one paper.

**Paper-to-paper edges are not stored.** `CITES` isn't available (arXiv returns no references — that needs Semantic Scholar or OpenAlex). "Builds on" is *derivable* from the entity graph, but the naive rule ("an earlier paper mentions the thing you build on") yields 370 mostly-junk edges like *both papers mention LLMs*; restricting to entities the earlier paper actually contributed gives 26 real ones. Either way it's an **inference, not evidence** — materializing it would make the drift detector report churn in derived edges and make Z3 verify our own conclusions. Derive at query time instead.

---

## `consistency_checker.py` — Z3 over the assembled graph

### What it catches that `validate()` cannot

```
A EXTENDS B   (paper 1)
B EXTENDS C   (paper 2)
C EXTENDS A   (paper 3)
```

Transitivity closes this into `A EXTENDS A`; irreflexivity forbids it. **No single edge is wrong** — the contradiction exists only in the conjunction, and it spans three papers no human read together.

### Why a solver rather than a DFS

For `EXTENDS` alone, a cycle-detecting traversal would do. The solver earns its place when constraints interact — asymmetry, type disjointness, and domain/range simultaneously — where hand-rolled checks multiply and a solver just takes the conjunction. Be honest about which claim is being made.

The real payoff is the **unsat core**: the minimal set of edges that cannot coexist, rather than "something is wrong somewhere."

### Encoding decides tractability

The first version quantified over every node pair — `itertools.product(nodes, repeat=2)` — and timed out. `ADDRESSES` alone would need ~500k booleans instead of 481.

Fix: **variables only for pairs the edges can actually reach.** `scope = _closure(pairs) if spec.transitive else set(pairs)`. Closures are tiny in practice — `EXTENDS` 33 edges → 34 closure pairs, `USES` 410 → 444. Whole check runs in ~5s.

> Same constraints, same solver, 100× difference. With SMT the encoding matters more than the solver.

### Tracked vs plain assertions

Edges use `assert_and_track` so they appear in the unsat core. Ontology properties are added plainly, so **a core names only edges, never the schema** — the schema is assumed correct by construction.

Entity types are `z3.Const` over an `EnumSort`. A constant holds exactly one value, so type disjointness is structural rather than asserted.

### Finding more than one conflict

`solver.check()` returns one core. To enumerate, drop one edge from that core and re-solve until sat.

### Verified by injection

The real graph has no structural contradictions — 33 `EXTENDS` edges over 300 papers is too sparse. So the checker was verified by injecting a 3-cycle and a mutual `USES` pair; it returned exactly the 3 conflicting edges and exactly the 2. **"No errors found" is indistinguishable from a broken checker until you make it find something.**

Its value today is preventive; it becomes load-bearing as lineage chains lengthen.

---

# Serving

## `pipeline/embeddings.py` — one embedding cache

### Why it was extracted

`vector_rag.py` owned the SentenceTransformer and the `.npz` cache, and `graph_rag.py` reached across packages for the private `_model`. A third consumer — drift detection — made that untenable. All three now import `pipeline.embeddings`, which matters beyond tidiness: if entity linking and the drift monitor embedded with different settings, drift would be measured against vectors the linker never sees.

### `normalize_embeddings=True` is load-bearing

Raw embeddings have arbitrary length, and length mostly tracks how long the text was rather than what it means. Normalising puts every vector on the unit sphere, and then

```
cos(a, b) = (a · b) / (|a| |b|)   →   cos(a, b) = a · b     when |a| = |b| = 1
```

Cosine similarity collapses into a plain dot product. That is why search in both RAGs is a single `vectors @ query` — one matrix multiply scores all 300 papers at once, no division and no norms at run time. Drop the flag and every similarity number in the project silently becomes something else.

### Two caches doing two jobs

`@functools.cache` on `model()` and on `embed_snapshot()` is the in-process cache; the `.npz` file is the across-runs cache. Encoding 300 abstracts costs about ten seconds, so the file means paying it once ever per snapshot, and the decorator means not even re-reading the file within a run.

Both are safe only because embedding is deterministic and pure: same model, same text, same vector, forever. It is also why two snapshots holding the same papers show exactly zero drift — see `embedding_drift.py` below.

`.npz` is numpy's zip-of-arrays, so the 300×384 matrix stays a binary blob instead of becoming 460,800 lines of JSON. The ids, titles and texts are stored beside it because a vector alone cannot tell you which paper row 47 is.

### The `sentence_transformers` import is inside the function

Importing it pulls in torch, which costs seconds. At module level, `--help` on any module that transitively imports this one would pay for torch before printing a usage string.

### `vectors_for(snapshot_id, arxiv_ids)`

The cache is per snapshot and holds every paper. Drift needs *subsets* — the papers added since last time, or one side of a date split — so the selector maps ids to rows rather than re-encoding. Re-encoding a subset would give identical vectors, but slowly, and would tempt a future change into encoding the two sides with different settings.

`vectors[[3, 7, 12]]` is numpy fancy indexing: a list of row numbers returns those rows, in that order. The `if a in index` guard skips ids the snapshot does not contain — deliberate, but it is a silent path, so a caller passing the wrong snapshot gets a smaller matrix rather than an error.

---

## `pipeline/embedding_drift.py` — semantic drift

### Structural drift and semantic drift are different questions

`drift_detector.py`: same papers, same versions — did the *extractor* change? Any difference is a defect, so it exits 1.

`embedding_drift.py`: are the papers still *about the same things*? A field moving into new topics is normal, so this reports by default. What it invalidates is calibration: entity linking's 0.80 threshold and the golden eval's ground truth were both fitted to the old distribution.

### The comparison is the delta, not the snapshot

Snapshots are cumulative. `2026-08-14` and `2026-08-16` contain the *same 300 papers*, so comparing them whole is guaranteed to show zero drift — the embeddings are deterministic. `compare_snapshots` therefore compares papers **new in** the current snapshot against everything in the previous one. Whole-snapshot comparison dilutes any real signal with the overlap.

`compare_within` exists because there is only one meaningful corpus today: it splits one snapshot at its median publication date, which answers "is the recent literature drifting from the older literature the graph was built on".

### Two signals, read together

- **Domain classifier AUC** — train a classifier to tell reference from current. 0.5 means indistinguishable. It catches drift in *any* direction, including ones that leave the mean untouched.
- **Centroid cosine distance** — only catches a shift in the mean, but is interpretable and cheap.

They disagreed once, which is how the `target` bug below was found. A single metric would have reported drift on an unchanged corpus indefinitely.

### ⚠️ `ModelDriftMethod` mutates the frames you give it

```python
reference_emb["target"] = [1] * reference_emb.shape[0]
current_emb["target"] = [0] * current_emb.shape[0]
```

In place, on the caller's DataFrame. Sharing frames between the two methods made the constant label a 385th embedding dimension, and the centroid distance jumped from 0.008 to 0.452 on *identical* data. Each method now gets `.copy()`.

The symptom was two metrics contradicting each other: AUC 0.482 (indistinguishable) beside a centroid distance screaming drift. Neither number alone would have looked wrong.

### ⚠️ Evidently's bootstrap for centroid distance is not usable here

It builds the null by resampling **both** halves from the reference set, with replacement:

```python
b_ref_idx  = np.random.choice(reference_emb.shape[0], b_ref_size)
b_curr_idx = np.random.choice(reference_emb.shape[0], b_curr_size)   # also reference
```

Two overlapping samples from the same 200 points produce centroids that sit closer than two independent sets would, so the null is too tight. It also estimates a 95th percentile from `N_BOOTSTRAP = 100` draws.

Measured over 40 pairs from an identical distribution it called drift **22 times**, and detected a real shift in only **9 of 20**. A permutation test on the same data: **3/40** and **20/20**. Pool both sides, shuffle, split at the original sizes, repeat 500 times, take the 95th percentile — the null then reflects exactly the question being asked.

Evidently still runs the domain classifier (whose null is principled) and renders the HTML report. The centroid null is computed in `_centroid_drift`.

The lesson generalises: a library giving you a number is not the same as the number being calibrated for your data. Checking a detector's false-positive rate against a distribution with a known answer costs ten lines.

### `MIN_PAPERS = 30`

Below that, both statistics are noise. It returns `skipped` with the sizes rather than a verdict, because "no drift detected on 8 papers" is a sentence that will eventually be quoted as if it meant something.

---

## `retrieval/vector_rag.py` — the baseline

### The whole retrieval engine is four lines

```python
ids, titles, texts, vectors = embed_snapshot(snapshot_id)
query = model().encode([question], normalize_embeddings=True)[0]
scores = vectors @ query
top = np.argsort(-scores)[:k]
```

Embed the question the same way the corpus was embedded, dot-product against every paper at once, sort, take k. `np.argsort` is ascending and returns *positions* rather than values, hence the negation.

### Why there is no vector database

300 × 384 float32 is 460 KB, and an exact brute-force search over it takes microseconds. A vector database exists to make approximate nearest-neighbour search fast at millions of documents; at this size it would be slower *and* less accurate than the matrix multiply. The decision worth defending is recognising the scale where infrastructure stops paying for itself.

### It is pinned to the extracted snapshot

`embed_snapshot` defaults to `list_extracted()[-1]`, not the latest raw snapshot. If the baseline searched 320 papers while the graph held 300, the graph-vs-vector comparison would be measuring corpus size instead of architecture.

### One abstract is one chunk

No chunking, no overlap, no windowing. Abstracts are short enough that splitting them would only introduce boundary artefacts, and the baseline should be given the *easier* setup — a baseline you handicapped proves nothing.

---

## `retrieval/graph_rag.py` — plan, link, traverse, answer

### The LLM sits at both ends and never in the middle

```
question → plan()      LLM: language → QueryPlan(intent, entities)
         → link()      deterministic: names → graph nodes
         → traverse()  deterministic: whitelisted Cypher
         → answer()    LLM: facts → prose
```

Two LLM calls, and neither one writes a query. `TRAVERSALS` is a fixed dict of six Cypher templates keyed by intent, so the model chooses *which* traversal from a closed set and supplies parameters — it never constructs one. The same closed-enum discipline as extraction, applied to retrieval.

### Embeddings do a different job here than in the baseline

The baseline embeds abstracts to find relevant *documents*. This embeds entity *names* to solve a mapping problem: the question says "neural nets", the graph node is called "neural network". Same model, same dot product, entirely different purpose.

`link()` is two tiers:

1. `_group_key(text)` — the canonicalization key, reused. Free, exact, already handles plurals, punctuation and the alias table.
2. `_nearest(text)` — nearest neighbour among all entity names, accepted only above `LINK_THRESHOLD`.

Reusing `_group_key` means the linker and the graph builder agree on what counts as the same name by construction.

### `LINK_THRESHOLD = 0.80` is what makes refusal possible ⚠️

`np.argmax` always returns something. In a 1742-name vocabulary there is always a nearest neighbour, however wrong. Without the threshold, "Quantum Banana Framework" links to whatever is least unlike it, the traversal runs, and the answer confidently describes an entity nobody asked about.

```python
return names[best] if scores[best] >= LINK_THRESHOLD else None
```

The `None` is the feature. The number was measured, not chosen: every correct match in the vocabulary scores above it and every wrong one below, with `"neural nets" → "deep learning"` sitting at 0.68 as the nearest miss. An earlier substring fallback was removed after it was found to be actively harmful.

### Two places it can decline to answer

```python
if missing:    return f"Not in the graph: ..."
if not facts:  return "The graph holds no facts matching that traversal."
```

One for an entity that could not be linked, one for a traversal that returned nothing. Vector RAG has neither: `[:k]` always returns k documents.

That is the structural finding behind the 100% vs 61% comparison, and it holds independently of the golden set's bias toward graph-shaped questions: **top-k retrieval cannot express absence.** Asked about a relationship that does not exist, it hands the model five plausible abstracts and an implicit invitation to connect them. A traversal can answer "there is no path".

### Side by side

| | vector_rag | graph_rag |
|---|---|---|
| embeds | abstracts | entity names |
| retrieval unit | documents | facts (subject–predicate–object) |
| retrieval | `vectors @ query`, top-k | Cypher from a whitelist |
| embedding's job | find relevant text | resolve a name to a node |
| can return nothing | no — always k | yes, in two places |
| citations | whole papers | per fact, from `r.papers` |
| LLM calls | 1 | 2 |

---

## `proto/graphrag.proto` — the contract

An interface definition language, not a program. `protoc` compiles it into real classes
in both languages, so the Python server and the Java client share one definition and
neither knows the other exists.

### Field numbers are the wire format

`= 1`, `= 2` are not defaults or values, they are the identifiers protobuf actually
serializes. Names never cross the wire.

- Renaming a field costs nothing. Changing its number breaks every deployed client.
- **Never reuse a number** after removing a field: an old client sends 3 meaning the old
  thing and new code reads it as the new thing. `reserved 3;` turns that into a compile
  error.
- Numbers 1-15 encode in one byte, 16 and up take two, so hot repeated fields belong in
  the low range.

### proto3 has no required fields

Everything is optional and unset scalars read back as the zero value, so `""` and "never
set" are indistinguishable unless a field is marked `optional`. That is why every enum
starts at `_UNSPECIFIED = 0`: zero is what an unset field returns, and it must not
silently mean the first real choice.

### Enum values use C++ scoping ⚠️

The trap that shaped every enum in this file. Enum *values* are siblings of their type,
not children of it, so they must be unique across the whole package:

```
"EVALUATED_ON" is already defined in "arxivlens.v1".
Note that enum values use C++ scoping rules, meaning that enum values are siblings of
their type, not children of it.
```

`EVALUATED_ON` exists in both `QueryIntent` and `RelationType`, which is fine in Python
(`QueryIntent.EVALUATED_ON` and `RelationType.EVALUATED_ON` coexist) and a compile error
in protobuf. Hence `QUERY_INTENT_EVALUATED_ON` and `RELATION_TYPE_EVALUATED_ON`. The
prefixing convention exists entirely because of this rule.

> `option java_package: "..."` is a syntax error; protobuf wants `=`. Same shape as
> writing `EXTENDS: "EXTENDS"` in a Python enum: a colon reads as a label and produces
> something that is not what you meant. See [[the ontology notes]] above.

### The design decisions

**`QueryIntent` crosses the wire, both ways.** On the request it is an optional override;
on the response it reports the traversal that actually ran, so a caller who sent
`UNSPECIFIED` still learns what happened. Sending `intent` *and* `entities` together skips
the planner completely, which makes a call deterministic, free, and free of model latency
— useful for integration tests. Sending `intent` alone does not, because the planner is
also what extracts the entity names.

**`predicate` is an enum, not a string.** Adding a relation type now requires a contract
change, which is correct: it changes what the graph can express. A string would let the
wire format drift silently away from `RELATION_SPECS`.

**Facts cross the wire, not only prose.** Provenance is the point of the project. If only
the answer sentence crossed, a REST layer could never show which paper asserted what
without querying again.

**An unlinkable entity is a normal response, not a gRPC error.** `ResultStatus`
distinguishes three real outcomes: `OK`, `ENTITY_NOT_FOUND` (the name did not resolve to
any node), and `NO_FACTS` (it resolved, and nothing is asserted). "No connection" is an
answer, not a failure — the negative eval cases are the system working. A gRPC `NOT_FOUND`
would make the Java client throw, so callers would be catching exceptions to handle
successful queries. gRPC error codes are reserved for Neo4j being down or a malformed
request.

### Generated stubs are not committed

`graph-pipeline/generated/` is gitignored; regenerate with the command in the README.
Java generates its own through Gradle. The alternative, committing them, means a fresh
clone works without `protoc` at the cost of checked-in code that can go stale.

---

## `retrieval/grpc_server.py` — the server

Thin: it maps the proto onto `retrieval/graph_rag.py` and adds nothing of its own.

### The planner is consulted only for what the caller omitted

```python
if intent is None or not entities:
    query_plan = plan(request.question)
    intent = intent or query_plan.intent
    entities = entities or list(query_plan.entities)
```

Supply both and no model call happens at all. Supply neither and it behaves like the CLI.

### The empty paths return before the LLM

`ENTITY_NOT_FOUND` and `NO_FACTS` are constructed and returned without calling `answer()`.
No reason to pay for prose when there is nothing to say — and it means those paths can be
tested for free.

### Enum conversion is by name, not by number

`_to_proto_intent` builds `QUERY_INTENT_` + the Python enum's value and looks it up.
Mapping by ordinal would break the moment either enum is reordered; mapping by name fails
loudly instead, at the point of the mistake.

### `sys.path` insert for the generated code

`graphrag_pb2_grpc.py` does a plain `import graphrag_pb2`, so the generated directory has
to be importable on its own. The insert happens once, in this module, rather than being
scattered.

---

## Containerization

### The build context is the repository root, for both services

Neither service can be built from its own directory, because both need `proto/`, which is a sibling of both:

- `retrieval/grpc_server.py` imports from `generated/`, which is gitignored and produced by `grpc_tools.protoc` against `../proto`
- `build.gradle` declares `sourceSets { main { proto { srcDir '../proto' } } }`

Hence `build.context: ..` in compose, `COPY graph-pipeline/...` paths inside the Dockerfiles, and a single `.dockerignore` at the root. The Java builder also has to preserve the sibling layout — `/build/proto` beside `/build/query-service` — or Gradle's `../proto` resolves to nothing and it compiles zero proto sources without complaining.

### `RUN` is build time, `CMD` is run time ⚠️

The distinction the whole file rests on. `RUN` executes during `docker build` and its filesystem changes are frozen into a layer; `CMD` is not executed at build at all, only recorded as what to run when a container starts.

`RUN python -m retrieval.grpc_server` would hang the build forever, waiting for a server to exit.

Corollary: a `CMD` in a builder stage does nothing. The second `FROM` discards everything not explicitly copied forward.

### Layer order is stable → volatile

Docker reuses cached layers until one changes, then invalidates every layer after it. Dependencies change monthly, source changes constantly, so:

```dockerfile
COPY graph-pipeline/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt   # expensive, stays cached
COPY graph-pipeline/pipeline ./pipeline              # cheap, changes often
```

Reversed, a one-character edit re-downloads torch. Same reason the Java builder copies the wrapper and `*.gradle` and runs `./gradlew dependencies` before it copies `src/`.

`--no-cache-dir` on pip matters here too: without it every downloaded wheel stays under `~/.cache/pip`, and torch's alone is ~800 MB of dead weight in the layer.

### Why not alpine

The original Dockerfile was `python:3.11-alpine`. Alpine uses musl libc, and PyPI wheels for torch are built against glibc, so pip falls back to building from source — an hour, if it succeeds. `python:3.11-slim` is Debian-based and every wheel in `requirements.txt` installs prebuilt.

### `COPY . .` bakes secrets into a layer ⚠️

The original also copied everything, including `graph-pipeline/.env` and its `ANTHROPIC_API_KEY`. Deleting a file in a later layer does not remove it — the earlier layer still holds it and `docker history` still shows it.

The root `.dockerignore` excludes `**/.env`, `data/`, `.venv`, `build/` and `.gradle`. Worth verifying rather than trusting, because a mistyped pattern fails open:

```bash
docker run --rm <image> sh -c 'find / -name ".env" 2>/dev/null'
docker history --no-trunc <image> | grep -c ANTHROPIC
```

### The embedding model is baked in

The builder runs a download of `all-MiniLM-L6-v2` with `HF_HOME=/opt/hf`, and the runtime stage copies that directory. Without it, the first `link()` call in a fresh container reaches huggingface.co — a cold-start dependency on the public internet that only shows up under a network policy or an outage. `HF_HUB_OFFLINE=1` in compose makes any accidental fetch fail loudly instead of hanging.

`HF_HOME` has to be set in **both** stages: in the builder so the download lands somewhere known, in the runtime so the library looks in the same place. Set in only one, it silently re-downloads.

### Why torch stays in the runtime image

`_nearest()` catches `ImportError` and returns `None`, so an image without `sentence-transformers` still runs — entity linking just degrades to exact-match-only, and questions like "neural nets" stop resolving. Graceful degradation is a good property; shipping it as the default is not. The cost is an image around 2.5 GB, which is what an ML service costs.

### `-x test` in the Java image build

CI already runs all 31 tests. Running them inside `docker build` would put an embedded Kafka broker on the critical path of every image build, and a build machine without a Docker socket could not do it anyway. Tests gate the commit; the image build packages what the commit already proved.

`--no-daemon` because Gradle's background daemon has nothing to speed up in a container that exits after one build, and can leave the build waiting on it.

### `EXPOSE` publishes nothing

It is documentation plus a hint for `docker run -P`. The `ports:` mapping in compose is what actually opens a port.

### Addresses change meaning inside a container

Inside a container `localhost` is the container. Every address is an environment variable with a localhost default, so one image serves both modes:

| | host process | container |
|---|---|---|
| Neo4j | `bolt://localhost:7687` | `bolt://neo4j:7687` |
| Kafka | `localhost:9094` | `kafka:9092` |
| gRPC | `localhost:50051` | `graph-pipeline:50051` |

The Kafka split was already handled by `KAFKA_ADVERTISED_LISTENERS: INTERNAL://kafka:9092,EXTERNAL://localhost:9094` — a client is told the address for the listener it connected on.

### `environment` overrides `env_file` ⚠️

`graph-pipeline` loads `../graph-pipeline/.env` for `ANTHROPIC_API_KEY`, and that file also contains `NEO4J_URL=bolt://localhost:7687`, which is wrong inside a container. Compose resolves `environment:` after `env_file:`, so the explicit `NEO4J_URL: bolt://neo4j:7687` wins. Relying on that precedence is deliberate but subtle — it is the kind of thing to state rather than discover.

Only `graph-pipeline` gets the env file. Putting it on `neo4j` or `kafka` would inject the Anthropic key into containers that have no use for it.

### Profiles keep the existing dev loop

Both application services carry `profiles: [app]`, so `docker compose up` still starts infrastructure only and running the services on the host is unchanged. `docker compose --profile app up --build` runs everything containerised. Two modes, one file, no second compose file to drift.

---

## `k8s/` — Kubernetes

### Six objects replace what compose did implicitly

| | compose equivalent |
|---|---|
| Deployment | `restart: unless-stopped` for a stateless container |
| StatefulSet | the same, plus stable identity and its own disk |
| Service | the compose network's name resolution |
| PersistentVolumeClaim | a named volume |
| Secret | `env_file` |
| Job | `docker compose run --rm` |

The YAML is long because Kubernetes makes explicit what compose supplied by convention. Nothing here is a seventh idea.

### None of the addresses changed

```yaml
NEO4J_URL: bolt://neo4j:7687
KAFKA_BOOTSTRAP: kafka:9092
GRPC_TARGET: static://graph-pipeline:50051
```

Byte-identical to the compose file, because a Service named `neo4j` in this namespace resolves as `neo4j`. The port was making every address an environment variable with a localhost default; had they stayed literals in the code, moving to Kubernetes would have meant rebuilding both images.

### `imagePullPolicy: IfNotPresent` ⚠️

Kubernetes special-cases the `latest` tag and defaults its pull policy to `Always`. An image loaded into the node with `minikube image load` is therefore still fetched from Docker Hub, and fails with `ImagePullBackOff` on an image that is sitting right there. Loading the image and setting the policy are two separate requirements, and only doing one produces a confusing error.

More generally: a cluster has its own image store. It cannot see the Docker daemon that built the image. Locally that means `minikube image load`; in production it means a registry push.

### `fsGroup` on every pod with a volume ⚠️

A freshly provisioned PersistentVolume is owned by root. The Neo4j image runs as uid 7474 and both project images as uid 1000, so without

```yaml
securityContext:
  fsGroup: 7474
```

the database cannot write to its own data directory and the pod crash-loops with a permissions error at startup. `fsGroup` makes the kubelet chown the volume to that group on mount.

This is the cost of the non-root user in the Dockerfiles — worth paying, but it has to be paid in both places.

### StatefulSet vs Deployment, decided by a concrete requirement

Neo4j needs its data to survive a pod restart, which a `volumeClaimTemplate` gives it: one PVC per pod, bound by name.

Kafka has a second, sharper reason. KRaft's controller quorum is configured as

```yaml
KAFKA_CONTROLLER_QUORUM_VOTERS: 1@kafka-0.kafka.arxiv-lens.svc.cluster.local:9093
```

A voter has to be addressable as a *specific node*, not as a load-balanced Service. Only a StatefulSet plus a headless Service produces the stable per-pod DNS name `kafka-0.kafka` that this requires. That is the difference between the two controllers made concrete: Deployments treat pods as interchangeable, StatefulSets do not.

### One Kafka listener here, two in compose

Compose needed INTERNAL and EXTERNAL listeners because processes on the host connected as `localhost:9094` while containers used `kafka:9092`. In the cluster every client is a pod, so the EXTERNAL listener has nothing to serve and is dropped.

### `publishNotReadyAddresses: true` ⚠️

query-service crash-looped three times on first deploy:

```
ConfigException: No resolvable bootstrap urls given in bootstrap.servers
```

**Unresolvable**, not refused — and the distinction is the whole bug. A Kafka client retries a refused connection indefinitely, but treats a bootstrap address that does not resolve as fatal and exits. A headless Service publishes DNS only for pods that are *ready*, so while Kafka was still pulling its image the name `kafka` did not exist at all.

`publishNotReadyAddresses: true` publishes the record as soon as the pod has an IP, which converts the fatal error into a retryable one.

Underneath this is a design fact worth internalising: **Kubernetes has no `depends_on`.** The assumption is that every service tolerates its dependencies being absent and retries. The pods did self-heal, which is the system working as intended — but "eventually converges after three crashes" and "starts cleanly" are different quality bars, and only one of them is quiet in a log.

### Readiness and liveness answer different questions

- **readiness** — can this pod take traffic? Failing removes it from the Service's endpoints.
- **liveness** — is this pod still alive? Failing kills the container.

Same probe, opposite consequences. Liveness is deliberately later and slower (`initialDelaySeconds: 90` against readiness's 20), because a tight liveness probe on a slow-starting JVM restarts it before it can finish booting — a restart loop that looks exactly like a crash but is the probe causing it.

query-service uses Spring Boot's `/actuator/health/readiness` and `/liveness`, which exist for precisely this split.

### A Job, not a Deployment, for the graph build

`build_graph` runs to completion and stops. A Deployment would restart it forever, since that is what a Deployment is for. A Job runs it to success and stops, with `backoffLimit` bounding the retries.

It also demonstrated something the compose run could not: against an empty Neo4j the counters were `+300 papers, +1742 entities, +1244 relations` rather than the `+0` an already-populated database returns. The idempotency claim and the correctness claim need different starting states to be visible.

A completed Job is immutable — delete it before re-applying.

### The data volume starts empty

Compose bind-mounted `../graph-pipeline/data`. A cluster has no host to bind to, so the PVC starts empty and the snapshots are copied in once with `kubectl cp`. This is the correct shape — images hold code, volumes hold data — but it is a real step that compose hid, and it is why the Job exists at all.

`serve()` reads nothing at startup, only per request, so the Deployment starts happily before the volume has any content. That was luck rather than design, and it is worth keeping true.

### The Secret is created, never committed

A Secret manifest stores base64, which is encoding and not encryption. `01-secrets.example.yml` is a template with placeholder values; the real one is created imperatively from `.env` and the filename pattern is gitignored. Same rule as `.env` itself, and the same rule the `.dockerignore` enforces for images.

---

## `pipeline/tracking.py` — MLflow

### Reproducible was already true; comparable was not

Immutable snapshots and the `(arxiv_id, version)` extraction cache mean a build can be re-run and produce the same graph. What was missing was a way to ask *did anything change between builds, and at which commit* — the answer lived in terminal scrollback. One MLflow run per build fixes that.

### Params are inputs, metrics are outputs

The distinction is not cosmetic. MLflow lets you filter and group runs by **params** and chart **metrics** across runs, so a number in the wrong bucket becomes unusable.

- **params** — `snapshot_id`, `extraction_model`, `papers_in_snapshot`, the three drift thresholds. Things that were *chosen* and could have been chosen otherwise.
- **metrics** — created counts, `repairs`, `unresolved_types`, `violation_rate`. Things that were *measured*.
- **tags** — `git_commit`, for finding a run again.

A threshold looks like a result when you read the printout, which is what makes it tempting to log as a metric. It is configuration.

### `violation_rate`, not a violation count

```python
"violation_rate": violations / relations if relations else 0.0,
```

Exactly the bug the drift gate shipped with in its first version: a bigger corpus has more violations without being any worse, so the count trends upward on healthy growth. Rates are comparable across builds; counts are not.

### The git commit is the most useful field ⚠️

```python
subprocess.run([...], capture_output=True, text=True, check=True).stdout.strip()
```

`subprocess.run` returns a `CompletedProcess`, not the output — without `capture_output=True` the child writes straight to the terminal and the tag reads `CompletedProcess(args=[...], returncode=0)`. `text=True` gives `str` rather than `bytes`.

It falls back to `"unknown"` rather than raising, because the pipeline can legitimately run somewhere without git — inside the container image, for instance, where `.git` is excluded by `.dockerignore`.

### Tracking must never fail the build ⚠️

The whole body is wrapped in `try/except Exception`, returning `None`. Same rule as `publish_graph_updated`, for the same reason: by the time this runs the graph is already committed to Neo4j, so an outage in an observability system must not turn a good build into a failed one.

This was exercised on the first call rather than in theory. **MLflow 3 put the `./mlruns` file backend into maintenance mode and refuses to write to it**, so the initial run raised `MlflowException`, printed a warning, and the build exited 0. The default is `sqlite:///mlflow.db` now — a local database with no server, overridable through `MLFLOW_TRACKING_URI`, the same local-default pattern as `KAFKA_BOOTSTRAP` and `GRPC_TARGET`.

### `relations_total` and `relations_created` differ, and that is the point

A recent build logged 1245 and 1244. One relation is asserted by two papers, and `MERGE` collapses it into a single edge carrying both ids in `r.papers`. The provenance model, visible as a one-unit gap between two metrics.

---

## Observability — Prometheus and Grafana

### Prometheus pulls

Nothing is pushed. The app exposes `/actuator/prometheus` as text and Prometheus scrapes it every 15s, which is why there is no metrics client configuration in the application and why the scrape target must be reachable *from the Prometheus container* — `query-service:8082`, not `localhost:8082`.

Running the Java service on the host while Prometheus runs in a container is the same `localhost`-means-the-container trap arriving from a new direction: it would need `host.docker.internal:8082`.

### `metrics_path` has to be overridden

Prometheus defaults to `/metrics`; Spring exposes `/actuator/prometheus`. Without the override the target reads DOWN with a 404 — loud, at least.

### The config mount path fails *silently* ⚠️

```yaml
- ./prometheus/prometheus.yml:/etc/prometheus.yml:ro          # wrong
- ./prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro   # right
```

The image ships its own default config at the correct path, so mounting to the wrong one leaves Prometheus running happily with the default: one target, itself, UP. Nothing reports that your config was never read. The tell is the job name on the targets page — if it is not `query-service`, the file was not loaded.

Same shape as `HF_HOME` landing in `/app/opt/hf`: a plausible-but-wrong path where the software has a fallback, so the symptom is wrong behaviour rather than a crash.

### Caffeine records nothing unless asked ⚠️

```java
Caffeine.newBuilder().maximumSize(500).recordStats()
```

Spring Boot registers cache meters for every cache in the `CacheManager` regardless, so `cache_gets_total` appears in the endpoint — reporting `0` forever. You get a dashboard with a flat line and no error anywhere.

Worth distinguishing the two failure states, because they point at different problems:

- **metric absent** — the meter was never registered, so the code is not there
- **metric `0.0`** — registered but never exercised, so the code is there and the traffic is not

The absent case here turned out to be a stale image: the container was running a jar built 57 minutes before `recordStats()` was added. `docker inspect --format '{{.Created}}'` against the source file's mtime settled it in one command. Containers run what was baked in; `bootRun` picks up edits.

### Spring Boot 4 removed `management.metrics.*` ⚠️

Micrometer exports a timer as count/sum/max, and no percentile can be derived from those three numbers. `histogram_quantile()` needs the pre-bucketed `_bucket` series, which Boot 3 enabled with:

```yaml
management.metrics.distribution.percentiles-histogram.http.server.requests: true
```

In Boot 4 that property does not exist. Not renamed — **absent**: dumping `META-INF/spring-configuration-metadata.json` out of `spring-boot-actuator-autoconfigure-4.1.1.jar` returns zero `management.metrics` properties. An environment-variable override did nothing either, which ruled out a YAML binding problem.

The replacement is a `MeterFilter` bean in `MetricsConfig.java`. After that, 138 bucket series.

The general lesson: when a Spring property silently does nothing, read the configuration metadata in the jar before debugging the YAML. It is the authoritative list, and it ships with the dependency.

This is the third Boot 4 migration surprise in this project, after `@MockBean` → `@MockitoBean` and Jackson 2 → `tools.jackson`.

### Grafana is provisioned from files, never clicked

Dashboards built in the UI live in Grafana's database and disappear with the volume. Provisioning puts them in git, where they can be reviewed in a diff.

Three details that each cost a restart:

- **`providers:`, plural.** The singular key is valid YAML and silently ignored.
- **Directory layout.** Grafana reads `provisioning/datasources/` and `provisioning/dashboards/` — subdirectories. A file at `provisioning/dashboards.yml` is never read, and its own log says `can't read dashboard provisioning files from directory`.
- **The datasource needs a fixed `uid`.** Without one Grafana generates a random uid at provision time, and a committed dashboard referencing `"uid": "prometheus"` renders every panel as *datasource not found*.

The dashboard JSON lives in a separate directory from the provider YAML, so Grafana is never handed a config file to parse as a dashboard.

### The panels that are specific to this system

Request rate, p95 and heap are the same four panels any service gets. The cache panels are the ones that describe *this* system: a cold `/api/stats` takes 4.1 s — gRPC to Python, a traversal, a count over every label — and the cached call takes 8 ms. Watching the hit ratio fall to zero and climb back is the `graph.updated` consumer doing its job, which is otherwise only visible as a log line.

---

## Infrastructure gotchas

- **Neo4j has two ports.** 7474 is the HTTP browser; **7687 is Bolt**, which drivers speak. `bolt://localhost:7687`, not the URL you log into.
- **`os.getenv` returns `None` for a missing key** and doesn't raise. `GraphDatabase.driver(None, ...)` then fails several lines later with an error that says nothing about environment variables. Use `os.environ[...]` when the program genuinely cannot run without the value.
- **Run the container with a named volume.** `docker run` without `-v` keeps data in the container's writable layer; a Docker Desktop reset takes the container, the image, and the graph with it. `-v arxiv_lens_neo4j_data:/data --restart unless-stopped`.
- The port opens several seconds before Neo4j accepts Bolt connections — poll `verify_connectivity()` rather than sleeping a fixed time.

---

## Scheduled pipeline — `.github/workflows/pipeline.yml`

The four stages had CLIs from the start; automating them was never the hard part. What made this different from `ci.yml` is that the workflow **spends money, writes to a live database, and is stateful** — and the state is where the cost and the correctness problems turn out to be the same problem.

**`data/extracted/` is doing two jobs at once.** `load_extraction_index()` reuses any `(arxiv_id, version)` already paid for, so a normal week extracts only genuinely new papers. And `drift_detector` compares the last two entries of `list_extracted()`. On an empty runner both fail *silently and in the expensive direction*: extraction re-runs the whole corpus, and the gate prints "first extracted snapshot; nothing to compare" and returns 0. A green run that quietly billed for 300 papers and skipped its own safety check.

**A cache is a performance optimisation; it must never be a correctness dependency.** GitHub evicts caches after seven days unused and drops them when the repository's 10 GB fills. So the cache carries the state, and `--max-new` makes its absence loud: above N pending papers `extract_entities` exits non-zero before `submit_batch` is ever called. Verified by pointing it at a 500-paper snapshot with 300 already extracted — `refusing: 200 new papers exceeds --max-new 5`, exit 1, no batch created. Roughly two dollars not spent.

**Refuse, don't truncate.** Capping `pending` to the first N was the obvious alternative and it is a trap. `failures()` tests `changed_share`, `lost_edge_share` and `violation_rate_increase` — none of which fire on papers merely being *absent*. A truncated snapshot passes the gate, gets loaded, and is recorded in MLflow as the state of the corpus. Exiting is the only option that cannot produce a wrong answer confidently.

**Save the cache after the build, not in a post-step.** `actions/cache` saves in a post-step that runs on failure too. A drift-blocked run would therefore save its rejected snapshot as the baseline, and the next run would compare against the thing the gate just refused — the gate silently re-baselining onto its own failure, which is the exact failure mode a gate exists to prevent. Splitting into `cache/restore` and `cache/save`, with the save after `build_graph`, means a blocked run leaves the baseline untouched. The cost is re-extracting on retry; that is the right trade for a rare event, and the rejected snapshot still uploads as an artifact.

**`concurrency` is the opposite of `ci.yml`'s.** There, a second push makes the first run's answer irrelevant, so `cancel-in-progress: true`. Here, cancelling mid-batch throws away tokens already paid for, and two runs writing the same graph is not something the loaders were asked to survive. Queue, never cancel.

**Two flags are load-bearing and both are easy to miss.** `--limit` defaults to `1` — the safe interactive default — so a workflow that omits it produces a one-paper snapshot. And without `--batch` the module prints extractions instead of writing them. Both silent.

**No snapshot ID is plumbed between steps.** Every downstream stage already defaults to the newest — `latest_snapshot()`, `list_extracted()[-1]` — and IDs are UTC timestamps in a lexically sortable format. Parsing an ID out of stdout would add a failure mode to remove one that does not exist.

**The cron is committed but inert**, gated on a repository *variable* rather than a code change, so arming and disarming it is a settings-page action. A workflow that starts billing the moment it merges is not one to merge.

---

## Next

Everything on the request path is built and exercised end to end: ingest → extract → repair → canonicalize → drift gate → Neo4j → Z3, and REST → gRPC → traversal → cited answer, with `graph.updated` closing the loop back to cache eviction.

The last operational gap is closed too: the pipeline runs on a gated schedule behind secrets, with a cost guard that fails loudly when its cache is gone and a drift gate that stops the build before the graph is touched.

The standing gap is unchanged and worth restating: every gate here measures *stability*, and only the golden eval measures *correctness*. A consistently wrong graph still passes the drift gate, the Z3 checker and the embedding monitor. The eval is 18 questions; that is the number to grow.

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

## Infrastructure gotchas

- **Neo4j has two ports.** 7474 is the HTTP browser; **7687 is Bolt**, which drivers speak. `bolt://localhost:7687`, not the URL you log into.
- **`os.getenv` returns `None` for a missing key** and doesn't raise. `GraphDatabase.driver(None, ...)` then fails several lines later with an error that says nothing about environment variables. Use `os.environ[...]` when the program genuinely cannot run without the value.
- **Run the container with a named volume.** `docker run` without `-v` keeps data in the container's writable layer; a Docker Desktop reset takes the container, the image, and the graph with it. `-v arxiv_lens_neo4j_data:/data --restart unless-stopped`.
- The port opens several seconds before Neo4j accepts Bolt connections — poll `verify_connectivity()` rather than sleeping a fixed time.

---

## Next

Alias table (`LLM` ≡ `Large Language Models`), then retrieval: question → traversal → answer with citations. Nothing consumes the graph yet, which is what still separates this from GraphRAG. The golden QA eval comes after retrieval, because an eval needs something to evaluate — and it closes the real gap: every gate here measures *stability*, none measures *correctness*. A consistently wrong graph passes all of them.

# Ingestion notes

Personal reference for the ingestion stage — the reasoning that used to sit in code comments.

Files covered: `pipeline/models.py`, `pipeline/ingest_arxiv.py`, `pipeline/snapshots.py`.

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

## Next

Entity/relation extraction with Claude, reading a snapshot id. First decision is what counts as an entity in this domain — that comes before any prompt.

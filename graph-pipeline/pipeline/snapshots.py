import json
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .models import Paper

DEFAULT_ROOT = "data/raw"

_SNAPSHOT_ID_FORMAT = "%Y-%m-%dT%H-%M-%SZ"
_SNAPSHOT_ID_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z$")


def dedupe(papers: list[Paper]) -> list[Paper]:
    best: dict[str, Paper] = {}
    for paper in papers:
        existing = best.get(paper.arxiv_id)
        if existing is None or paper.version > existing.version:
            best[paper.arxiv_id] = paper
    return list(best.values())


def write_snapshot(papers: list[Paper], query: str, root: str = DEFAULT_ROOT) -> str:
    snapshot_id = datetime.now(timezone.utc).strftime(_SNAPSHOT_ID_FORMAT)

    root_path = Path(root)
    root_path.mkdir(parents=True, exist_ok=True)

    # rename is only atomic within one filesystem, so stage inside root.
    staging = Path(tempfile.mkdtemp(dir=root_path))

    with (staging / "papers.jsonl").open("w", encoding="utf-8") as handle:
        for paper in papers:
            handle.write(paper.model_dump_json() + "\n")

    metadata = {
        "snapshot_id": snapshot_id,
        "query": query,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "paper_count": len(papers),
    }
    with (staging / "metadata.json").open("w", encoding="utf-8") as handle:
        json.dump(metadata, handle, indent=2)

    staging.rename(root_path / snapshot_id)
    return snapshot_id


def load_snapshot(snapshot_id: str, root: str = DEFAULT_ROOT) -> list[Paper]:
    path = Path(root) / snapshot_id / "papers.jsonl"
    if not path.exists():
        raise FileNotFoundError(f"no snapshot {snapshot_id!r} under {root!r}")

    with path.open(encoding="utf-8") as handle:
        return [Paper.model_validate_json(line) for line in handle if line.strip()]


def load_metadata(snapshot_id: str, root: str = DEFAULT_ROOT) -> dict:
    path = Path(root) / snapshot_id / "metadata.json"
    if not path.exists():
        raise FileNotFoundError(f"no snapshot {snapshot_id!r} under {root!r}")

    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def list_snapshots(root: str = DEFAULT_ROOT) -> list[str]:
    root_path = Path(root)
    if not root_path.exists():
        return []

    return sorted(
        entry.name
        for entry in root_path.iterdir()
        if entry.is_dir() and _SNAPSHOT_ID_RE.match(entry.name)
    )

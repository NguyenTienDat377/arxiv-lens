import json
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .models import ExtractionRecord, Paper, PaperExtraction

DEFAULT_ROOT = "data/raw"
DEFAULT_EXTRACTED_ROOT = "data/extracted"

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


def latest_snapshot(root: str = DEFAULT_ROOT) -> str:
    snapshots = list_snapshots(root)
    if not snapshots:
        raise FileNotFoundError(f"no snapshots under {root!r}")
    return snapshots[-1]


def write_extractions(
    records: list[ExtractionRecord],
    snapshot_id: str,
    root: str = DEFAULT_EXTRACTED_ROOT,
) -> Path:
    root_path = Path(root)
    root_path.mkdir(parents=True, exist_ok=True)

    staging = Path(tempfile.mkdtemp(dir=root_path))

    with (staging / "extractions.jsonl").open("w", encoding="utf-8") as handle:
        for record in sorted(records, key=lambda r: r.arxiv_id):
            handle.write(record.model_dump_json() + "\n")

    metadata = {
        "snapshot_id": snapshot_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "paper_count": len(records),
    }
    with (staging / "metadata.json").open("w", encoding="utf-8") as handle:
        json.dump(metadata, handle, indent=2)

    destination = root_path / snapshot_id
    if destination.exists():
        shutil.rmtree(destination)
    staging.rename(destination)
    return destination


def load_extractions(
    snapshot_id: str, root: str = DEFAULT_EXTRACTED_ROOT
) -> list[ExtractionRecord]:
    path = Path(root) / snapshot_id / "extractions.jsonl"
    if not path.exists():
        raise FileNotFoundError(f"no extractions for {snapshot_id!r} under {root!r}")

    with path.open(encoding="utf-8") as handle:
        return [ExtractionRecord.model_validate_json(line) for line in handle if line.strip()]


def list_extracted(root: str = DEFAULT_EXTRACTED_ROOT) -> list[str]:
    root_path = Path(root)
    if not root_path.exists():
        return []

    return sorted(
        entry.name
        for entry in root_path.iterdir()
        if entry.is_dir() and _SNAPSHOT_ID_RE.match(entry.name)
    )


def load_extraction_index(
    root: str = DEFAULT_EXTRACTED_ROOT,
) -> dict[tuple[str, int], PaperExtraction]:
    index: dict[tuple[str, int], PaperExtraction] = {}
    for snapshot_id in list_extracted(root):
        for record in load_extractions(snapshot_id, root):
            index[(record.arxiv_id, record.version)] = record.extraction
    return index

import functools
from pathlib import Path

from .snapshots import list_extracted, load_snapshot

EMBEDDER = "sentence-transformers/all-MiniLM-L6-v2"
CACHE = Path("data/embeddings")


@functools.cache
def model():
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(EMBEDDER)


@functools.cache
def embed_snapshot(snapshot_id: str | None = None):
    """Vectors for one snapshot's abstracts, cached on disk between runs.

    Shared by the vector baseline, entity linking and the drift monitor so all
    three necessarily see the same numbers.
    """
    import numpy as np

    # The latest *extracted* snapshot, not the latest raw one: the baseline has
    # to see exactly the corpus the graph was built from or the comparison is rigged.
    snapshot = snapshot_id or list_extracted()[-1]
    papers = load_snapshot(snapshot)
    path = CACHE / f"{snapshot}.npz"

    if path.exists():
        stored = np.load(path, allow_pickle=True)
        return (
            list(stored["ids"]),
            list(stored["titles"]),
            list(stored["texts"]),
            stored["vectors"],
        )

    # One abstract is one chunk. They are short enough that chunking would only
    # introduce boundary problems, so the baseline is given the easier setup.
    texts = [f"{p.title}\n\n{p.abstract}" for p in papers]
    vectors = model().encode(texts, normalize_embeddings=True, show_progress_bar=True)

    CACHE.mkdir(parents=True, exist_ok=True)
    np.savez(
        path,
        ids=np.array([p.arxiv_id for p in papers]),
        titles=np.array([p.title for p in papers]),
        texts=np.array(texts),
        vectors=vectors,
    )
    return [p.arxiv_id for p in papers], [p.title for p in papers], texts, vectors


def vectors_for(snapshot_id: str, arxiv_ids: list[str]):
    """The rows of a snapshot's embedding matrix belonging to `arxiv_ids`."""
    ids, _, _, vectors = embed_snapshot(snapshot_id)
    index = {arxiv_id: row for row, arxiv_id in enumerate(ids)}
    return vectors[[index[a] for a in arxiv_ids if a in index]]

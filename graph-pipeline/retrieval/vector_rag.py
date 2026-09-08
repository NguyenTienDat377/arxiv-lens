import argparse
import functools
import json
from pathlib import Path

import anthropic
from anthropic.types import TextBlockParam
from dotenv import load_dotenv

from pipeline.snapshots import list_extracted, load_snapshot

load_dotenv()

MODEL = "claude-sonnet-5"
MAX_TOKENS = 4096
EMBEDDER = "sentence-transformers/all-MiniLM-L6-v2"
CACHE = Path("data/embeddings")
TOP_K = 5

ANSWER_PROMPT = """You answer questions about neuro-symbolic AI research using \
only the paper abstracts supplied.

Cite papers inline as [arxiv_id]. Do not use knowledge beyond the supplied \
abstracts, and say plainly when they do not contain enough to answer. Be concise."""


@functools.cache
def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic()


@functools.cache
def _model():
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(EMBEDDER)


@functools.cache
def _corpus(snapshot_id: str | None = None):
    import numpy as np

    # The latest *extracted* snapshot, not the latest raw one: the baseline has
    # to see exactly the corpus the graph was built from or the comparison is rigged.
    snapshot = snapshot_id or list_extracted()[-1]
    papers = load_snapshot(snapshot)
    path = CACHE / f"{snapshot}.npz"

    if path.exists():
        stored = np.load(path, allow_pickle=True)
        return list(stored["ids"]), list(stored["titles"]), list(stored["texts"]), stored["vectors"]

    # One abstract is one chunk. They are short enough that chunking would only
    # introduce boundary problems, so the baseline is given the easier setup.
    texts = [f"{p.title}\n\n{p.abstract}" for p in papers]
    vectors = _model().encode(texts, normalize_embeddings=True, show_progress_bar=True)

    CACHE.mkdir(parents=True, exist_ok=True)
    np.savez(
        path,
        ids=np.array([p.arxiv_id for p in papers]),
        titles=np.array([p.title for p in papers]),
        texts=np.array(texts),
        vectors=vectors,
    )
    return [p.arxiv_id for p in papers], [p.title for p in papers], texts, vectors


def retrieve(question: str, k: int = TOP_K, snapshot_id: str | None = None) -> list[dict]:
    import numpy as np

    ids, titles, texts, vectors = _corpus(snapshot_id)
    query = _model().encode([question], normalize_embeddings=True)[0]
    scores = vectors @ query
    top = np.argsort(-scores)[:k]
    return [
        {"arxiv_id": ids[i], "title": titles[i], "text": texts[i], "score": float(scores[i])}
        for i in top
    ]


def answer(question: str, docs: list[dict]) -> str:
    context = "\n\n".join(f"[{d['arxiv_id']}] {d['text']}" for d in docs)
    response = _client().messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        output_config={"effort": "low"},
        system=[TextBlockParam(type="text", text=ANSWER_PROMPT,
                               cache_control={"type": "ephemeral"})],
        messages=[{"role": "user", "content": f"Question: {question}\n\nAbstracts:\n{context}"}],
    )
    return "".join(b.text for b in response.content if b.type == "text")


def ask(question: str, k: int = TOP_K, verbose: bool = False) -> str:
    docs = retrieve(question, k)
    if verbose:
        for d in docs:
            print(f"  {d['score']:.3f}  [{d['arxiv_id']}] {d['title'][:60]}")
    return answer(question, docs)


def main() -> None:
    parser = argparse.ArgumentParser(description="Vector-RAG baseline over abstracts.")
    parser.add_argument("question")
    parser.add_argument("-k", type=int, default=TOP_K)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    print(ask(args.question, args.k, args.verbose))


if __name__ == "__main__":
    main()

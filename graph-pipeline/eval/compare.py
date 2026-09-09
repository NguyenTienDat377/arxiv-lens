import argparse
import json
from pathlib import Path

from retrieval.graph_rag import QueryIntent, QueryPlan, link, traverse
from retrieval.vector_rag import TOP_K, retrieve

GOLDEN = Path(__file__).with_name("golden.json")


def graph_papers(case: dict) -> tuple[set[str], int]:
    linked = [name for name in (link(e) for e in case["entities"]) if name]
    needed = 2 if case["intent"] == QueryIntent.PATH_BETWEEN else 1
    if len(linked) < needed:
        return set(), 0
    facts = traverse(
        QueryPlan(intent=QueryIntent(case["intent"]), entities=case["entities"]), linked
    )
    return {p for f in facts for p in (f["papers"] or [])}, len(facts)


def vector_papers(case: dict, k: int) -> set[str]:
    return {d["arxiv_id"] for d in retrieve(case["question"], k)}


def recall(expected: list[str], found: set[str]) -> float | None:
    if not expected:
        return None
    return len([e for e in expected if e in found]) / len(expected)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Graph retrieval vs a vector baseline on the golden set."
    )
    parser.add_argument("--golden", default=str(GOLDEN))
    parser.add_argument("-k", type=int, default=TOP_K)
    args = parser.parse_args()

    cases = json.loads(Path(args.golden).read_text())
    positives = [c for c in cases if not c.get("expect_empty")]
    negatives = [c for c in cases if c.get("expect_empty")]

    print(f"PAPER RECALL  (graph traversal vs top-{args.k} vector search)\n")
    print(f"{'graph':>7} {'vector':>7}   {'ret':>7}   question")
    graph_scores, vector_scores = [], []
    for case in positives:
        found_graph, facts = graph_papers(case)
        found_vector = vector_papers(case, args.k)
        g = recall(case["expect_papers"], found_graph)
        v = recall(case["expect_papers"], found_vector)
        if g is None:
            continue
        graph_scores.append(g)
        vector_scores.append(v)
        flag = "  " if g >= (v or 0) else " *"
        print(f"{g:>7.0%} {v:>7.0%}{flag} {len(found_graph):>3}/{args.k:<3}   {case['question']}")

    n = len(graph_scores)
    print(f"\n  mean over {n} answerable questions:"
          f"  graph {sum(graph_scores)/n:.0%}   vector {sum(vector_scores)/n:.0%}")

    print("\n\nNEGATIVES  (the correct answer is 'no connection' or 'not stated')\n")
    print(f"{'graph':>7} {'vector':>7}   question")
    for case in negatives:
        found_graph, facts = graph_papers(case)
        found_vector = vector_papers(case, args.k)
        print(f"{facts:>7} {len(found_vector):>7}   {case['question']}")
    print("\n  columns are facts/papers returned. The graph returns nothing when")
    print("  nothing is asserted; top-k always returns k, whatever the question.")


if __name__ == "__main__":
    main()

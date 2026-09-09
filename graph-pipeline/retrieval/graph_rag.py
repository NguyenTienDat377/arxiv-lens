import argparse
import functools
from enum import StrEnum

import anthropic
from anthropic.types import TextBlockParam
from pydantic import BaseModel, ConfigDict

from pipeline.build_graph import LABELS, _driver
from pipeline.canonicalize import _group_key

MODEL = "claude-sonnet-5"
MAX_TOKENS = 4096
# Calibrated on the entity vocabulary: every correct match scores above it,
# every wrong one below. "neural nets" -> "deep learning" sits at 0.68.
LINK_THRESHOLD = 0.80


class QueryIntent(StrEnum):
    WHAT_ADDRESSES = "WHAT_ADDRESSES"
    WHAT_USES = "WHAT_USES"
    EVALUATED_ON = "EVALUATED_ON"
    LINEAGE = "LINEAGE"
    NEIGHBOURHOOD = "NEIGHBOURHOOD"
    PATH_BETWEEN = "PATH_BETWEEN"


class QueryPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: QueryIntent
    entities: list[str]


PLANNER_PROMPT = """You turn a question about neuro-symbolic AI research into a \
graph traversal over a knowledge graph of arXiv papers.

The graph has six entity types: METHOD, TASK, FORMALISM, MODEL, DATASET, PROBLEM.
They are connected by EXTENDS, USES, EVALUATED_ON, ADDRESSES, COMBINES and \
COMPILES_TO.

Choose one intent:
- WHAT_ADDRESSES: what methods tackle a given task or problem. Entity: the task \
or problem.
- WHAT_USES: what methods consume a given component. Entity: the component.
- EVALUATED_ON: what datasets or benchmarks a given method was measured against. \
Entity: the method.
- LINEAGE: what builds on or descends from a given method or formalism. Entity: \
the ancestor.
- NEIGHBOURHOOD: everything known about one entity. Entity: that entity.
- PATH_BETWEEN: how two entities are connected. Entities: both, in the order the \
question mentions them.

List the entity names exactly as the question writes them, expanding nothing. \
PATH_BETWEEN takes two entities; every other intent takes one."""

ANSWER_PROMPT = """You answer questions about neuro-symbolic AI research using \
only the knowledge-graph facts supplied.

Each fact carries the arXiv ids of the papers that assert it. Cite them inline as \
[arxiv_id]. Do not use knowledge beyond the supplied facts, and say plainly when \
the graph does not contain enough to answer. Be concise."""

TRAVERSALS: dict[QueryIntent, str] = {
    QueryIntent.WHAT_ADDRESSES: """
        MATCH (m)-[r:ADDRESSES]->(x {name: $a})
        RETURN m.name AS subject, 'ADDRESSES' AS predicate, x.name AS object,
               r.papers[..3] AS papers
    """,
    QueryIntent.WHAT_USES: """
        MATCH (m)-[r:USES]->(x {name: $a})
        RETURN m.name AS subject, 'USES' AS predicate, x.name AS object,
               r.papers[..3] AS papers
    """,
    QueryIntent.EVALUATED_ON: """
        MATCH (x {name: $a})-[r:EVALUATED_ON]->(d)
        RETURN x.name AS subject, 'EVALUATED_ON' AS predicate, d.name AS object,
               r.papers[..3] AS papers
    """,
    QueryIntent.LINEAGE: """
        MATCH path = (m)-[:EXTENDS*1..4]->(x {name: $a})
        UNWIND relationships(path) AS r
        RETURN DISTINCT startNode(r).name AS subject, 'EXTENDS' AS predicate,
               endNode(r).name AS object, r.papers[..3] AS papers
    """,
    QueryIntent.NEIGHBOURHOOD: """
        MATCH (x {name: $a})-[r]-(y)
        WHERE type(r) <> 'MENTIONED_IN' AND NOT y:Paper
        RETURN startNode(r).name AS subject, type(r) AS predicate,
               endNode(r).name AS object, r.papers[..3] AS papers
    """,
    QueryIntent.PATH_BETWEEN: """
        MATCH (x {name: $a}), (y {name: $b})
        MATCH path = shortestPath((x)-[*1..6]-(y))
        UNWIND relationships(path) AS r
        WITH r WHERE type(r) <> 'MENTIONED_IN'
        RETURN DISTINCT startNode(r).name AS subject, type(r) AS predicate,
               endNode(r).name AS object, r.papers[..3] AS papers
    """,
}


@functools.cache
def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic()


@functools.cache
def _name_index() -> dict[str, str]:
    with _driver().session() as session:
        rows = session.run(
            "MATCH (n) WHERE any(l IN labels(n) WHERE l IN $labels) RETURN n.name AS name",
            labels=list(LABELS.values()),
        )
        return {_group_key(row["name"]): row["name"] for row in rows}


@functools.cache
def _entity_vectors():
    from pipeline.embeddings import model

    names = sorted(set(_name_index().values()))
    return names, model().encode(names, normalize_embeddings=True,
                                  show_progress_bar=False)


def _nearest(text: str) -> str | None:
    try:
        import numpy as np

        from pipeline.embeddings import model
    except ImportError:
        return None

    names, vectors = _entity_vectors()
    scores = vectors @ model().encode([text], normalize_embeddings=True)[0]
    best = int(np.argmax(scores))
    return names[best] if scores[best] >= LINK_THRESHOLD else None


def link(text: str) -> str | None:
    index = _name_index()
    key = _group_key(text)
    if key in index:
        return index[key]
    return _nearest(text)


def plan(question: str) -> QueryPlan:
    response = _client().messages.parse(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        output_config={"effort": "low"},
        system=[TextBlockParam(type="text", text=PLANNER_PROMPT,
                               cache_control={"type": "ephemeral"})],
        messages=[{"role": "user", "content": question}],
        output_format=QueryPlan,
    )
    return response.parsed_output


def traverse(query_plan: QueryPlan, linked: list[str]) -> list[dict]:
    params = {"a": linked[0], "b": linked[1] if len(linked) > 1 else None}
    with _driver().session() as session:
        return [dict(row) for row in session.run(TRAVERSALS[query_plan.intent], **params)]


def context(facts: list[dict]) -> str:
    lines = []
    for fact in facts:
        cites = " ".join(f"[{p}]" for p in fact["papers"])
        lines.append(f"{fact['subject']} --{fact['predicate']}--> {fact['object']} {cites}")
    return "\n".join(lines)


def answer(question: str, facts: list[dict]) -> str:
    response = _client().messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        output_config={"effort": "low"},
        system=[TextBlockParam(type="text", text=ANSWER_PROMPT,
                               cache_control={"type": "ephemeral"})],
        messages=[{
            "role": "user",
            "content": f"Question: {question}\n\nGraph facts:\n{context(facts)}",
        }],
    )
    return "".join(b.text for b in response.content if b.type == "text")


def ask(question: str, verbose: bool = False) -> str:
    query_plan = plan(question)
    linked = [link(name) for name in query_plan.entities]

    if verbose:
        print(f"  intent    {query_plan.intent}")
        for raw, resolved in zip(query_plan.entities, linked, strict=True):
            print(f"  entity    {raw!r} -> {resolved!r}")

    missing = [
        raw
        for raw, resolved in zip(query_plan.entities, linked, strict=True)
        if resolved is None
    ]
    if missing:
        return f"Not in the graph: {', '.join(repr(m) for m in missing)}"

    facts = traverse(query_plan, [name for name in linked if name])
    if verbose:
        print(f"  facts     {len(facts)}")
    if not facts:
        return "The graph holds no facts matching that traversal."
    return answer(question, facts)


def main() -> None:
    parser = argparse.ArgumentParser(description="Ask the knowledge graph a question.")
    parser.add_argument("question")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    print(ask(args.question, args.verbose))


if __name__ == "__main__":
    main()

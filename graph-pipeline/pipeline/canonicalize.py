import argparse
import re
from collections import Counter, defaultdict

from .extract_entities import repair
from .models import Entity, ExtractionRecord, Relation
from .ontology import RELATION_SPECS, EntityType
from .snapshots import list_extracted, load_extractions


TYPE_PRECEDENCE = [
    EntityType.DATASET,
    EntityType.MODEL,
    EntityType.FORMALISM,
    EntityType.METHOD,
    EntityType.TASK,
    EntityType.PROBLEM,
]

_PUNCTUATION = re.compile(r"[\-_/]+")
_WHITESPACE = re.compile(r"\s+")


ALIASES: dict[str, str] = {
    "LLM": "Large Language Models",
    "MLLM": "Multimodal Large Language Models",
    "VLM": "Vision-Language Model",
    "RAG": "Retrieval-Augmented Generation",
    "LTN": "Logic Tensor Networks",
    "ASP": "Answer Set Programming",
    "Answer Set Programs": "Answer Set Programming",
    "ILP": "Inductive Logic Programming",
    "SMT": "Satisfiability Modulo Theories",
    "KG": "knowledge graph",
    "CoT": "Chain-of-Thought",
    "GNN": "graph neural network",
    "DRL": "deep reinforcement learning",
    "RL": "reinforcement learning",
    "PDDL": "Planning Domain Definition Language",
    "MCTS": "Monte Carlo Tree Search",
    "DSL": "domain-specific language",
    "NLU": "natural language understanding",
    "NLI": "natural language inference",
    "SLM": "Small Language Models",
    "OWL": "Web Ontology Language",
}


def normalize(name: str) -> str:
    spaced = _WHITESPACE.sub(" ", _PUNCTUATION.sub(" ", name)).strip()
    words = []
    for word in spaced.split():
        if len(word) > 3 and word.endswith("s") and not word.isupper():
            word = word[:-1]
        words.append(word.lower())
    return " ".join(words)


_ALIAS_KEYS = {normalize(k): normalize(v) for k, v in ALIASES.items()}


def _group_key(name: str) -> str:
    key = normalize(name)
    return _ALIAS_KEYS.get(key, key)


def canonical_names(records: list[ExtractionRecord]) -> dict[str, str]:
    counts: Counter[str] = Counter()
    for record in records:
        for entity in record.extraction.entities:
            counts[entity.name] += 1

    groups: dict[str, list[str]] = defaultdict(list)
    for name in counts:
        groups[_group_key(name)].append(name)

    return {
        name: min(variants, key=lambda v: (-counts[v], len(v), v))
        for variants in groups.values()
        for name in variants
    }


def resolve_types(
    records: list[ExtractionRecord], names: dict[str, str]
) -> tuple[dict[str, EntityType], list[str]]:
    votes: dict[str, Counter[EntityType]] = defaultdict(Counter)
    slots: dict[str, list[frozenset[EntityType]]] = defaultdict(list)

    for record in records:
        for entity in record.extraction.entities:
            votes[names.get(entity.name, entity.name)][entity.type] += 1
        for relation in record.extraction.relations:
            spec = RELATION_SPECS[relation.predicate]
            slots[names.get(relation.subject, relation.subject)].append(spec.domain)
            slots[names.get(relation.object, relation.object)].append(spec.range)

    resolved: dict[str, EntityType] = {}
    unresolved: list[str] = []

    for name, tally in votes.items():
        candidates = list(tally)
        if len(candidates) == 1:
            resolved[name] = candidates[0]
            continue

        fit = {t: sum(t in slot for slot in slots.get(name, ())) for t in candidates}
        candidates = [t for t in candidates if fit[t] == max(fit.values())]

        if len(candidates) > 1:
            top = max(tally[t] for t in candidates)
            candidates = [t for t in candidates if tally[t] == top]

        if len(candidates) > 1:
            candidates.sort(key=TYPE_PRECEDENCE.index)
            unresolved.append(
                f"{name}: {sorted(str(t) for t in candidates)} -> {candidates[0]}"
            )

        resolved[name] = candidates[0]

    return resolved, unresolved


def canonicalize(
    records: list[ExtractionRecord],
) -> tuple[list[ExtractionRecord], dict[str, str], list[str], list[str]]:
    names = canonical_names(records)
    types, unresolved = resolve_types(records, names)

    rewritten: list[ExtractionRecord] = []
    repairs: list[str] = []
    for record in records:
        entities = {
            names[e.name]: Entity(name=names[e.name], type=types[names[e.name]])
            for e in record.extraction.entities
        }
        relations = {
            (names[r.subject], r.predicate, names[r.object]): Relation(
                subject=names[r.subject],
                predicate=r.predicate,
                object=names[r.object],
            )
            for r in record.extraction.relations
        }
        merged = record.extraction.model_copy(
            update={
                "entities": list(entities.values()),
                "relations": list(relations.values()),
            }
        )
        # Global type resolution can invalidate an edge that was legal under the
        # per-paper types, so the ontology gets the last word after the merge.
        merged, log = repair(merged)
        repairs += [f"{record.arxiv_id}  {line}" for line in log]
        rewritten.append(record.model_copy(update={"extraction": merged}))

    return rewritten, names, unresolved, repairs


def main() -> None:
    parser = argparse.ArgumentParser(description="Canonicalize extracted entities.")
    parser.add_argument("--snapshot", default=None)
    args = parser.parse_args()

    snapshot_id = args.snapshot or list_extracted()[-1]
    records = load_extractions(snapshot_id)
    merged, names, unresolved, repairs = canonicalize(records)

    before = {e.name for r in records for e in r.extraction.entities}
    after = {e.name for r in merged for e in r.extraction.entities}
    print(f"names      {len(before)} -> {len(after)}")
    print(f"entities   {sum(len(r.extraction.entities) for r in records)} -> "
          f"{sum(len(r.extraction.entities) for r in merged)} mentions")
    print(f"relations  {sum(len(r.extraction.relations) for r in records)} -> "
          f"{sum(len(r.extraction.relations) for r in merged)}")

    print(f"\nmerged into a different surface form:")
    for variant, canonical in sorted(names.items()):
        if variant != canonical:
            print(f"  {variant!r} -> {canonical!r}")

    print(f"\nrepaired after merging ({len(repairs)}):")
    for line in repairs:
        print(f"  {line}")

    print(f"\ntype resolved by precedence only ({len(unresolved)}, review these):")
    for line in unresolved:
        print(f"  {line}")


if __name__ == "__main__":
    main()

import argparse
import sys
from collections import defaultdict

import z3

from .build_graph import LABELS, _driver
from .ontology import RELATION_SPECS, EntityType, RelationType

Edge = tuple[str, str]

_TYPE_SORT, _TYPE_CONSTS = z3.EnumSort("EntityType", [str(t) for t in EntityType])
_TYPE_BY_NAME = dict(zip((str(t) for t in EntityType), _TYPE_CONSTS))


def load_graph(session) -> tuple[dict[str, EntityType], dict[RelationType, list[Edge]]]:
    labels = {v: k for k, v in LABELS.items()}
    types = {
        row["name"]: labels[row["label"]]
        for row in session.run(
            """
            MATCH (n) WHERE any(l IN labels(n) WHERE l IN $labels)
            RETURN n.name AS name, head([l IN labels(n) WHERE l IN $labels]) AS label
            """,
            labels=list(LABELS.values()),
        )
    }

    edges: dict[RelationType, list[Edge]] = defaultdict(list)
    for predicate in RelationType:
        edges[predicate] = [
            (row["a"], row["b"])
            for row in session.run(
                f"MATCH (a)-[:{predicate}]->(b) RETURN a.name AS a, b.name AS b"
            )
        ]
    return types, edges


def _closure(pairs: list[Edge]) -> set[Edge]:
    """Transitive closure of the observed edges, by fixpoint."""
    reachable = set(pairs)
    while True:
        grown = reachable | {
            (a, d) for a, b in reachable for c, d in reachable if b == c
        }
        if grown == reachable:
            return reachable
        reachable = grown


def build_solver(
    types: dict[str, EntityType], edges: dict[RelationType, list[Edge]]
) -> tuple[z3.Solver, dict[str, str]]:
    solver = z3.Solver()
    solver.set(unsat_core=True)
    labels: dict[str, str] = {}

    # Each entity gets one variable over the type sort. A variable holds exactly
    # one value, so type disjointness is structural rather than asserted.
    type_of = {name: z3.Const(f"type_{name}", _TYPE_SORT) for name in types}
    for name, entity_type in types.items():
        solver.add(type_of[name] == _TYPE_BY_NAME[str(entity_type)])

    def track(constraint, key: str, description: str) -> None:
        labels[key] = description
        solver.assert_and_track(constraint, z3.Bool(key))

    holds: dict[tuple[RelationType, str, str], z3.BoolRef] = {}
    for predicate, spec in RELATION_SPECS.items():
        pairs = edges.get(predicate, [])
        # Only pairs the edges can actually reach need a variable. Quantifying
        # over every node pair is what makes a naive encoding intractable:
        # ADDRESSES alone would need ~500k booleans instead of 481.
        scope = _closure(pairs) if spec.transitive else set(pairs)
        scope |= {(b, a) for a, b in scope}

        for a, b in scope:
            holds[(predicate, a, b)] = z3.Bool(f"{predicate}({a},{b})")

        for a, b in pairs:
            if a not in type_of or b not in type_of:
                continue
            key = f"edge:{predicate}:{a}->{b}"
            track(
                z3.And(
                    holds[(predicate, a, b)],
                    z3.Or([type_of[a] == _TYPE_BY_NAME[str(t)] for t in spec.domain]),
                    z3.Or([type_of[b] == _TYPE_BY_NAME[str(t)] for t in spec.range]),
                ),
                key,
                f"{a} --{predicate}--> {b}",
            )

        # Properties are facts about the relation itself, so they are asserted
        # plainly: only edges appear in an unsat core, never the ontology.
        for a, b in scope:
            if spec.irreflexive and a == b:
                solver.add(z3.Not(holds[(predicate, a, b)]))
            if spec.asymmetric and a != b:
                solver.add(
                    z3.Implies(holds[(predicate, a, b)],
                               z3.Not(holds[(predicate, b, a)]))
                )
            if spec.symmetric and a != b:
                solver.add(
                    z3.Implies(holds[(predicate, a, b)], holds[(predicate, b, a)])
                )

        if spec.transitive:
            outgoing: dict[str, list[str]] = defaultdict(list)
            for a, b in scope:
                outgoing[a].append(b)
            for a, b in scope:
                for c in outgoing[b]:
                    if (predicate, a, c) in holds:
                        solver.add(
                            z3.Implies(
                                z3.And(holds[(predicate, a, b)],
                                       holds[(predicate, b, c)]),
                                holds[(predicate, a, c)],
                            )
                        )

    return solver, labels


def find_conflicts(
    types: dict[str, EntityType],
    edges: dict[RelationType, list[Edge]],
    limit: int = 20,
) -> list[list[str]]:
    """Repeatedly solve, dropping one edge of each core to expose the next."""
    conflicts: list[list[str]] = []
    working = {predicate: list(pairs) for predicate, pairs in edges.items()}

    while len(conflicts) < limit:
        solver, labels = build_solver(types, working)
        if solver.check() == z3.sat:
            break

        core = [labels[str(term)] for term in solver.unsat_core()]
        if not core:
            break
        conflicts.append(sorted(core))

        # Drop one edge from the core so the next solve finds a different clash.
        predicate_name, _, rest = str(solver.unsat_core()[0]).partition(":")[2].partition(":")
        subject, _, obj = rest.partition("->")
        working[RelationType(predicate_name)] = [
            pair
            for pair in working[RelationType(predicate_name)]
            if pair != (subject, obj)
        ]

    return conflicts


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Check the graph against the ontology's logical properties."
    )
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()

    driver = _driver()
    driver.verify_connectivity()
    with driver.session() as session:
        types, edges = load_graph(session)
    driver.close()

    total = sum(len(pairs) for pairs in edges.values())
    print(f"checking {len(types)} entities, {total} relations")
    for predicate, spec in RELATION_SPECS.items():
        properties = [
            name
            for name in ("transitive", "symmetric", "irreflexive", "asymmetric")
            if getattr(spec, name)
        ]
        print(f"  {predicate:<14} {len(edges[predicate]):>4} edges  {', '.join(properties) or '-'}")

    conflicts = find_conflicts(types, edges, args.limit)
    if not conflicts:
        print("\nconsistent — no contradiction under the ontology")
        return

    print(f"\n{len(conflicts)} contradiction(s):")
    for core in conflicts:
        print("  ---")
        for line in core:
            print(f"    {line}")
    sys.exit(1)


if __name__ == "__main__":
    main()

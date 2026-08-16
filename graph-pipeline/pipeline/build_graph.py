import argparse
import functools
import os
from collections import defaultdict

from dotenv import load_dotenv
from neo4j import Driver, GraphDatabase, Session

from .canonicalize import canonicalize
from .models import ExtractionRecord, Paper
from .ontology import EntityType, RelationType
from .snapshots import list_extracted, load_extractions, load_snapshot

load_dotenv()

# Cypher cannot parameterize a label, so every label we interpolate has to come
# from this table rather than from data.
LABELS: dict[EntityType, str] = {
    EntityType.METHOD: "Method",
    EntityType.TASK: "Task",
    EntityType.FORMALISM: "Formalism",
    EntityType.MODEL: "Model",
    EntityType.DATASET: "Dataset",
    EntityType.PROBLEM: "Problem",
}


@functools.cache
def _driver() -> Driver:
    return GraphDatabase.driver(
        os.environ["NEO4J_URL"],
        auth=(os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"]),
    )


def create_constraints(session: Session) -> None:
    for label in LABELS.values():
        session.run(
            f"CREATE CONSTRAINT {label.lower()}_name IF NOT EXISTS "
            f"FOR (n:{label}) REQUIRE n.name IS UNIQUE"
        )
    session.run(
        "CREATE CONSTRAINT paper_arxiv_id IF NOT EXISTS "
        "FOR (p:Paper) REQUIRE p.arxiv_id IS UNIQUE"
    )


def load_papers(session: Session, papers: list[Paper], snapshot_id: str) -> int:
    rows = [
        {
            "arxiv_id": p.arxiv_id,
            "version": p.version,
            "title": p.title,
            "published": p.published.isoformat(),
            "categories": p.categories,
        }
        for p in papers
    ]
    result = session.run(
        """
        UNWIND $rows AS row
        MERGE (p:Paper {arxiv_id: row.arxiv_id})
        SET p.version = row.version,
            p.title = row.title,
            p.published = datetime(row.published),
            p.categories = row.categories,
            p.last_seen = $snapshot
        """,
        rows=rows,
        snapshot=snapshot_id,
    )
    return result.consume().counters.nodes_created


def reconcile_labels(session: Session, types: dict[str, EntityType]) -> list[str]:
    """Move nodes whose resolved type changed since the last build."""
    existing = {
        record["name"]: record["label"]
        for record in session.run(
            """
            MATCH (n)
            WHERE any(l IN labels(n) WHERE l IN $labels)
            RETURN n.name AS name,
                   head([l IN labels(n) WHERE l IN $labels]) AS label
            """,
            labels=list(LABELS.values()),
        )
    }

    moves: dict[tuple[str, str], list[str]] = defaultdict(list)
    for name, entity_type in types.items():
        was = existing.get(name)
        if was is not None and was != LABELS[entity_type]:
            moves[(was, LABELS[entity_type])].append(name)

    for (old, new), names in moves.items():
        session.run(
            f"UNWIND $names AS name "
            f"MATCH (n:{old} {{name: name}}) REMOVE n:{old} SET n:{new}",
            names=names,
        )

    return [
        f"{name}: {old} -> {new}"
        for (old, new), names in moves.items()
        for name in names
    ]


def load_entities(
    session: Session, types: dict[str, EntityType], snapshot_id: str
) -> int:
    by_label: dict[str, list[str]] = defaultdict(list)
    for name, entity_type in types.items():
        by_label[LABELS[entity_type]].append(name)

    created = 0
    for label, names in by_label.items():
        result = session.run(
            f"""
            UNWIND $names AS name
            MERGE (n:{label} {{name: name}})
            ON CREATE SET n.first_seen = $snapshot
            SET n.last_seen = $snapshot
            """,
            names=names,
            snapshot=snapshot_id,
        )
        created += result.consume().counters.nodes_created
    return created


def load_mentions(
    session: Session,
    records: list[ExtractionRecord],
    types: dict[str, EntityType],
    snapshot_id: str,
) -> int:
    by_label: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        for entity in record.extraction.entities:
            label = LABELS[types[entity.name]]
            by_label[label].append(
                {"name": entity.name, "arxiv_id": record.arxiv_id}
            )

    created = 0
    for label, rows in by_label.items():
        result = session.run(
            f"""
            UNWIND $rows AS row
            MATCH (e:{label} {{name: row.name}})
            MATCH (p:Paper {{arxiv_id: row.arxiv_id}})
            MERGE (e)-[r:MENTIONED_IN]->(p)
            ON CREATE SET r.first_seen = $snapshot
            SET r.last_seen = $snapshot
            """,
            rows=rows,
            snapshot=snapshot_id,
        )
        created += result.consume().counters.relationships_created
    return created


def load_relations(
    session: Session,
    records: list[ExtractionRecord],
    types: dict[str, EntityType],
    snapshot_id: str,
) -> int:
    # Grouped by (subject label, predicate, object label) so each query can name
    # its labels and relationship type literally, and match on the indexed key.
    groups: dict[tuple[str, RelationType, str], list[dict]] = defaultdict(list)
    for record in records:
        for relation in record.extraction.relations:
            if relation.subject not in types or relation.object not in types:
                continue
            key = (
                LABELS[types[relation.subject]],
                relation.predicate,
                LABELS[types[relation.object]],
            )
            groups[key].append(
                {
                    "subject": relation.subject,
                    "object": relation.object,
                    "arxiv_id": record.arxiv_id,
                }
            )

    created = 0
    for (subject_label, predicate, object_label), rows in groups.items():
        result = session.run(
            f"""
            UNWIND $rows AS row
            MATCH (a:{subject_label} {{name: row.subject}})
            MATCH (b:{object_label} {{name: row.object}})
            MERGE (a)-[r:{predicate}]->(b)
            ON CREATE SET r.first_seen = $snapshot, r.snapshots = [$snapshot]
            SET r.last_seen = $snapshot,
                r.snapshots = CASE
                    WHEN $snapshot IN r.snapshots THEN r.snapshots
                    ELSE r.snapshots + $snapshot
                END
            """,
            rows=rows,
            snapshot=snapshot_id,
        )
        created += result.consume().counters.relationships_created
    return created


def main() -> None:
    parser = argparse.ArgumentParser(description="Load a snapshot into Neo4j.")
    parser.add_argument("--snapshot", default=None)
    parser.add_argument("--reset", action="store_true", help="delete the graph first")
    args = parser.parse_args()

    snapshot_id = args.snapshot or list_extracted()[-1]
    records = load_extractions(snapshot_id)
    papers = load_snapshot(snapshot_id)

    merged, _, unresolved = canonicalize(records)
    types = {
        entity.name: entity.type
        for record in merged
        for entity in record.extraction.entities
    }
    print(f"{snapshot_id}: {len(papers)} papers, {len(types)} entities")

    driver = _driver()
    driver.verify_connectivity()

    with driver.session() as session:
        if args.reset:
            session.run("MATCH (n) DETACH DELETE n")
            print("graph deleted")

        create_constraints(session)

        relabelled = reconcile_labels(session, types)
        for line in relabelled:
            print(f"  relabelled {line}")

        print(f"papers       +{load_papers(session, papers, snapshot_id)}")
        print(f"entities     +{load_entities(session, types, snapshot_id)}")
        print(f"mentions     +{load_mentions(session, merged, types, snapshot_id)}")
        print(f"relations    +{load_relations(session, merged, types, snapshot_id)}")

    driver.close()
    print(f"\n{len(unresolved)} entity types resolved by precedence only")


if __name__ == "__main__":
    main()

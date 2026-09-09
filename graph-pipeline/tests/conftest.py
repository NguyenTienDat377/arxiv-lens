from pipeline.models import Entity, ExtractionRecord, PaperExtraction, Relation
from pipeline.ontology import EntityType, RelationType

E = EntityType
R = RelationType


def extraction(
    entities: dict[str, EntityType],
    relations: list[tuple[str, RelationType, str]] | None = None,
) -> PaperExtraction:
    return PaperExtraction(
        entities=[Entity(name=n, type=t) for n, t in entities.items()],
        relations=[
            Relation(subject=s, predicate=p, object=o) for s, p, o in relations or []
        ],
    )


def record(
    arxiv_id: str,
    entities: dict[str, EntityType],
    relations: list[tuple[str, RelationType, str]] | None = None,
    version: int = 1,
) -> ExtractionRecord:
    return ExtractionRecord(
        arxiv_id=arxiv_id,
        version=version,
        extraction=extraction(entities, relations),
    )


def edges(extracted: PaperExtraction) -> set[tuple[str, str, str]]:
    return {(r.subject, str(r.predicate), r.object) for r in extracted.relations}

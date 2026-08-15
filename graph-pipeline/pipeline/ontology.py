from dataclasses import dataclass

from enum import StrEnum

class EntityType(StrEnum):
    METHOD = "METHOD"
    TASK = "TASK"
    FORMALISM = "FORMALISM"
    MODEL = "MODEL"
    DATASET = "DATASET"
    PROBLEM = "PROBLEM"

class RelationType(StrEnum):
    EXTENDS = "EXTENDS"
    USES = "USES"
    EVALUATED_ON = "EVALUATED_ON"
    ADDRESSES = "ADDRESSES"
    COMBINES = "COMBINES"
    COMPILES_TO  = "COMPILES_TO "

@dataclass(frozen=True)
class RelationSpec:
    domain: frozenset[EntityType]
    range: frozenset[EntityType]
    transitive: bool = False
    symmetric: bool = False
    irreflexive: bool = False
    asymmetric: bool = False


RELATION_SPECS: dict[RelationType, RelationSpec] = {
    RelationType.EXTENDS: RelationSpec(
        domain=frozenset({EntityType.METHOD, EntityType.FORMALISM}),
        range=frozenset({EntityType.METHOD, EntityType.FORMALISM}),
        transitive=True,
        irreflexive=True,
        asymmetric=True,
    ),
    RelationType.EVALUATED_ON: RelationSpec(
        domain=frozenset({EntityType.METHOD}),
        range=frozenset({EntityType.DATASET}),
    ),
    RelationType.USES: RelationSpec(
        domain=frozenset({EntityType.METHOD}),
        range=frozenset({EntityType.METHOD, EntityType.MODEL, EntityType.FORMALISM}),
        irreflexive=True,
        asymmetric=True
    ),
    RelationType.ADDRESSES: RelationSpec(
        domain=frozenset({EntityType.METHOD}),
        range=frozenset({EntityType.TASK, EntityType.PROBLEM})
    ),
    RelationType.COMBINES: RelationSpec(
        domain=frozenset({EntityType.FORMALISM, EntityType.METHOD}),
        range=frozenset({EntityType.FORMALISM, EntityType.METHOD}),
        symmetric=True,
        irreflexive=True
    ),
    RelationType.COMPILES_TO : RelationSpec(
        domain=frozenset({EntityType.FORMALISM}),
        range=frozenset({EntityType.FORMALISM}),
        transitive=True,
        irreflexive=True,
        asymmetric=True
    )
}

assert set(RELATION_SPECS) == set(RelationType)







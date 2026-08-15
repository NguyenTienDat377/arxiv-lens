import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from .ontology import EntityType, RelationType

_SHORT_ID_RE = re.compile(r"^(?P<arxiv_id>.+?)v(?P<version>\d+)$")


def parse_short_id(short_id: str) -> tuple[str, int]:
    match = _SHORT_ID_RE.match(short_id)
    if match is None:
        return short_id, 1
    return match.group("arxiv_id"), int(match.group("version"))


class Paper(BaseModel):
    arxiv_id: str
    version: int = 1
    title: str
    abstract: str
    authors: list[str]
    categories: list[str]
    published: datetime
    updated: datetime
    doi: str | None = None
    pdf_url: str

class Entity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    type: EntityType


class Relation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: str
    predicate: RelationType
    object: str


class PaperExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entities: list[Entity]
    relations: list[Relation]


class ExtractionRecord(BaseModel):
    arxiv_id: str
    version: int
    extraction: PaperExtraction



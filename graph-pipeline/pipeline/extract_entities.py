import argparse
import functools
import time

import anthropic
from anthropic.types import TextBlockParam
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request

from .models import ExtractionRecord, Paper, PaperExtraction, Relation
from .ontology import RELATION_SPECS, EntityType, RelationType
from .snapshots import (
    latest_snapshot,
    load_extraction_index,
    load_snapshot,
    write_extractions,
)
from dotenv import load_dotenv

load_dotenv()

MODEL = "claude-sonnet-5"
MAX_TOKENS = 8192
EFFORT = "low"

ONTOLOGY_PROMPT = """You extract a knowledge graph from the abstract of a \
neuro-symbolic AI paper.

Entity types:
- METHOD: a technique, system, or architecture. Examples: Logic Tensor \
Networks, SABRE, Monte Carlo Dropout.
- TASK: a problem domain with a definable input and output. Examples: \
clinical trial matching, ransomware detection, inductive logic programming.
- FORMALISM: a logic, calculus, or formal representation. Examples: OWL 2 EL, \
Sentential Decision Diagrams, first-order logic, SMT.
- MODEL: a pretrained component the paper consumes off the shelf rather than \
contributes. Examples: CLIP, ViT, Gemini 3 Pro.
- DATASET: data or a benchmark the paper evaluates on. Examples: MNIST, seL4, \
MITRE ATT&CK.
- PROBLEM: a failure mode or open difficulty, with no definable input and \
output. Examples: hallucination, concept drift, reasoning shortcuts.

If a phrase names something the paper trained or invented, it is a METHOD, not \
a MODEL. If you can describe what a solution to it takes in and puts out, it is \
a TASK, not a PROBLEM.

Relation types:
- EXTENDS: the subject builds on and inherits the structure of the object.
- USES: the subject consumes the object as a component.
- EVALUATED_ON: the subject is measured against the object.
- ADDRESSES: the subject targets the object.
- COMBINES: the subject integrates the object with something else.
- COMPILES_TO: the subject is the representation being translated, not the system 
performing the translation. If a method compiles X into Y, the relation is X COMPILES_TO Y.


Rules:
1. Both ends of every relation must be an entity you listed in `entities`, \
matched by exact name.
2. Extract only relations between two entities. Verbs whose subject is the \
authors or the paper itself are not relations: ignore introduce, propose, \
present, show, demonstrate, argue, and similar.
3. Use the entity name as it appears in the abstract. Do not expand \
abbreviations and do not invent names.
4. An entity name is a short noun phrase of at most four words. If you cannot \
name something that briefly, it is a description rather than an entity: leave \
it out.
5. Extract a METHOD only when the abstract gives it a proper name. Generic \
categories of approach, such as fuzzy logic, ontology embedding, \
neuro-symbolic learning, or multi-objective optimization, are not entities \
unless the paper names a specific system.
6. Software frameworks and libraries, such as PyTorch, TensorFlow, and JAX, \
are not entities.
7. EVALUATED_ON takes a named dataset or benchmark as its object. If the \
object is a task rather than a named dataset, the relation is ADDRESSES.
8. COMPILES_TO takes the representation being translated as its subject, not \
the system performing the translation. If a method compiles X into Y, write \
X COMPILES_TO Y, and relate the method to Y with USES.
9. Extract only what the abstract states. Do not infer relations from \
background knowledge.
10. If the abstract supports no relations, return an empty list.
"""


@functools.cache
def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic()


def _system_blocks() -> list[TextBlockParam]:
    return [
        TextBlockParam(
            type="text",
            text=ONTOLOGY_PROMPT,
            cache_control={"type": "ephemeral"},
        )
    ]


def extract_paper(paper: Paper) -> PaperExtraction | None:
    response = _client().messages.parse(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        output_config={"effort": EFFORT},
        system=_system_blocks(),
        messages=[{"role": "user", "content": paper.abstract}],
        output_format=PaperExtraction,
    )
    if response.stop_reason == "max_tokens":
        raise RuntimeError(
            f"{paper.arxiv_id}: response truncated at {MAX_TOKENS} tokens"
        )
    return response.parsed_output


def validate(extraction: PaperExtraction) -> list[str]:
    types_by_name: dict[str, EntityType] = {e.name: e.type for e in extraction.entities}
    issues: list[str] = []

    for relation in extraction.relations:
        spec = RELATION_SPECS[relation.predicate]
        subject_type = types_by_name.get(relation.subject)
        object_type = types_by_name.get(relation.object)

        if subject_type is None:
            issues.append(f"unknown subject {relation.subject!r}")
        elif subject_type not in spec.domain:
            issues.append(
                f"{relation.predicate} domain: {relation.subject!r} is {subject_type}"
            )

        if object_type is None:
            issues.append(f"unknown object {relation.object!r}")
        elif object_type not in spec.range:
            issues.append(
                f"{relation.predicate} range: {relation.object!r} is {object_type}"
            )

        if spec.irreflexive and relation.subject == relation.object:
            issues.append(f"{relation.predicate} self-loop on {relation.subject!r}")

    return issues


def _candidates(
    subject_type: EntityType, object_type: EntityType
) -> list[RelationType]:
    return [
        predicate
        for predicate, spec in RELATION_SPECS.items()
        if subject_type in spec.domain and object_type in spec.range
    ]


def repair(extraction: PaperExtraction) -> tuple[PaperExtraction, list[str]]:
    types_by_name: dict[str, EntityType] = {e.name: e.type for e in extraction.entities}
    kept: list[Relation] = []
    log: list[str] = []

    for relation in extraction.relations:
        edge = f"{relation.subject!r} --{relation.predicate}--> {relation.object!r}"
        subject_type = types_by_name.get(relation.subject)
        object_type = types_by_name.get(relation.object)

        if subject_type is None or object_type is None:
            log.append(f"dropped {edge}: endpoint missing from entities")
            continue

        spec = RELATION_SPECS[relation.predicate]

        if spec.irreflexive and relation.subject == relation.object:
            log.append(f"dropped {edge}: {relation.predicate} is irreflexive")
            continue

        if subject_type in spec.domain and object_type in spec.range:
            kept.append(relation)
            continue

        candidates = _candidates(subject_type, object_type)
        if len(candidates) == 1:
            kept.append(relation.model_copy(update={"predicate": candidates[0]}))
            log.append(f"rewrote {edge} as {candidates[0]}")
        elif not candidates:
            log.append(f"dropped {edge}: no relation accepts {subject_type} -> {object_type}")
        else:
            # Several predicates fit; guessing would be worse than reporting.
            kept.append(relation)

    return extraction.model_copy(update={"relations": kept}), log


def _request(paper: Paper) -> Request:
    return Request(
        custom_id=paper.arxiv_id,
        params=MessageCreateParamsNonStreaming(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            messages=[{"role": "user", "content": paper.abstract}],
            system=_system_blocks(),
            output_config={
                "effort": EFFORT,
                "format": {
                    "type": "json_schema",
                    "schema": PaperExtraction.model_json_schema(),
                },
            },
        ),
    )


def submit_batch(papers: list[Paper]) -> str:
    batch = _client().messages.batches.create(
        requests=[_request(paper) for paper in papers]
    )
    return batch.id


def await_batch(batch_id: str, poll_seconds: int = 30) -> None:
    while True:
        batch = _client().messages.batches.retrieve(batch_id)
        if batch.processing_status == "ended":
            return
        counts = batch.request_counts
        print(f"{batch.processing_status}: {counts.succeeded} done, {counts.processing} left")
        time.sleep(poll_seconds)


def collect_batch(batch_id: str) -> tuple[dict[str, PaperExtraction], dict[str, str]]:
    extractions: dict[str, PaperExtraction] = {}
    failures: dict[str, str] = {}

    for result in _client().messages.batches.results(batch_id):
        if result.result.type != "succeeded":
            failures[result.custom_id] = result.result.type
            continue

        text = next(
            (b.text for b in result.result.message.content if b.type == "text"), None
        )
        if text is None:
            failures[result.custom_id] = "no text block"
            continue

        try:
            extractions[result.custom_id] = PaperExtraction.model_validate_json(text)
        except ValueError as error:
            failures[result.custom_id] = f"parse error: {error}"

    return extractions, failures


def _report(
    arxiv_id: str,
    title: str,
    extraction: PaperExtraction,
    repairs: list[str] | None = None,
) -> None:
    print(f"\n{arxiv_id} — {title}")
    for entity in extraction.entities:
        print(f"  {entity.type:<10} {entity.name}")
    for relation in extraction.relations:
        print(f"  {relation.subject} --{relation.predicate}--> {relation.object}")
    for entry in repairs or []:
        print(f"  ~ {entry}")
    for issue in validate(extraction):
        print(f"  ! {issue}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract entities and relations from a corpus snapshot."
    )
    parser.add_argument("--snapshot", default=None)
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--batch", action="store_true")
    parser.add_argument("--no-reuse", action="store_true")
    args = parser.parse_args()

    snapshot_id = args.snapshot or latest_snapshot()
    papers = load_snapshot(snapshot_id)
    if args.limit:
        papers = papers[: args.limit]

    if not args.batch:
        for paper in papers:
            extraction = extract_paper(paper)
            if extraction is None:
                print(f"{paper.arxiv_id}: parse failed")
                continue
            extraction, entries = repair(extraction)
            _report(paper.arxiv_id, paper.title, extraction, entries)
        return

    index = {} if args.no_reuse else load_extraction_index()
    reused = [
        ExtractionRecord(
            arxiv_id=paper.arxiv_id,
            version=paper.version,
            extraction=index[(paper.arxiv_id, paper.version)],
        )
        for paper in papers
        if (paper.arxiv_id, paper.version) in index
    ]
    pending = [
        paper for paper in papers if (paper.arxiv_id, paper.version) not in index
    ]
    print(f"{len(reused)} reused, {len(pending)} to extract")

    extractions: dict[str, PaperExtraction] = {}
    failures: dict[str, str] = {}
    if pending:
        batch_id = submit_batch(pending)
        print(f"submitted batch {batch_id}")
        await_batch(batch_id)
        extractions, failures = collect_batch(batch_id)

    # Repair before the record is built, so what lands on disk is already
    # ontology-clean and downstream stages never see a violating edge.
    repairs: dict[str, list[str]] = {}
    for arxiv_id, extraction in extractions.items():
        extractions[arxiv_id], repairs[arxiv_id] = repair(extraction)

    versions = {paper.arxiv_id: paper.version for paper in pending}
    records = reused + [
        ExtractionRecord(
            arxiv_id=arxiv_id, version=versions[arxiv_id], extraction=extraction
        )
        for arxiv_id, extraction in extractions.items()
    ]

    titles = {paper.arxiv_id: paper.title for paper in papers}
    for record in records:
        _report(
            record.arxiv_id,
            titles.get(record.arxiv_id, ""),
            record.extraction,
            repairs.get(record.arxiv_id),
        )

    write_extractions(records, snapshot_id=snapshot_id)
    rewritten = sum(len(entries) for entries in repairs.values())
    print(f"\nwrote {len(records)} extractions, {rewritten} repairs, {len(failures)} failures")
    for arxiv_id, reason in failures.items():
        print(f"  {arxiv_id}: {reason}")


if __name__ == "__main__":
    main()

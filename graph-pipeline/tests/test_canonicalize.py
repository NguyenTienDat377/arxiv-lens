from conftest import E, R, edges, record

from pipeline.canonicalize import (
    _group_key,
    canonical_names,
    canonicalize,
    normalize,
    resolve_types,
)


def test_plurals_and_punctuation_collapse_to_one_key():
    assert _group_key("Logic Tensor Networks") == _group_key("Logic Tensor Network")
    assert _group_key("Vision-Language Model") == _group_key("Vision Language Model")
    assert _group_key("Chain-of-Thought") == _group_key("chain of thought")


def test_an_acronym_is_never_depluralised():
    # CLIPS is a production-rule language, CLIP is an image-text model. Stripping
    # the trailing 's' merged two unrelated entities: a bad merge is worse than a
    # missed merge, because it fabricates edges between them.
    assert normalize("CLIPS") != normalize("CLIP")
    assert _group_key("CLIPS") != _group_key("CLIP")


def test_the_alias_table_merges_an_acronym_with_its_expansion():
    assert _group_key("ASP") == _group_key("Answer Set Programming")
    assert _group_key("Answer Set Programs") == _group_key("Answer Set Programming")
    assert _group_key("LLM") == _group_key("Large Language Models")


def test_the_most_mentioned_surface_form_wins():
    records = [
        record("1", {"Logic Tensor Networks": E.METHOD}),
        record("2", {"Logic Tensor Networks": E.METHOD}),
        record("3", {"Logic Tensor Network": E.METHOD}),
    ]
    names = canonical_names(records)

    assert names["Logic Tensor Network"] == "Logic Tensor Networks"
    assert names["Logic Tensor Networks"] == "Logic Tensor Networks"


def test_a_tie_is_broken_by_the_shorter_name_then_alphabetically():
    records = [
        record("1", {"Bravo Method": E.METHOD}),
        record("2", {"Bravo Methods": E.METHOD}),
    ]
    names = canonical_names(records)

    assert set(names.values()) == {"Bravo Method"}


def test_an_entity_seen_with_one_type_keeps_it():
    records = [record("1", {"MNIST": E.DATASET})]
    types, unresolved = resolve_types(records, canonical_names(records))

    assert types["MNIST"] == E.DATASET
    assert unresolved == []


def test_a_disputed_type_is_settled_by_which_edge_slots_it_fills():
    # Two papers disagree; the DATASET reading is the only one that fits the
    # range of EVALUATED_ON, so the graph's own structure casts the vote.
    records = [
        record("1", {"seL4": E.DATASET, "A": E.METHOD}, [("A", R.EVALUATED_ON, "seL4")]),
        record("2", {"seL4": E.METHOD}),
        record("3", {"seL4": E.METHOD}),
    ]
    types, _ = resolve_types(records, canonical_names(records))

    assert types["seL4"] == E.DATASET


def test_a_type_settled_only_by_precedence_is_reported_for_review():
    # No edges to fit and a tied vote, so precedence picks and says so: silent
    # coin-flips are the ones that quietly corrupt the graph.
    records = [
        record("1", {"Ambiguous": E.METHOD}),
        record("2", {"Ambiguous": E.DATASET}),
    ]
    types, unresolved = resolve_types(records, canonical_names(records))

    assert types["Ambiguous"] == E.DATASET  # DATASET outranks METHOD
    assert len(unresolved) == 1
    assert "Ambiguous" in unresolved[0]


def test_merging_rewrites_both_ends_of_a_relation():
    records = [
        record(
            "1",
            {"sLTN": E.METHOD, "Logic Tensor Networks": E.METHOD},
            [("sLTN", R.EXTENDS, "Logic Tensor Networks")],
        ),
        record(
            "2",
            {"sLTN": E.METHOD, "Logic Tensor Network": E.METHOD},
            [("sLTN", R.EXTENDS, "Logic Tensor Network")],
        ),
        record("3", {"Logic Tensor Networks": E.METHOD}),
    ]
    merged, names, _, _ = canonicalize(records)

    assert edges(merged[1].extraction) == {
        ("sLTN", "EXTENDS", "Logic Tensor Networks")
    }
    assert names["Logic Tensor Network"] == "Logic Tensor Networks"


def test_duplicate_relations_within_a_paper_collapse_after_merging():
    merged, _, _, _ = canonicalize(
        [
            record(
                "1",
                {
                    "A": E.METHOD,
                    "Logic Tensor Networks": E.METHOD,
                    "Logic Tensor Network": E.METHOD,
                },
                [
                    ("A", R.EXTENDS, "Logic Tensor Networks"),
                    ("A", R.EXTENDS, "Logic Tensor Network"),
                ],
            )
        ]
    )

    assert len(merged[0].extraction.relations) == 1


def test_global_type_resolution_can_invalidate_a_locally_legal_edge():
    # Paper 2's edge is legal while seL4 is a METHOD. Once the corpus resolves
    # seL4 to DATASET, USES no longer accepts it, so repair has to run again
    # after the merge rather than only per paper.
    records = [
        record("1", {"seL4": E.DATASET, "X": E.METHOD}, [("X", R.EVALUATED_ON, "seL4")]),
        record("2", {"seL4": E.METHOD, "Y": E.METHOD}, [("Y", R.USES, "seL4")]),
    ]
    merged, _, _, repairs = canonicalize(records)

    assert edges(merged[1].extraction) == {("Y", "EVALUATED_ON", "seL4")}
    assert any("rewrote" in line for line in repairs)

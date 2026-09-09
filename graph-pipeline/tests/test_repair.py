from conftest import E, R, edges, extraction

from pipeline.extract_entities import _candidates, repair, validate


def test_a_legal_edge_is_left_alone():
    before = extraction(
        {"sLTN": E.METHOD, "Logic Tensor Networks": E.METHOD},
        [("sLTN", R.EXTENDS, "Logic Tensor Networks")],
    )
    after, log = repair(before)

    assert edges(after) == edges(before)
    assert log == []


def test_exactly_one_candidate_predicate_rewrites_the_edge():
    # METHOD -> DATASET is accepted by EVALUATED_ON and nothing else, so the
    # intended relation is recoverable without guessing. This is the corpus-wide
    # failure of prompt rule 7 ("demonstrated through case studies" reads as
    # EVALUATED_ON) being fixed mechanically instead of by another prompt rule.
    assert _candidates(E.METHOD, E.DATASET) == [R.EVALUATED_ON]

    after, log = repair(
        extraction(
            {"AutoQuREO": E.METHOD, "MNIST": E.DATASET},
            [("AutoQuREO", R.ADDRESSES, "MNIST")],
        )
    )

    assert edges(after) == {("AutoQuREO", "EVALUATED_ON", "MNIST")}
    assert log == ["rewrote 'AutoQuREO' --ADDRESSES--> 'MNIST' as EVALUATED_ON"]


def test_no_candidate_predicate_drops_the_edge():
    # No relation accepts a DATASET as its subject, so there is nothing to
    # rewrite to and the claim cannot be salvaged.
    assert _candidates(E.DATASET, E.METHOD) == []

    after, log = repair(
        extraction(
            {"MNIST": E.DATASET, "AutoQuREO": E.METHOD},
            [("MNIST", R.USES, "AutoQuREO")],
        )
    )

    assert edges(after) == set()
    assert log == [
        "dropped 'MNIST' --USES--> 'AutoQuREO': no relation accepts DATASET -> METHOD"
    ]


def test_several_candidate_predicates_keeps_the_edge_untouched():
    # METHOD -> METHOD fits EXTENDS, USES and COMBINES. Picking one would invent
    # a claim the abstract never made, so the edge survives unchanged and stays
    # visible to validate().
    assert len(_candidates(E.METHOD, E.METHOD)) > 1

    before = extraction(
        {"A": E.METHOD, "B": E.METHOD},
        [("A", R.COMPILES_TO, "B")],
    )
    after, log = repair(before)

    assert edges(after) == edges(before)
    assert log == ["kept 'A' --COMPILES_TO--> 'B': 3 predicates fit METHOD -> METHOD"]
    assert validate(after) != []


def test_an_endpoint_missing_from_entities_drops_the_edge():
    after, log = repair(
        extraction({"A": E.METHOD}, [("A", R.USES, "Ghost")]),
    )

    assert edges(after) == set()
    assert log == ["dropped 'A' --USES--> 'Ghost': endpoint missing from entities"]


def test_an_irreflexive_self_loop_is_dropped():
    after, log = repair(
        extraction({"A": E.METHOD}, [("A", R.EXTENDS, "A")]),
    )

    assert edges(after) == set()
    assert log == ["dropped 'A' --EXTENDS--> 'A': EXTENDS is irreflexive"]


def test_repair_is_idempotent():
    # canonicalize() runs repair a second time after merging, so a repaired
    # extraction has to be a fixed point or the two passes would disagree.
    once, first_log = repair(
        extraction(
            {"A": E.METHOD, "MNIST": E.DATASET, "Ghost_holder": E.TASK},
            [
                ("A", R.ADDRESSES, "MNIST"),
                ("A", R.EXTENDS, "A"),
                ("A", R.ADDRESSES, "Ghost_holder"),
            ],
        )
    )
    twice, second_log = repair(once)

    assert edges(twice) == edges(once)
    assert first_log != []
    assert second_log == []

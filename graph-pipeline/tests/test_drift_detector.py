from conftest import E, R, record

from pipeline.drift_detector import (
    MAX_CHANGED_SHARE,
    MAX_LOST_EDGE_SHARE,
    compare,
)


def paper(arxiv_id: str, edge_count: int, version: int = 1):
    entities = {"A": E.METHOD} | {f"D{i}": E.DATASET for i in range(edge_count)}
    relations = [("A", R.EVALUATED_ON, f"D{i}") for i in range(edge_count)]
    return record(arxiv_id, entities, relations, version=version)


def corpus(size: int, edge_count: int = 3):
    return [paper(f"{i}", edge_count) for i in range(size)]


def test_an_unchanged_snapshot_shows_no_drift():
    before = corpus(10)
    report = compare(before, corpus(10))

    assert report.shared == 10
    assert report.papers_changed == []
    assert report.changed_share == 0.0
    assert report.failures() == []


def test_added_papers_are_not_churn():
    # Growing the corpus is the normal case and must never trip the gate.
    report = compare(corpus(10), corpus(20))

    assert len(report.papers_added) == 10
    assert report.shared == 10
    assert report.failures() == []


def test_enough_re_extracted_papers_fails_the_gate():
    current = corpus(10)
    current[0] = record("0", {"A": E.METHOD, "D0": E.DATASET}, [])
    current[1] = record("1", {"A": E.METHOD, "D0": E.DATASET}, [])

    report = compare(corpus(10), current)

    assert report.changed_share == 0.2 > MAX_CHANGED_SHARE
    assert any("extracted differently" in reason for reason in report.failures())


def test_a_version_bump_is_a_revision_not_churn():
    # A genuinely revised paper should extract differently, so it is excluded
    # from the shared set instead of being counted as unexplained churn.
    current = corpus(10)
    current[0] = paper("0", edge_count=0, version=2)

    report = compare(corpus(10), current)

    assert report.shared == 9
    assert report.papers_changed == []
    assert report.failures() == []


def test_lost_edges_are_measured_against_the_shared_edges_not_the_churn():
    # The first version of this metric divided losses by the number of changed
    # papers, so three lost edges in three papers read as 100% and any real
    # snapshot failed the gate.
    current = corpus(10, edge_count=3)
    current[0] = paper("0", edge_count=0)

    report = compare(corpus(10, edge_count=3), current)

    assert report.edges_shared_before == 30
    assert len(report.edges_lost) == 3
    assert report.lost_edge_share == 0.1


def test_enough_lost_edges_fails_the_gate():
    current = corpus(10, edge_count=3)
    current[0] = paper("0", edge_count=0)
    current[1] = paper("1", edge_count=0)

    report = compare(corpus(10, edge_count=3), current)

    assert report.lost_edge_share == 0.2 > MAX_LOST_EDGE_SHARE
    assert any("edges lost" in reason for reason in report.failures())


def test_a_growing_corpus_at_a_steady_violation_rate_passes():
    # The first version counted violations instead of rating them, so simply
    # extracting more papers looked like the ontology was degrading.
    def illegal(arxiv_id: str):
        return record(
            arxiv_id, {"M": E.METHOD, "D": E.DATASET}, [("D", R.USES, "M")]
        )

    before = [*corpus(9), illegal("bad0")]
    after = [*corpus(27), illegal("bad0"), illegal("bad1"), illegal("bad2")]

    report = compare(before, after)

    assert report.violations_current > report.violations_previous
    assert report.violation_rate_increase <= 0
    assert report.failures() == []

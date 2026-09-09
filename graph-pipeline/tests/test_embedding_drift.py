import numpy as np

from pipeline.embedding_drift import MIN_PAPERS, compare


def normalised(rows: int, dims: int = 32, shift: float = 0.0, seed: int = 0):
    # Unit-normalised like the real MiniLM vectors, so cosine distance means
    # what it means in production.
    rng = np.random.default_rng(seed)
    vectors = rng.normal(0.0, 1.0, (rows, dims)) + shift
    return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)


def test_the_same_distribution_is_not_drift():
    report = compare(normalised(200, seed=1), normalised(200, seed=2))

    assert not report.classifier_drift
    assert not report.centroid_drift
    assert not report.drifted


def test_a_shifted_distribution_is_drift():
    # "No drift found" is indistinguishable from a broken detector until it is
    # made to find something.
    report = compare(normalised(200, seed=1), normalised(200, shift=0.8, seed=2))

    assert report.classifier_auc > 0.9
    assert report.classifier_drift
    assert report.centroid_drift
    assert report.drifted


def test_comparing_a_set_with_itself_gives_zero_distance():
    # Regression: ModelDriftMethod appends a `target` column to the frames it is
    # given, in place. Sharing frames between the two methods made the label a
    # 385th dimension and put the centroids ~0.45 apart on identical corpora.
    vectors = normalised(200, seed=3)
    report = compare(vectors, vectors)

    assert report.centroid_distance == 0.0
    assert not report.drifted


def test_too_few_papers_is_reported_rather_than_answered():
    report = compare(normalised(200), normalised(MIN_PAPERS - 1))

    assert report.skipped is not None
    assert not report.drifted

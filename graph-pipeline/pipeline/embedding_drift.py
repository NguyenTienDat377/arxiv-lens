import argparse
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .embeddings import embed_snapshot, vectors_for
from .models import Paper
from .snapshots import list_extracted, load_snapshot

REPORTS = Path("data/drift")

# A domain classifier that cannot separate the two sets scores 0.5. Evidently's
# default calls anything above 0.55 drift; the corpus is small enough that a
# weak classifier is easy to fit by chance, so the bar is raised.
CLASSIFIER_AUC_THRESHOLD = 0.60

# How far apart two centroids have to be, expressed as a percentile of the
# distances seen when the same papers are shuffled between the two sides. The
# corpus answers "how far is far" instead of a number picked in advance.
CENTROID_PERCENTILE = 95
PERMUTATIONS = 500
PERMUTATION_SEED = 0

MIN_PAPERS = 30


@dataclass
class EmbeddingDriftReport:
    reference: str
    current: str
    reference_size: int
    current_size: int
    classifier_auc: float = 0.0
    classifier_drift: bool = False
    centroid_distance: float = 0.0
    centroid_drift: bool = False
    html: Path | None = None
    skipped: str | None = None

    @property
    def drifted(self) -> bool:
        return self.classifier_drift or self.centroid_drift

    def lines(self) -> list[str]:
        if self.skipped:
            return [f"skipped: {self.skipped}"]
        return [
            f"reference        {self.reference} ({self.reference_size} papers)",
            f"current          {self.current} ({self.current_size} papers)",
            f"classifier AUC   {self.classifier_auc:.3f} "
            f"(> {CLASSIFIER_AUC_THRESHOLD} means separable)"
            f"{'  DRIFT' if self.classifier_drift else ''}",
            f"centroid cosine  {self.centroid_distance:.4f}"
            f"{'  DRIFT' if self.centroid_drift else ''}",
        ]


def _centroid_drift(reference_vectors, current_vectors) -> tuple[float, bool]:
    """Cosine distance between centroids, judged against a permutation null.

    Evidently ships a bootstrap for this, but it draws both halves of its null
    from the reference set with replacement, so the two samples overlap and land
    closer together than two independent sets would. Measured over 40 pairs of
    identical distributions it called drift 22 times; this permutation test
    called it 3 times, which is the 5% it advertises.
    """
    import numpy as np
    from scipy.spatial.distance import cosine

    observed = float(cosine(reference_vectors.mean(0), current_vectors.mean(0)))

    pooled = np.vstack([reference_vectors, current_vectors])
    cut = len(reference_vectors)
    rng = np.random.default_rng(PERMUTATION_SEED)
    null = []
    for _ in range(PERMUTATIONS):
        shuffled = rng.permutation(pooled)
        null.append(cosine(shuffled[:cut].mean(0), shuffled[cut:].mean(0)))

    return observed, observed > float(np.percentile(null, CENTROID_PERCENTILE))


def _frame(vectors):
    import pandas as pd

    return pd.DataFrame(vectors, columns=[f"e{i}" for i in range(vectors.shape[1])])


def compare(
    reference_vectors,
    current_vectors,
    reference: str = "reference",
    current: str = "current",
    html: Path | None = None,
) -> EmbeddingDriftReport:
    """Two embedding matrices in, one verdict out.

    Takes arrays rather than snapshot ids so the statistics can be tested
    against distributions with a known answer.
    """
    from evidently.metrics.embeddings import ModelDriftMethod

    report = EmbeddingDriftReport(
        reference=reference,
        current=current,
        reference_size=len(reference_vectors),
        current_size=len(current_vectors),
    )
    if min(len(reference_vectors), len(current_vectors)) < MIN_PAPERS:
        report.skipped = (
            f"fewer than {MIN_PAPERS} papers on one side "
            f"({len(reference_vectors)} vs {len(current_vectors)})"
        )
        return report

    reference_frame, current_frame = _frame(reference_vectors), _frame(current_vectors)

    # Copies, because ModelDriftMethod appends a `target` column to the frames it
    # is handed, in place. Anything reading them afterwards sees the label as an
    # extra embedding dimension.
    auc, classifier_drift, _ = ModelDriftMethod(
        threshold=CLASSIFIER_AUC_THRESHOLD
    )(current_frame.copy(), reference_frame.copy())
    distance, centroid_drift = _centroid_drift(reference_vectors, current_vectors)

    report.classifier_auc = float(auc)
    report.classifier_drift = bool(classifier_drift)
    report.centroid_distance = float(distance)
    report.centroid_drift = bool(centroid_drift)

    if html is not None:
        report.html = _render(reference_frame, current_frame, html)
    return report


def _render(reference_frame, current_frame, path: Path) -> Path:
    from evidently import DataDefinition, Dataset, Report
    from evidently.metrics import EmbeddingsDrift
    from evidently.metrics.embeddings import DistanceDriftMethod, ModelDriftMethod

    definition = DataDefinition(embeddings={"abstract": list(reference_frame.columns)})
    run = Report(
        [
            EmbeddingsDrift(
                embeddings_name="abstract",
                drift_method=ModelDriftMethod(threshold=CLASSIFIER_AUC_THRESHOLD),
            ),
            EmbeddingsDrift(
                embeddings_name="abstract",
                drift_method=DistanceDriftMethod(dist="cosine"),
            ),
        ]
    ).run(
        Dataset.from_pandas(current_frame, data_definition=definition),
        Dataset.from_pandas(reference_frame, data_definition=definition),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    run.save_html(str(path))
    return path


def split_by_date(
    papers: list[Paper], on: datetime | None = None
) -> tuple[list[Paper], list[Paper], datetime]:
    """Older half against newer half, split at the median publication date."""
    ordered = sorted(papers, key=lambda p: p.published)
    cut = on or ordered[len(ordered) // 2].published
    older = [p for p in ordered if p.published < cut]
    newer = [p for p in ordered if p.published >= cut]
    return older, newer, cut


def compare_snapshots(
    previous_id: str, current_id: str, html: Path | None = None
) -> EmbeddingDriftReport:
    """Papers new in `current_id` against everything already in `previous_id`.

    The delta, not the whole snapshot: snapshots are cumulative, so comparing
    them whole dilutes any signal with the papers both of them contain.
    """
    previous = [p.arxiv_id for p in load_snapshot(previous_id)]
    added = [p.arxiv_id for p in load_snapshot(current_id) if p.arxiv_id not in set(previous)]

    return compare(
        vectors_for(previous_id, previous),
        vectors_for(current_id, added),
        reference=previous_id,
        current=f"{current_id} (new papers)",
        html=html,
    )


def compare_within(
    snapshot_id: str, on: datetime | None = None, html: Path | None = None
) -> EmbeddingDriftReport:
    """Older papers against newer papers inside one snapshot."""
    older, newer, cut = split_by_date(load_snapshot(snapshot_id), on)
    return compare(
        vectors_for(snapshot_id, [p.arxiv_id for p in older]),
        vectors_for(snapshot_id, [p.arxiv_id for p in newer]),
        reference=f"{snapshot_id} before {cut.date()}",
        current=f"{snapshot_id} from {cut.date()}",
        html=html,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Detect semantic drift between two sets of abstracts."
    )
    parser.add_argument("--snapshot", default=None)
    parser.add_argument("--previous", default=None, help="compare two snapshots instead")
    parser.add_argument("--split-date", default=None, help="YYYY-MM-DD")
    parser.add_argument("--html", action="store_true", help="write an Evidently report")
    parser.add_argument(
        "--fail-on-drift",
        action="store_true",
        help="exit 1 when drift is detected (off by default: see the README)",
    )
    args = parser.parse_args()

    snapshot_id = args.snapshot or list_extracted()[-1]
    embed_snapshot(snapshot_id)  # warm the cache before the timing-sensitive part

    path = REPORTS / f"embedding-{snapshot_id}.html" if args.html else None

    if args.previous:
        report = compare_snapshots(args.previous, snapshot_id, path)
    else:
        cut = (
            datetime.fromisoformat(args.split_date).astimezone()
            if args.split_date
            else None
        )
        report = compare_within(snapshot_id, cut, path)

    for line in report.lines():
        print(line)
    if report.html:
        print(f"report           {report.html}")

    if report.skipped:
        return
    if report.drifted:
        print(
            "\nThe corpus has moved. Entity linking's 0.80 threshold and the "
            "golden eval were calibrated on the older distribution; re-check both."
        )
        if args.fail_on_drift:
            sys.exit(1)
    else:
        print("\nno semantic drift — the corpus is still about the same things")


if __name__ == "__main__":
    main()

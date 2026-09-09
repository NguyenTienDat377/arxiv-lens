import os
import subprocess

from .drift_detector import (
    MAX_CHANGED_SHARE,
    MAX_LOST_EDGE_SHARE,
    MAX_VIOLATION_RATE_INCREASE,
)
from .extract_entities import MODEL, validate
from .models import ExtractionRecord

EXPERIMENT = "arxiv-lens-graph"


def _git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def log_build(
    snapshot_id: str,
    counts: dict[str, int],
    records: list[ExtractionRecord],
    unresolved: list[str],
    repairs: list[str],
) -> str | None:
    """Record one graph build as an MLflow run. Returns the run id, or None.

    The graph is already committed by the time this runs, so a tracking failure
    must not fail the build — the same rule the Kafka producer follows.
    """
    try:
        import mlflow

        # SQLite, not the './mlruns' file store: MLflow 3 put the file backend
        # into maintenance mode and refuses to write to it. A local database
        # needs no server, and the variable points at a real one when there is.
        mlflow.set_tracking_uri(os.getenv("MLFLOW_TRACKING_URI", "sqlite:///mlflow.db"))
        mlflow.set_experiment(EXPERIMENT)

        relations = sum(len(r.extraction.relations) for r in records)
        violations = sum(len(validate(r.extraction)) for r in records)

        with mlflow.start_run(run_name=snapshot_id) as run:
            # Params are inputs: what was chosen. They are what you filter and
            # group by, so a number that could have been set differently belongs
            # here even when it looks like a result.
            mlflow.log_params(
                {
                    "snapshot_id": snapshot_id,
                    "extraction_model": MODEL,
                    "papers_in_snapshot": len(records),
                    "max_changed_share": MAX_CHANGED_SHARE,
                    "max_lost_edge_share": MAX_LOST_EDGE_SHARE,
                    "max_violation_rate_increase": MAX_VIOLATION_RATE_INCREASE,
                }
            )

            # Metrics are outputs: what was measured. Numeric only, and charted
            # across runs, which is the whole point of recording them.
            mlflow.log_metrics(
                {
                    "papers_created": counts.get("papers", 0),
                    "entities_created": counts.get("entities", 0),
                    "mentions_created": counts.get("mentions", 0),
                    "relations_created": counts.get("relations", 0),
                    "entities_total": len(
                        {e.name for r in records for e in r.extraction.entities}
                    ),
                    "relations_total": relations,
                    "repairs": len(repairs),
                    "unresolved_types": len(unresolved),
                    # A rate, not a count: a bigger corpus has more violations
                    # without being any worse. The same mistake the drift gate
                    # made in its first version.
                    "violation_rate": violations / relations if relations else 0.0,
                }
            )

            # Tags are for finding a run again. The commit is the one that turns
            # "the graph changed" into "the graph changed at this revision".
            mlflow.set_tags(
                {
                    "git_commit": _git_commit(),
                    "snapshot_id": snapshot_id,
                }
            )

            return run.info.run_id
    except Exception as error:
        print(f"  mlflow: not logged ({type(error).__name__}: {error})")
        return None
